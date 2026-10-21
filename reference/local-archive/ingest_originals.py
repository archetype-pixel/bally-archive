#!/usr/bin/env python3
"""Ingest an authorized local original-file manifest without contacting the source.
python3 ingest_originals.py manifest.json [--commit]
Manifest: {"root":"/path/to/export","files":[{"record_id":"SHOO...",
"filename":"image.tif","path":"relative/image.tif","source_guid":"...",
"source_url":"https://ballyheritage.com/...","sha256":"optional expected digest"}]}
"""
import hashlib,json,pathlib,shutil,sys
import server

def ingest(manifest,commit=False):
 root=pathlib.Path(manifest['root']).resolve();items=[];errors=[];seen={}
 with server.connect() as c:
  records={r['id']:r for r in server.all_records(c)}
  for i,f in enumerate(manifest.get('files',[])):
   try:
    path=(root/f['path']).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('Missing file or path outside export root')
    if f['record_id'] not in records:raise ValueError('Unresolved object relationship')
    if not f.get('source_guid'):raise ValueError('Missing original source GUID')
    if not f.get('source_url','').startswith(('https://ballyheritage.com/','https://cdn.ballyheritage.com/')):raise ValueError('Missing Bally source URL')
    sha=hashlib.sha256()
    with path.open('rb') as stream:
     for chunk in iter(lambda:stream.read(1024*1024),b''):sha.update(chunk)
    digest=sha.hexdigest()
    if f.get('sha256') and f['sha256']!=digest:raise ValueError('Checksum mismatch')
    exists=c.execute('SELECT sha256 FROM originals WHERE sha256=?',(digest,)).fetchone() is not None
    a={**f,'sha256':digest,'bytes':path.stat().st_size,'filename':f.get('filename') or path.name,'role':'original','original_status':'Verified','captured_at':server.now(),'storage_key':f'originals/{digest[:2]}/{digest}'}
    items.append((path,a));seen.setdefault(digest,[]).append(f['source_guid'])
   except (ValueError,KeyError,OSError) as e:errors.append({'entry':i+1,'error':str(e)})
  if commit and not errors:
   for path,a in items:
    dest=server.ROOT/a['storage_key'];dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():
     tmp=dest.with_suffix('.partial');shutil.copyfile(path,tmp)
     if hashlib.sha256(tmp.read_bytes()).hexdigest()!=a['sha256']:tmp.unlink();raise ValueError('Copy verification failed')
     tmp.replace(dest)
    c.execute('INSERT OR IGNORE INTO originals VALUES(?,?,?,?,?)',(a['sha256'],a['storage_key'],a['bytes'],a['filename'],a['source_guid']))
    r=records[a['record_id']];r.setdefault('originals',[])
    if not any(x['source_guid']==a['source_guid'] and x['sha256']==a['sha256'] for x in r['originals']):r['originals'].append(a);server.save(c,r,'original ingest')
   c.commit()
 return {'files':len(items),'bytes':sum(a['bytes'] for _,a in items),'duplicate_content_groups':[v for v in seen.values() if len(v)>1],'errors':errors,'committed':commit and not errors,'manifest':[a for _,a in items]}
if __name__=='__main__':
 server.init();m=json.loads(pathlib.Path(sys.argv[1]).read_text());report=ingest(m,'--commit' in sys.argv);print(json.dumps(report,indent=2));sys.exit(1 if report['errors'] else 0)
