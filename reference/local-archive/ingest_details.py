#!/usr/bin/env python3
"""Merge checkpointed read-only physical detail captures without discarding source evidence."""
import hashlib,json,pathlib,sys
import server

def ingest(path):
 payload=path.read_bytes();batch=hashlib.sha256(payload).hexdigest();records=json.loads(payload)['records'];seen=set()
 with server.connect() as c:
  added=0
  for capture in records:
   key=capture['inventory_id'];detail=capture['detail']
   if key in seen or detail['title']!=key:raise ValueError('Duplicate or mismatched detail identity')
   seen.add(key);row=c.execute('SELECT body FROM inventories WHERE id=?',(key,)).fetchone()
   if not row:raise ValueError('Inventory detail has no exported inventory record: '+key)
   r=json.loads(row[0]);fields={f['label'].strip().rstrip(':'):f['value'] for f in detail['fields'] if f.get('label') and f.get('id','').startswith('METADATA')}
   selections=detail.get('selections',[])
   if len(selections)==2:
    fields['Item Availability']='' if selections[0]=='Select' else selections[0];fields['Warehouse']='' if selections[1]=='Select' else selections[1]
   if r.get('source_detail')==detail:continue
   r.update(supplemental_detail=fields,source_detail=detail,detail_source_url=capture['source_url'],detail_captured_at=capture['captured_at'],detail_source_asset_id=capture['source_asset_id'],detail_evidence_sha256=batch)
   c.execute('UPDATE inventories SET body=? WHERE id=?',(json.dumps(r,ensure_ascii=False),key));added+=1
  c.execute('INSERT INTO audit(at,action,record_id,body) VALUES(?,?,?,?)',(server.now(),'physical detail page ingest',path.name,json.dumps({'updated':added,'captured':len(records),'evidence_sha256':batch})));c.commit()
 print(json.dumps({'captured':len(records),'updated':added,'evidence_sha256':batch}))
if __name__=='__main__':server.init();ingest(pathlib.Path(sys.argv[1]))
