#!/usr/bin/env python3
"""Validate a browser-selected source manifest against an original-media ZIP.
Preserve compressed source bytes, content-address originals, retain source GUIDs.
"""
import hashlib,json,pathlib,shutil,sys,zipfile
import server,ingest_asset_details

def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()

def ingest(zpath,mpath):
 manifest=json.loads(mpath.read_text());assets=manifest['assets'];expected={a['filename']:a for a in assets}
 if len(expected)!=len(assets):raise ValueError('Ambiguous filenames in selected batch')
 with zipfile.ZipFile(zpath) as z:
  members=[i for i in z.infolist() if not i.is_dir()]
  if len(members)!=len(assets) or {i.filename for i in members}!=set(expected):raise ValueError('ZIP contents differ from selected source manifest')
  bad=z.testzip()
  if bad:raise ValueError('ZIP integrity failure: '+bad)
  batch=digest(zpath);folder=server.ROOT/'data/media-batches';folder.mkdir(exist_ok=True)
  retained=folder/(batch+'.zip')
  if not retained.exists():shutil.copyfile(zpath,retained)
  if digest(retained)!=batch:raise ValueError('Retained ZIP checksum mismatch')
  recovered=[]
  for member in members:
   a=expected[member.filename];h=hashlib.sha256();tmp=server.ROOT/'originals'/('pending-'+batch+'-'+str(len(recovered)));tmp.parent.mkdir(exist_ok=True)
   with z.open(member) as src,tmp.open('wb') as out:
    for chunk in iter(lambda:src.read(1024*1024),b''):h.update(chunk);out.write(chunk)
   sha=h.hexdigest();dest=server.ROOT/'originals'/sha[:2]/sha;dest.parent.mkdir(exist_ok=True)
   if dest.exists():
    if digest(dest)!=sha:raise ValueError('Existing content-addressed original differs')
    tmp.unlink()
   else:tmp.replace(dest)
   if digest(dest)!=sha:raise ValueError('Original checksum verification failed')
   recovered.append({**a,'sha256':sha,'bytes':member.file_size,'storage_key':str(dest.relative_to(server.ROOT)),'role':'original','version':None,'record_ids':[],'inventory_numbers':[],'captured_at':server.now(),'source_url':manifest.get('source_url'),'batch_sha256':batch,'verification':'ZIP CRC passed; exact filename matched selected source GUID manifest; retained bytes SHA-256 verified. Remote expected checksum unavailable.'})
 with server.connect() as c:
  for a in recovered:
   old=c.execute('SELECT body FROM media_assets WHERE guid=?',(a['guid'],)).fetchone()
   if old:
    prior=json.loads(old[0]);a={**prior,**a,'record_ids':prior.get('record_ids',[]),'inventory_numbers':prior.get('inventory_numbers',[])}
   a=ingest_asset_details.enrich(c,a)
   c.execute('INSERT OR REPLACE INTO originals VALUES(?,?,?,?,?)',(a['sha256'],a['storage_key'],a['bytes'],a['filename'],a['guid']))
   c.execute('INSERT OR REPLACE INTO media_assets VALUES(?,?)',(a['guid'],json.dumps(a,ensure_ascii=False)))
  c.execute('INSERT INTO audit(at,action,record_id,body) VALUES(?,?,?,?)',(server.now(),'verified original media ZIP ingest',batch,json.dumps({'assets':len(recovered),'source_manifest':mpath.name})));c.commit()
 report={'batch_sha256':batch,'originals':len(recovered),'bytes':sum(a['bytes'] for a in recovered),'filenames_match':True,'zip_crc_verified':True,'assets':recovered}
 (folder/(batch+'.json')).write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='assets'},indent=2))
if __name__=='__main__':server.init();ingest(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]))
