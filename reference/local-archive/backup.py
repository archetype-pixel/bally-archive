#!/usr/bin/env python3
"""Consistent local backup plus independent checksum and SQLite restore verification."""
import datetime,hashlib,json,pathlib,sqlite3,tarfile,tempfile,sys,os
import server

def file_hash(path):
 with path.open('rb') as f:return stream_hash(f)
def stream_hash(stream):
 h=hashlib.sha256()
 for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def verify(path):
 checked=0
 with tarfile.open(path,'r:gz') as tar,tempfile.TemporaryDirectory() as d:
  expected=json.load(tar.extractfile('checksums.json'));names=[m.name for m in tar.getmembers() if m.isfile()]
  if len(names)!=len(set(names)) or set(names)!=set(expected)|{'checksums.json'}:raise ValueError('Backup membership mismatch')
  for key,sha in expected.items():
   with tar.extractfile(key) as source:
    if stream_hash(source)!=sha:raise ValueError('Backup checksum mismatch: '+key)
   checked+=1
  db=pathlib.Path(d)/'restored.sqlite3'
  with tar.extractfile('data/archive.sqlite3') as src,db.open('wb') as dst:
   for chunk in iter(lambda:src.read(1024*1024),b''):dst.write(chunk)
  with sqlite3.connect(db) as c:
   integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
   if integrity!='ok':raise ValueError('Restored database integrity failed')
   tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
   counts={table:c.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('records','inventories','media_assets','originals','collections','collection_members','shooting_sessions','ingestion_receipts','source_media_index','source_media_pages','source_asset_details','source_asset_detail_captures','audit') if table in tables}
 report={'backup':str(path),'verified_at':server.now(),'sha256':file_hash(path),'bytes':path.stat().st_size,'verified_files':checked,'sqlite_integrity':integrity,'restored_counts':counts,'scope':'Recovered local archive only; source extraction and functionality parity remain incomplete.'}
 path.with_suffix(path.suffix+'.verification.json').write_text(json.dumps(report,indent=2));return report

def create():
 server.init();name='bally-backup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')+'.tar.gz';out=server.ROOT.parent/name
 with tempfile.TemporaryDirectory() as d:
  root=pathlib.Path(d);db=root/'archive.sqlite3'
  with server.connect() as source,sqlite3.connect(db) as dest:source.backup(dest)
  manifest={};files=[p for p in server.ROOT.iterdir() if p.is_file()]
  files += [p for directory in ('assets','originals','derivatives','data') for p in (server.ROOT/directory).rglob('*') if p.is_file() and not p.name.endswith('.tmp') and p.name not in ('archive.sqlite3','archive.sqlite3-wal','archive.sqlite3-shm')]
  with tarfile.open(out,'w:gz',compresslevel=1) as tar:
   tar.add(db,arcname='data/archive.sqlite3');manifest['data/archive.sqlite3']=file_hash(db)
   for file in sorted(files):
    key=str(file.relative_to(server.ROOT))
    with file.open('rb') as stream:
     manifest[key]=stream_hash(stream);stream.seek(0);info=tar.gettarinfo(str(file),arcname=key);info.size=os.fstat(stream.fileno()).st_size;tar.addfile(info,stream)
   mf=root/'checksums.json';mf.write_text(json.dumps(manifest,indent=2));tar.add(mf,arcname='checksums.json')
 return verify(out)
if __name__=='__main__':print(json.dumps(verify(pathlib.Path(sys.argv[2])) if len(sys.argv)>1 and sys.argv[1]=='--verify' else create(),indent=2))
