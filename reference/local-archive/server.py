#!/usr/bin/env python3
"""Bally Heritage: private local archive, durable CMS and provenance-preserving ingestion.
No source-system writes, external requests or dependencies. Bind only to loopback.
"""
import search_engine
import base64,csv,datetime,hashlib,http.server,io,json,mimetypes,pathlib,re,sqlite3,sys,urllib.parse,zipfile,xml.etree.ElementTree as ET
ROOT=pathlib.Path(__file__).resolve().parent
DB=ROOT/'data/archive.sqlite3'
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def byte_range(value,size):
 m=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
 if not m or not any(m.groups()) or size<=0:raise ValueError('Unsupported byte range')
 left,right=m.groups()
 if not left:
  if int(right)<=0:raise ValueError('Invalid suffix range')
  return max(0,size-int(right)),size-1
 start=int(left);end=min(int(right),size-1) if right else size-1
 if start>=size or end<start:raise ValueError('Unsatisfiable range')
 return start,end
def media_kind(filename):
 ext=pathlib.Path(filename).suffix.lower()
 groups={'Images':{'.tif','.tiff','.jpg','.jpeg','.png','.gif','.webp','.bmp','.psd','.eps','.ai'},'Video':{'.mp4','.m4v','.mov','.avi','.mpeg','.mpg','.webm'},'Documents':{'.pdf','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.txt','.csv','.xml'},'Packages':{'.zip','.rar','.7z'}}
 return next((name for name,extensions in groups.items() if ext in extensions),'Other')
def media_results(rows,q):
 text=q.get('q',[''])[0].casefold().strip();kind=q.get('kind',[''])[0]
 if kind and kind not in ('Images','Video','Documents','Packages','Other'):raise ValueError('Unknown file type')
 rows=[dict(r,kind=media_kind(r['filename'])) for r in rows]
 if text:rows=[r for r in rows if text in ' '.join([r['filename'],r['guid']]+r.get('record_ids',[])+r.get('inventory_numbers',[])).casefold()]
 if kind:rows=[r for r in rows if r['kind']==kind]
 rows.sort(key=lambda r:(r['filename'].casefold(),r['guid']))
 limit=max(1,min(100,int(q.get('limit',['50'])[0])));offset=max(0,int(q.get('offset',['0'])[0]))
 return {'rows':rows[offset:offset+limit],'total':len(rows),'offset':offset,'limit':limit}
