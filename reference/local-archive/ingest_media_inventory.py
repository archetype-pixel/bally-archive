#!/usr/bin/env python3
"""Index browser-visible source identities separately from recovered file bytes."""
import hashlib,json,pathlib,sys
import server

def ingest(path):
 path=pathlib.Path(path);raw=path.read_bytes();d=json.loads(raw);assets=d['assets'];total=d['totals'];page=int(d['page']);digest=hashlib.sha256(raw).hexdigest()
 if total[2]!=75360 or len(assets)!=total[1]-total[0]+1 or len({a['guid'] for a in assets})!=len(assets):raise ValueError('Unreconciled source listing page')
 if any(not a.get('asset_id') or not a.get('filename') or not a.get('guid') for a in assets):raise ValueError('Missing source identity')
 with server.connect() as c:
  c.execute('CREATE TABLE IF NOT EXISTS source_media_index(guid TEXT PRIMARY KEY,asset_id TEXT,filename TEXT,body TEXT)')
  c.execute('CREATE TABLE IF NOT EXISTS source_media_pages(page INTEGER PRIMARY KEY,source_sha256 TEXT,body TEXT)')
  for a in assets:
   old=c.execute('SELECT asset_id,filename FROM source_media_index WHERE guid=?',(a['guid'],)).fetchone()
   if old and tuple(old)!=(a['asset_id'],a['filename']):raise ValueError('Source GUID identity changed; review required')
   body={**a,'source_url':d['source_url'],'captured_at':d['captured_at'],'source_page':page,'evidence_sha256':digest,'original_bytes_recovered':False}
   c.execute('INSERT OR REPLACE INTO source_media_index VALUES(?,?,?,?)',(a['guid'],a['asset_id'],a['filename'],json.dumps(body)))
  c.execute('INSERT OR REPLACE INTO source_media_pages VALUES(?,?,?)',(page,digest,json.dumps(d)));c.commit()
 print(json.dumps({'source_page':page,'indexed_assets':len(assets),'originals_added':0,'evidence_sha256':digest}))
if __name__=='__main__':server.init();ingest(sys.argv[1])
