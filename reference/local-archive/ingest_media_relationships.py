#!/usr/bin/env python3
"""Apply explicitly captured source media/inventory edges to verified local assets."""
import hashlib,json,pathlib,sys
import server

def ingest(path):
 path=pathlib.Path(path); payload=json.loads(path.read_text()); batch=hashlib.sha256(path.read_bytes()).hexdigest(); updated=0
 with server.connect() as c:
  for edge in payload['records']:
   evidence=server.ROOT/'data'/edge['evidence_file']
   if evidence.parent!=server.ROOT/'data' or hashlib.sha256(evidence.read_bytes()).hexdigest()!=edge['evidence_sha256']:raise ValueError('Source relationship evidence does not match')
   proof=json.loads(evidence.read_text())
   if proof['asset_guid']!=edge['asset_guid']:raise ValueError('Source evidence asset identity differs')
   row=c.execute('SELECT body FROM media_assets WHERE guid=?',(edge['asset_guid'],)).fetchone()
   if not row:raise ValueError('Verified original asset is missing')
   asset=json.loads(row[0]); inventories=[]; objects=[]
   for code in edge['inventory_numbers']:
    if code not in proof['detail']['text'].splitlines():raise ValueError('Inventory identity not explicit in source detail')
    inv=c.execute('SELECT object_code FROM inventories WHERE id=?',(code,)).fetchone()
    if not inv or not inv[0] or not c.execute('SELECT 1 FROM records WHERE id=?',(inv[0],)).fetchone():raise ValueError('Inventory/object relationship unresolved')
    inventories.append(code);objects.append(inv[0])
   asset['inventory_numbers']=sorted(set(asset.get('inventory_numbers',[])+inventories));asset['record_ids']=sorted(set(asset.get('record_ids',[])+objects))
   asset['source_relationship_evidence']={'file':edge['evidence_file'],'sha256':edge['evidence_sha256'],'source_url':proof['source_url'],'captured_at':proof['captured_at'],'basis':'Explicit inventory code in source asset Info dialog; object resolved through official inventory export.'}
   c.execute('UPDATE media_assets SET body=? WHERE guid=?',(json.dumps(asset),edge['asset_guid']));updated+=1
  c.execute('INSERT INTO audit(at,action,record_id,body) VALUES(?,?,?,?)',(server.now(),'source media relationship ingest',path.name,json.dumps({'assets':updated,'evidence_sha256':batch})));c.commit()
 print(json.dumps({'updated_assets':updated,'evidence_sha256':batch}))

if __name__=='__main__':ingest(sys.argv[1])