def connect():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
def init():
 with connect() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS shooting_sessions(guid TEXT PRIMARY KEY,asset_id TEXT,inventory_id TEXT,object_code TEXT,body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,body TEXT NOT NULL,updated_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT,action TEXT,record_id TEXT,body TEXT); CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY,at TEXT,source TEXT,sha256 TEXT,created INTEGER,updated INTEGER,skipped INTEGER); CREATE TABLE IF NOT EXISTS originals(sha256 TEXT PRIMARY KEY,path TEXT,bytes INTEGER,filename TEXT,source_guid TEXT); CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(id UNINDEXED,content); CREATE TABLE IF NOT EXISTS inventories(id TEXT PRIMARY KEY,object_code TEXT,body TEXT NOT NULL,source_sha256 TEXT NOT NULL); CREATE TABLE IF NOT EXISTS media_assets(guid TEXT PRIMARY KEY,body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS collections(name TEXT PRIMARY KEY,body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS collection_members(collection_name TEXT,object_code TEXT,sort_order INTEGER,source_guid TEXT,source_asset_id TEXT,source_url TEXT,download_guid TEXT,PRIMARY KEY(collection_name,object_code));''')
  if not c.execute('SELECT count(*) FROM records').fetchone()[0]:
   for r in json.loads((ROOT/'data/pilot.json').read_text()):save(c,r,'pilot')
  c.commit()
def save(c,r,action):
 old=c.execute('SELECT body FROM records WHERE id=?',(r['id'],)).fetchone()
 c.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(r['id'],json.dumps(r,ensure_ascii=False),now()))
 c.execute('DELETE FROM search WHERE id=?',(r['id'],));c.execute('INSERT INTO search VALUES(?,?)',(r['id'],json.dumps(r,ensure_ascii=False)))
 c.execute('INSERT INTO audit(at,action,record_id,body) VALUES(?,?,?,?)',(now(),action,r['id'],json.dumps({'before':json.loads(old[0]) if old else None,'after':r},ensure_ascii=False)))
def all_records(c):return [json.loads(x[0]) for x in c.execute('SELECT body FROM records ORDER BY id')]
def xlsx_rows(data):
 ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'};z=zipfile.ZipFile(io.BytesIO(data))
 if sum(x.file_size for x in z.infolist())>250_000_000:raise ValueError('Workbook expands beyond 250 MB')
 strings=[]
 if 'xl/sharedStrings.xml' in z.namelist():strings=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',ns)]
 rows=[]
 for row in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).findall('.//s:row',ns):
  cells={}
  for cell in row.findall('s:c',ns):
   col=re.match('[A-Z]+',cell.attrib['r'])[0];v=cell.find('s:v',ns);val=v.text if v is not None else ''.join(cell.find('s:is',ns).itertext()) if cell.find('s:is',ns) is not None else ''
   cells[col]=strings[int(val)] if cell.attrib.get('t')=='s' else val
  rows.append(cells)
 if len(rows)<2:raise ValueError('Export contains headers only: no records. Source export must be repaired or a different official export supplied.')
 header=rows[0];return [{label:row.get(col,'') for col,label in header.items() if label} for row in rows[1:]]
def normalize(row,source,source_format=None):
 if row.get('id') and row.get('metadata') is not None:
  r=dict(row)
 else:
  def get(*keys):return next((row[k] for k in keys if row.get(k) not in (None,'')), '')
  code=str(get('Object Code','ObjectCode','code','id')).strip()
  if not code:raise ValueError('Missing Object Code / id')
  def year(v):
   v=str(v or '')
   if source_format=='xlsx' and re.fullmatch(r'-?\d+(\.\d+)?',v):
    try:return (datetime.datetime(1899,12,30)+datetime.timedelta(days=float(v))).year
    except (ValueError,OverflowError):return None
   if re.fullmatch(r'(?:1[0-9]{3}|20[0-9]{2}|21[0-9]{2})',v):return int(v)
   if re.fullmatch(r'\d{4,6}(\.\d+)?',v):
    try:return (datetime.datetime(1899,12,30)+datetime.timedelta(days=float(v))).year
    except (ValueError,OverflowError):pass
   m=re.search(r'(?:18|19|20)\d{2}',v)
   return int(m[0]) if m else None
  tags=get('Tags','tags');tags=tags if isinstance(tags,list) else [s.strip() for s in str(tags).split(';') if s.strip()]
  r={'id':code,'title':str(get('Model Name','Description','Name') or code),'category':str(get('CATEGORY','Category') or 'Unclassified').title(),'year_start':year(get('Start Date','year_start')),'year_end':year(get('End Date','year_end')),'tags':tags,'metadata':dict(row),'raw':dict(row),'source_format':source_format,'source_url':str(get('Source URL','source_url') or source),'source_guid':str(get('GUID','Guid','source_guid')),'source_asset_id':str(get('Asset ID','source_asset_id')),'source_system':'CHALCO Brand Life Suite','inventory_numbers':[s.strip() for s in str(get('Inventory No.','Inventory No','InventoryNo')).split(';') if s.strip()],'linked_items':[],'collections':[],'images':[],'rights':str(get('Copyright','COPYRIGHT') or 'Owner-authorized Bally archive'),'rights_status':'Owner-authorized migration','notes':'','captured_at':now(),'migration_status':'Metadata imported'}
 if not isinstance(r['id'],str) or not re.fullmatch(r'[\w .:-]{1,160}',r['id']):raise ValueError('Invalid record ID')
 if not str(r.get('source_url','')).startswith(('https://ballyheritage.com/','https://cdn.ballyheritage.com/')):raise ValueError('Record requires a Bally source URL')
 for key in ('tags','inventory_numbers','linked_items','collections','images'):
  if not isinstance(r.get(key,[]),list):raise ValueError(key+' must be an array')
 for key in ('year_start','year_end'):
  if r.get(key) is not None and (not isinstance(r[key],int) or not 1000<=r[key]<=2200):raise ValueError('Invalid '+key)
 return r
def prepare(payload,max_bytes=32_000_000):
 name=payload.get('filename','import.json');source=payload.get('source_url','');data=base64.b64decode(payload['data'],validate=True)
 if len(data)>max_bytes:raise ValueError('Import exceeds permitted file size')
 if name.lower().endswith('.xlsx'):rows=xlsx_rows(data)
 elif name.lower().endswith('.csv'):rows=list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
 else:
  rows=json.loads(data);rows=rows.get('records',[]) if isinstance(rows,dict) else rows
 if not isinstance(rows,list) or not rows:raise ValueError('No records in import')
 if len(rows)>100000:raise ValueError('Import limit is 100,000 records')
 normalized=[];errors=[];seen=set()
 for i,row in enumerate(rows):
  try:
   r=normalize(row,source,'xlsx' if name.lower().endswith('.xlsx') else None)
   if r['id'] in seen:raise ValueError('Duplicate ID within file: '+r['id'])
   seen.add(r['id']);normalized.append(r)
  except (ValueError,TypeError,KeyError) as e:errors.append({'row':i+2,'error':str(e)})
 return data,normalized,errors
def import_records(payload,commit=False,max_bytes=32_000_000):
 data,rows,errors=prepare(payload,max_bytes)
 with connect() as c:
  existing={r['id']:r for r in all_records(c)};created=updated=skipped=0
  for r in rows:
   old=existing.get(r['id'])
   if old:
    if old.get('raw')==r.get('raw'):skipped+=1;continue
    # Retain curated fields and known relationships when updating exported metadata.
    r['metadata']={**old.get('metadata',{}),**{k:v for k,v in r.get('metadata',{}).items() if v not in ('',None)}}
    r['source_snapshots']=old.get('source_snapshots',[])+[{'at':old.get('captured_at'),'source_url':old.get('source_url'),'raw':old.get('raw')}]
    for key in ('notes','rights_status','rights','images','originals','linked_items','collections','inventory_numbers','source_guid','source_asset_id'):
     if old.get(key):r[key]=old[key]
    updated+=1
   else:created+=1
   if commit and not errors:save(c,r,'import')
  if commit and not errors:
   sha=hashlib.sha256(data).hexdigest();(ROOT/'data/imports').mkdir(exist_ok=True);(ROOT/'data/imports'/sha).write_bytes(data)
   c.execute('INSERT INTO imports(at,source,sha256,created,updated,skipped) VALUES(?,?,?,?,?,?)',(now(),payload.get('source_url',''),sha,created,updated,skipped));c.commit()
  return {'created':created,'updated':updated,'skipped':skipped,'errors':errors,'total':len(rows),'committed':commit and not errors,'sha256':hashlib.sha256(data).hexdigest()}
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def respond(self,data,status=200,ctype='application/json'):
  body=json.dumps(data,ensure_ascii=False).encode() if ctype=='application/json' else data
  self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.end_headers()
  try:self.wfile.write(body)
  except (BrokenPipeError,ConnectionResetError):pass
 def guard(self,write=False):
  host=self.headers.get('Host','')
  if host not in ('127.0.0.1:8765','localhost:8765'):self.respond({'error':'Local host required'},403);return False
  if write and self.headers.get('Origin') not in ('http://127.0.0.1:8765','http://localhost:8765'):self.respond({'error':'Same-origin request required'},403);return False
  return True
 def do_GET(self):
  if not self.guard():return
  u=urllib.parse.urlparse(self.path);q=urllib.parse.parse_qs(u.query)
  with connect() as c:
   if u.path=='/api/live-migration':return self.respond(json.loads((ROOT/'data/live-migration.json').read_text()) if (ROOT/'data/live-migration.json').exists() else {'cutover_ready':False})
   if u.path=='/api/jobs':return self.respond(json.loads((ROOT/'data/migration-jobs.json').read_text()))
   if u.path=='/api/parity':return self.respond(json.loads((ROOT/'data/functionality-parity.json').read_text()))
   if u.path=='/api/inventory':return self.respond(json.loads((ROOT/'data/inventory.json').read_text()))
   if u.path=='/api/facets':return self.respond(search_engine.facets(all_records(c)))
   if u.path=='/api/records':
    try:rows=search_engine.filter_records(all_records(c),q)
    except ValueError as e:return self.respond({'error':str(e)},400)
    if q.get('summary'):
     rows=[{k:r.get(k) for k in ('id','title','category','year_start','year_end')}|{'metadata':{'Object Gender':r.get('metadata',{}).get('Object Gender')}} for r in rows]
    if q.get('limit'):
     try:limit=max(1,min(100,int(q['limit'][0])));offset=max(0,int(q.get('offset',['0'])[0]))
     except ValueError:return self.respond({'error':'Invalid pagination'},400)
     return self.respond({'rows':rows[offset:offset+limit],'total':len(rows)})
    return self.respond(rows)
   if u.path=='/api/export':return self.respond({'exported_at':now(),'records':all_records(c),'collections':[json.loads(x[0]) for x in c.execute('SELECT body FROM collections')],'inventories':[json.loads(x[0]) for x in c.execute('SELECT body FROM inventories')],'media_assets':[json.loads(x[0]) for x in c.execute('SELECT body FROM media_assets')],'shooting_sessions':[json.loads(x[0]) for x in c.execute('SELECT body FROM shooting_sessions')]})
   if u.path=='/api/status':
    rows=all_records(c);images=[a for r in rows for a in r.get('images',[])];byhash={}
    for a in images:
     if a.get('sha256'):byhash.setdefault(a['sha256'],[]).append(a.get('path'))
    return self.respond({'collection_memberships':c.execute('SELECT count(*) FROM collection_members').fetchone()[0],'inventories':c.execute('SELECT count(*) FROM inventories').fetchone()[0],'records':len(rows),'derivatives':len(images),'originals':c.execute('SELECT count(*) FROM originals').fetchone()[0],'duplicates':[v for v in byhash.values() if len(v)>1],'missing_images':sum(not r.get('images') for r in rows),'missing_source_guid':sum(not r.get('source_guid') for r in rows),'imports':[dict(x) for x in c.execute('SELECT * FROM imports ORDER BY id DESC')],'audit':[dict(x) for x in c.execute('SELECT id,at,action,record_id FROM audit ORDER BY id DESC LIMIT 100')]})
   if u.path=='/api/collections':return self.respond([json.loads(x[0]) for x in c.execute('SELECT body FROM collections ORDER BY rowid')])
   if u.path=='/api/collection':
    name=q.get('name',[''])[0];row=c.execute('SELECT body FROM collections WHERE name=?',(name,)).fetchone()
    if not row:return self.respond({'error':'Collection not found'},404)
    collection=json.loads(row[0]);members=[]
    for m in c.execute('SELECT * FROM collection_members WHERE collection_name=? ORDER BY sort_order',(name,)):
     r=c.execute('SELECT body FROM records WHERE id=?',(m['object_code'],)).fetchone();members.append({'source':dict(m),'record':json.loads(r[0]) if r else None})
    return self.respond({'collection':collection,'members':members})
   if u.path=='/api/inventories':
    query='SELECT body FROM inventories';params=()
    if q.get('object'):query+=' WHERE object_code=?';params=(q['object'][0],)
    return self.respond([json.loads(x[0]) for x in c.execute(query,params)])
   if u.path=='/api/media':
    rows=[json.loads(x[0]) for x in c.execute('SELECT body FROM media_assets')]
    if any(k in q for k in ('limit','offset','q','kind')):
     try:return self.respond(media_results(rows,q))
     except ValueError as e:return self.respond({'error':str(e)},400)
    return self.respond(rows)
   if u.path=='/api/original':
    row=c.execute('SELECT body FROM media_assets WHERE guid=?',(q.get('guid',[''])[0],)).fetchone()
    if not row:return self.respond({'error':'Original not found'},404)
    asset=json.loads(row[0]);path=(ROOT/asset['storage_key']).resolve()
    if not path.is_relative_to(ROOT/'originals') or not path.is_file():return self.respond({'error':'Original unavailable'},404)
    size=path.stat().st_size;start,end=0,size-1;partial=False
    inline=q.get('inline',['0'])[0]=='1';extension=pathlib.Path(asset['filename']).suffix.lower();video_types={'.m4v':'video/mp4','.mp4':'video/mp4','.webm':'video/webm','.mov':'video/quicktime'}
    if inline and extension not in video_types:return self.respond({'error':'Inline preview unavailable for this format'},400)
    if self.headers.get('Range'):
     try:start,end=byte_range(self.headers['Range'],size);partial=True
     except ValueError:
      self.send_response(416);self.send_header('Content-Range','bytes */'+str(size));self.send_header('Content-Length','0');self.end_headers();return
    self.send_response(206 if partial else 200);self.send_header('Content-Type',video_types[extension] if inline else 'application/octet-stream');self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes')
    if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
    self.send_header('Content-Disposition',('inline' if inline else 'attachment')+"; filename*=UTF-8''"+urllib.parse.quote(asset['filename']));self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
    with path.open('rb') as stream:
     stream.seek(start);remaining=end-start+1
     while remaining:
      chunk=stream.read(min(1024*1024,remaining))
      if not chunk:break
      try:self.wfile.write(chunk)
      except (BrokenPipeError,ConnectionResetError):break
      remaining-=len(chunk)
    return
   if u.path=='/api/sessions':
    rows=[json.loads(x[0]) for x in c.execute('SELECT body FROM shooting_sessions')]
    if q.get('guid'):
     row=next((r for r in rows if r['guid']==q['guid'][0]),None);return self.respond(row if row else {'error':'Not found'},200 if row else 404)
    if q.get('object'):rows=[r for r in rows if r.get('object_code')==q['object'][0]]
    if q.get('inventory'):rows=[r for r in rows if r.get('inventory_id')==q['inventory'][0]]
    if q.get('q'):
     text=q['q'][0].casefold();rows=[r for r in rows if text in (r['name']+' '+json.dumps(r.get('metadata',{}),ensure_ascii=False)).casefold()]
    if q.get('shot_type'):rows=[r for r in rows if r.get('metadata',{}).get('Shot Type')==q['shot_type'][0]]
    rows.sort(key=lambda r:(r.get('metadata',{}).get('Shooting Code',''),r['name']))
    try:offset=max(0,int(q.get('offset',['0'])[0]));limit=min(100,max(1,int(q.get('limit',['50'])[0])))
    except ValueError:return self.respond({'error':'Invalid pagination'},400)
    return self.respond({'total':len(rows),'rows':rows[offset:offset+limit]})
   if u.path=='/api/physical':
    rows=[json.loads(x[0]) for x in c.execute('SELECT body FROM inventories ORDER BY id')]
    if q.get('id'):
     row=next((r for r in rows if r['id']==q['id'][0]),None);return self.respond(row if row else {'error':'Not found'},200 if row else 404)
    query=q.get('q',[''])[0].casefold()
    if query:rows=[r for r in rows if query in json.dumps(r,ensure_ascii=False).casefold()]
    total=len(rows);offset=max(0,int(q.get('offset',['0'])[0]));limit=min(100,max(1,int(q.get('limit',['50'])[0])))
    return self.respond({'total':total,'rows':[{'id':r['id'],'object_code':r.get('object_code'),'warehouse':r.get('editorial',{}).get('Warehouse',r.get('supplemental_detail',{}).get('Warehouse',r.get('metadata',{}).get('Warehouse',''))),'detail_captured':bool(r.get('source_detail'))} for r in rows[offset:offset+limit]]})
   if u.path=='/api/record':
    x=c.execute('SELECT body FROM records WHERE id=?',(q.get('id',[''])[0],)).fetchone();return self.respond(json.loads(x[0]) if x else {'error':'Not found'},200 if x else 404)
  path=ROOT/('index.html' if u.path in ('/','/admin','/migration','/collections') else u.path.lstrip('/'))
  # Never serve database, raw exports, source code, manifests or internal evidence directories.
  if u.path not in ('/','/admin','/migration','/collections','/style.css','/app.js') and not u.path.startswith('/assets/'):return self.respond({'error':'Not found'},404)
  if not path.resolve().is_relative_to(ROOT) or not path.is_file():return self.respond({'error':'Not found'},404)
  self.respond(path.read_bytes(),ctype=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
 def do_POST(self):
  if not self.guard(True):return
  try:
   size=int(self.headers.get('Content-Length','0'))
   if size<1 or size>45_000_000:raise ValueError('Invalid request size')
   p=json.loads(self.rfile.read(size))
   if self.path in ('/api/import/validate','/api/import/commit'):return self.respond(import_records(p,self.path.endswith('commit')))
   if self.path=='/api/physical/save':
    allowed={'Inventory No. Ref.','Condition Report','Remarks','Item Availability','Warehouse','Position - AREA','Position - ROW','Position - COLUMN','Position - SHELF','Position - DETAILS'}
    fields=p.get('fields')
    if not isinstance(fields,dict) or any(k not in allowed or not isinstance(v,str) or len(v)>20000 for k,v in fields.items()):raise ValueError('Invalid physical inventory fields')
    with connect() as c:
     old=c.execute('SELECT body FROM inventories WHERE id=?',(p.get('id'),)).fetchone()
     if not old:return self.respond({'error':'Inventory not found'},404)
     r=json.loads(old[0]);before=r.get('editorial',{});r['editorial']={**before,**fields};r['editorial_updated_at']=now()
     c.execute('UPDATE inventories SET body=? WHERE id=?',(json.dumps(r,ensure_ascii=False),r['id']))
     c.execute('INSERT INTO audit(at,action,record_id,body) VALUES(?,?,?,?)',(now(),'physical inventory editorial update',r['id'],json.dumps({'before':before,'after':r['editorial']})));c.commit()
     return self.respond({'saved':True,'record':r})
   if self.path=='/api/record/save':
    with connect() as c:
     x=c.execute('SELECT body FROM records WHERE id=?',(p['id'],)).fetchone()
     if not x:return self.respond({'error':'Record not found'},404)
     r=json.loads(x[0]);allowed={'title','notes','rights_status','collections','category','tags','inventory_numbers','linked_items','year_start','year_end','metadata'}
     if any(k not in allowed|{'id'} for k in p):raise ValueError('Source metadata is immutable; use import for source updates')
     for k in allowed:
      if k in p:
       if k in ('collections','tags','inventory_numbers','linked_items'):
        if not isinstance(p[k],list) or any(not isinstance(v,str) or len(v)>500 for v in p[k]):raise ValueError('Invalid list field')
       elif k in ('year_start','year_end'):
        if p[k] is not None and (not isinstance(p[k],int) or not 1000<=p[k]<=2200):raise ValueError('Invalid year')
       elif k=='metadata':
        if not isinstance(p[k],dict) or any(not isinstance(v,str) or len(v)>10000 for v in p[k].values()):raise ValueError('Invalid metadata')
       elif not isinstance(p[k],str) or len(p[k])>10000:raise ValueError('Invalid field')
       r[k]=p[k]
     save(c,r,'editorial update');c.commit();return self.respond({'saved':True,'record':r})
   return self.respond({'error':'Not found'},404)
  except (ValueError,KeyError,TypeError,zipfile.BadZipFile,ET.ParseError) as e:self.respond({'error':str(e)},400)
if __name__=='__main__':
 init()
 if len(sys.argv)>1 and sys.argv[1]=='--import':
  path=pathlib.Path(sys.argv[2]);source=sys.argv[3];payload={'filename':path.name,'source_url':source,'data':base64.b64encode(path.read_bytes()).decode()};print(json.dumps(import_records(payload,'--commit' in sys.argv,256_000_000),indent=2));sys.exit()
 print('Bally Heritage private preview: http://127.0.0.1:8765',flush=True)
 http.server.ThreadingHTTPServer(('127.0.0.1',8765),Handler).serve_forever()
