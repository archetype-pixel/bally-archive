#!/usr/bin/env python3
"""Watch only this migration's local checkpoints and manifest-matched browser ZIPs.
No source requests, browser control, external uploads or source-system writes.
"""
import contextlib,datetime,hashlib,io,json,pathlib,re,sqlite3,sys,time,zipfile
import server,ingest_sessions,ingest_details,ingest_media_zip,ingest_media_inventory,ingest_media_relationships,ingest_asset_details,ingest_direct_original
DATA=server.ROOT/'data';DOWNLOADS=pathlib.Path.home()/'Downloads'
_processed_cache={};_hash_cache={};_physical_details_count=None
def sha(path):
 st=path.stat();key=(st.st_size,st.st_mtime_ns);cached=_hash_cache.get(str(path))
 if cached and cached[0]==key:return cached[1]
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 value=h.hexdigest();_hash_cache[str(path)]=(key,value);return value
def init():
 server.init()
 with server.connect() as c:
  c.execute('CREATE TABLE IF NOT EXISTS ingestion_receipts(path TEXT PRIMARY KEY,fingerprint TEXT,kind TEXT,at TEXT,result TEXT)');c.commit()
  _processed_cache.update({r['path']:r['fingerprint'] for r in c.execute('SELECT path,fingerprint FROM ingestion_receipts')})
def processed(path,fingerprint):
 return _processed_cache.get(str(path))==fingerprint
def receipt(path,fingerprint,kind,result):
 with server.connect() as c:c.execute('INSERT OR REPLACE INTO ingestion_receipts VALUES(?,?,?,?,?)',(str(path),fingerprint,kind,server.now(),json.dumps(result)));c.commit()
 _processed_cache[str(path)]=fingerprint
def invoke(fn,*args):
 out=io.StringIO()
 with contextlib.redirect_stdout(out):fn(*args)
 try:return json.loads(out.getvalue())
 except ValueError:return {'output':out.getvalue()[:2000]}
def atomic(path,data):
 tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,indent=2));tmp.replace(path)
