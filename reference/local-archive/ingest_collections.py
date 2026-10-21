#!/usr/bin/env python3
"""Preserve ordered collection membership from read-only browser evidence."""
import json,pathlib,sys
import server
p=server.ROOT/'data/source-collection-memberships.json';data=json.loads(p.read_text())
with server.connect() as c:
 c.executescript('CREATE TABLE IF NOT EXISTS collections(name TEXT PRIMARY KEY,body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS collection_members(collection_name TEXT,object_code TEXT,sort_order INTEGER,source_guid TEXT,source_asset_id TEXT,source_url TEXT,download_guid TEXT,PRIMARY KEY(collection_name,object_code));')
 rows={r['id']:r for r in server.all_records(c)};edges=unresolved=0
 for col in data['collections']:
  if col['has_more']:raise ValueError('Incomplete collection: '+col['name'])
  c.execute('INSERT OR REPLACE INTO collections VALUES(?,?)',(col['name'],json.dumps(col,ensure_ascii=False)))
  for member in col['members']:
   code=member['object_code'];edges+=1
   c.execute('INSERT OR REPLACE INTO collection_members VALUES(?,?,?,?,?,?,?)',(col['name'],code,member['order'],member['guid'],member['asset_id'],member['source_url'],member['download_guid']))
   if code in rows:
    r=rows[code];r['collections']=list(dict.fromkeys(r.get('collections',[])+[col['name']]))
    for key,value in [('source_guid',member['guid']),('source_asset_id',member['asset_id']),('source_url',member['source_url']),('primary_download_guid',member['download_guid'])]:
     if key=='source_url' or not r.get(key):r[key]=value
    c.execute('UPDATE records SET body=? WHERE id=?',(json.dumps(r,ensure_ascii=False),code))
   else:unresolved+=1
 c.execute('INSERT INTO audit(at,action,record_id,body) VALUES(?,?,?,?)',(server.now(),'collection membership import','batch',json.dumps({'collections':len(data['collections']),'edges':edges,'unresolved':unresolved})));c.commit()
print(json.dumps({'collections':len(data['collections']),'memberships':edges,'unresolved_object_memberships':unresolved,'all_pagination_exhausted':True},indent=2))
