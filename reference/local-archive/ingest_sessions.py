#!/usr/bin/env python3
"""Preserve read-only shooting grid captures and explicit session→inventory→object edges."""
import hashlib,json,pathlib,sys
import server

def ingest(path):
 payload=path.read_bytes();sha=hashlib.sha256(payload).hexdigest();d=json.loads(payload);rows=d['records']
 if 'columns' in d:
  converted=[]
  for r in rows:
   metadata={};columns={};preview_url=None
   for col in d['columns']:
    cell=next((x for x in col['cells'] if x['asset_id']==r['asset_id']),None)
    if cell is None:continue
    if col['id'].startswith('header-md-') or col['id']=='header-insert-date':
     metadata[col['label']]=cell['text'];columns[col['id']]={'label':col['label'],'metadata_id':col.get('metadata_id'),'metadata_name':col.get('metadata_name'),'value':cell['text']}
    if cell.get('preview_url'):preview_url=cell['preview_url']
   converted.append({**r,'metadata':metadata,'columns':columns,'preview_url':preview_url})
  rows=converted
 seen=set();linked=0
 with server.connect() as c:
  c.execute('CREATE TABLE IF NOT EXISTS shooting_sessions(guid TEXT PRIMARY KEY,asset_id TEXT,inventory_id TEXT,object_code TEXT,body TEXT NOT NULL)')
  for row in rows:
   guid=row['guid']
   if not guid or guid in seen:raise ValueError('Missing or duplicate source GUID')
   seen.add(guid);code=row['metadata'].get('Shooting Code','');inv=c.execute('SELECT object_code FROM inventories WHERE id=?',(code,)).fetchone();obj=inv[0] if inv else ''
   if inv:linked+=1
   record={**row,'source_url':d['source_url'],'captured_at':d['captured_at'],'evidence_sha256':sha,'inventory_id':code if inv else None,'object_code':obj or None,'relationship_basis':'Shooting Code field matches exported Inventory No.' if inv else 'No verified matching inventory record'}
   old=c.execute('SELECT body FROM shooting_sessions WHERE guid=?',(guid,)).fetchone()
   if old:
    prior=json.loads(old[0]);record['metadata']={**prior.get('metadata',{}),**record['metadata']};record['columns']={**prior.get('columns',{}),**record['columns']}
    for k in ('source_detail','editorial'):
     if prior.get(k):record[k]=prior[k]
   c.execute('INSERT OR REPLACE INTO shooting_sessions VALUES(?,?,?,?,?)',(guid,row['asset_id'],record['inventory_id'],obj,json.dumps(record,ensure_ascii=False)))
  c.execute('INSERT INTO audit(at,action,record_id,body) VALUES(?,?,?,?)',(server.now(),'shooting session grid ingest',path.name,json.dumps({'rows':len(rows),'verified_inventory_links':linked,'evidence_sha256':sha})));c.commit()
 print(json.dumps({'rows':len(rows),'verified_inventory_links':linked,'evidence_sha256':sha}))
if __name__=='__main__':server.init();[ingest(pathlib.Path(p)) for p in sys.argv[1:]]