def step():
 global _physical_details_count
 events=[]
 checkpoints=sorted(DATA.glob('shooting-records-page-*.json'))+sorted(DATA.glob('shooting-grid-page-*.json'))+sorted(DATA.glob('physical-details-page-*.json'))+sorted(DATA.glob('media-inventory-page-*.json'))+sorted(DATA.glob('media-asset-details-page-*.json'))
 for path in checkpoints:
  digest=sha(path)
  if processed(path,digest):continue
  fn=ingest_asset_details.ingest if path.name.startswith('media-asset-details-') else ingest_media_inventory.ingest if path.name.startswith('media-inventory-') else ingest_details.ingest if path.name.startswith('physical-') else ingest_sessions.ingest
  try:
   result=invoke(fn,path);receipt(path,digest,'checkpoint',result);events.append({'file':path.name,'result':result})
   if path.name.startswith('physical-details-'):_physical_details_count=None
  except (ValueError,KeyError,sqlite3.Error,json.JSONDecodeError) as e:events.append({'file':path.name,'error':str(e)})
 manifests=[]
 for path in DATA.glob('media-batch-*.json'):
  d=json.loads(path.read_text());assets=d.get('assets',[])
  if assets and all(a.get('guid') and a.get('filename') for a in assets):manifests.append((path,assets))
 # Single-selection exports use the source filename with periods replaced by underscores.
 # Limit inspection to known source manifest names and the normal DAM_Content prefix.
 single_names={assets[0]['filename'].replace('.','_') for _,assets in manifests if len(assets)==1}
 single_patterns=[re.compile(re.escape(name)+r'( \(\d+\))?\.zip') for name in single_names]
 download_files=[path for path in DOWNLOADS.iterdir() if path.is_file()]
 candidates=[path for path in download_files if path.suffix.lower()=='.zip' and (path.name.startswith('DAM_Content') or any(pattern.fullmatch(path.name) for pattern in single_patterns))]
 for path in sorted(candidates):
  st=path.stat();fingerprint=str(st.st_size)+':'+str(st.st_mtime_ns)
  if processed(path,fingerprint):continue
  try:
   with zipfile.ZipFile(path) as z:names=[i.filename for i in z.infolist() if not i.is_dir()]
   matches=[(p,a) for p,a in manifests if len(a)==len(names) and {x['filename'] for x in a}==set(names)]
   guid_sets={tuple(sorted(a['guid'] for a in assets)) for _,assets in matches}
   if len(guid_sets)!=1:events.append({'file':path.name,'status':'Unmatched or ambiguous; retained without automatic assignment'});continue
   manifest=matches[-1][0];result=invoke(ingest_media_zip.ingest,path,manifest);receipt(path,fingerprint,'original ZIP',result);events.append({'file':path.name,'result':result})
  except (ValueError,KeyError,zipfile.BadZipFile,OSError,sqlite3.Error) as e:events.append({'file':path.name,'error':str(e)})
 for path in sorted(DATA.glob('direct-original-download-*.json')):
  fingerprint=sha(path)
  if processed(path,fingerprint):continue
  try:result=invoke(ingest_direct_original.ingest,path);receipt(path,fingerprint,'original direct file',result);events.append({'file':path.name,'result':result})
  except (ValueError,KeyError,OSError,sqlite3.Error,json.JSONDecodeError) as e:events.append({'file':path.name,'error':str(e)})
 download_index={}
 for path in download_files:
  if path.name.endswith('.crdownload'):continue
  download_index.setdefault(path.name,[]).append(path)
  match=re.fullmatch(r'(.+) \(\d+\)(\.[^.]+)',path.name)
  if match:download_index.setdefault(match[1]+match[2],[]).append(path)
 for path in sorted(DATA.glob('direct-original-request-*.json')):
  fingerprint=sha(path)
  if processed(path,fingerprint):continue
  try:result=invoke(ingest_direct_original.ingest_request,path,download_index);receipt(path,fingerprint,'source-bound finalized original',result);events.append({'file':path.name,'result':result})
  except ingest_direct_original.PendingDownload:pass
  except (ValueError,KeyError,OSError,sqlite3.Error,json.JSONDecodeError) as e:events.append({'file':path.name,'error':str(e)})
 for path in sorted(DATA.glob('media-relations-*.json')):
  digest=sha(path)
  if processed(path,digest):continue
  try:result=invoke(ingest_media_relationships.ingest,path);receipt(path,digest,'source media relationships',result);events.append({'file':path.name,'result':result})
  except (ValueError,KeyError,sqlite3.Error,OSError) as e:events.append({'file':path.name,'error':str(e)})
 with server.connect() as c:
  tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  counts={t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('records','inventories','originals','media_assets','collection_members','shooting_sessions','source_media_index','source_media_pages','source_asset_details') if t in tables}
  if _physical_details_count is None:_physical_details_count=sum(bool(json.loads(r[0]).get('source_detail')) for r in c.execute('SELECT body FROM inventories'))
  details=_physical_details_count
  verified_guids=[r[0] for r in c.execute('SELECT guid FROM media_assets ORDER BY guid')]
  verified_set={g.upper() for g in verified_guids};page_coverage=[]
  for row in (c.execute('SELECT page,body FROM source_media_pages ORDER BY page') if 'source_media_pages' in tables else []):
   page=json.loads(row['body']);assets=page['assets'];present=sum(a['guid'].upper() in verified_set for a in assets);page_coverage.append({'page':row['page'],'listed_assets':len(assets),'verified_originals':present,'complete':present==len(assets) and len(assets)>0})
 verified_path=DATA/'verified-original-guids.json'
 if not verified_path.exists() or json.loads(verified_path.read_text()).get('guids')!=verified_guids:atomic(verified_path,{'checked_at':server.now(),'guids':verified_guids,'count':len(verified_guids),'scope':'Asset identities whose official browser downloads passed local ingestion verification; server-side expected checksums unavailable.'})
 status={'checked_at':server.now(),'counts':counts,'physical_detail_pages_imported':details,'original_page_coverage':page_coverage,'completed_original_source_pages':[r['page'] for r in page_coverage if r['complete']],'source_targets':{'objects':17233,'physical_inventory_rows':17357,'assets':75360,'shooting_sessions':35844},'recent_events':events[-10:],'cutover_ready':False,'scope':'Local ingestion only. Browser extraction, missing detail fields, versions, relationships, workflows and hosted operations remain incomplete.'}
 atomic(DATA/'live-migration.json',status)
 for event in events:print(json.dumps(event),flush=True)
 return status
if __name__=='__main__':
 init()
 if '--once' in sys.argv:print(json.dumps(step(),indent=2))
 else:
  interval=float(sys.argv[sys.argv.index('--interval')+1]) if '--interval' in sys.argv else 5
  if not 0.5<=interval<=60:raise ValueError('Local ingestion interval must be 0.5 to 60 seconds')
  print('Private Bally ingestion watcher started; only local checkpoints, matched ZIPs and completed direct-download receipts are processed.',flush=True)
  while True:
   try:step()
   except Exception as e:print(json.dumps({'at':server.now(),'pipeline_error':str(e)}),flush=True)
   time.sleep(interval)
