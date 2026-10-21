#!/usr/bin/env python3
"""Import official inventory-number export as distinct physical inventory records."""
import hashlib,json,pathlib,sys
import server
SOURCE='https://ballyheritage.com/DigProd/PRJSearch/Search/62?assetKey=heritageINVENTORYNO&buttonKey=browseHeritageInventoryNo'
def ingest(path):
 data=path.read_bytes();sha=hashlib.sha256(data).hexdigest();rows=server.xlsx_rows(data);seen=set();errors=[]
 for n,r in enumerate(rows):
  code=r.get('Inventory No.','').strip()
  if not code:errors.append({'row':n+2,'error':'Missing Inventory No.'})
  elif code in seen:errors.append({'row':n+2,'error':'Duplicate Inventory No.','id':code})
  seen.add(code)
 if errors:return {'rows':len(rows),'errors':errors,'committed':False}
 with server.connect() as c:
  c.execute('CREATE TABLE IF NOT EXISTS inventories(id TEXT PRIMARY KEY,object_code TEXT,body TEXT NOT NULL,source_sha256 TEXT NOT NULL)')
  objects={r['id']:r for r in server.all_records(c)};unresolved=[];edges={}
  for raw in rows:
   code=raw['Inventory No.'].strip();obj=raw.get('Object Code','').strip()
   inv={'id':code,'object_code':obj,'metadata':raw,'raw':raw,'source_url':SOURCE,'workbook_sha256':sha,'captured_at':server.now()}
   previous=c.execute('SELECT body FROM inventories WHERE id=?',(code,)).fetchone()
   if previous:
    prior=json.loads(previous[0])
    for key,value in prior.items():
     if key not in inv:inv[key]=value
   c.execute('INSERT OR REPLACE INTO inventories VALUES(?,?,?,?)',(code,obj,json.dumps(inv,ensure_ascii=False),sha));edges.setdefault(obj,[]).append(code)
   if obj not in objects:unresolved.append({'inventory_id':code,'object_code':obj})
  for code,inv in edges.items():
   if code in objects:
    r=objects[code];r['inventory_numbers']=list(dict.fromkeys(r.get('inventory_numbers',[])+inv));c.execute('UPDATE records SET body=? WHERE id=?',(json.dumps(r,ensure_ascii=False),code))
  c.execute('INSERT INTO audit(at,action,record_id,body) VALUES(?,?,?,?)',(server.now(),'inventory-number import','batch',json.dumps({'rows':len(rows),'sha256':sha,'unresolved_relationships':len(unresolved)})));c.commit()
 report={'rows':len(rows),'unique_inventory_ids':len(seen),'object_codes':len(edges),'unresolved_relationships':unresolved,'sha256':sha,'committed':True};(server.ROOT/'data/inventory-number-import-report.json').write_text(json.dumps(report,indent=2));return {k:v for k,v in report.items() if k!='unresolved_relationships'}|{'unresolved_relationship_count':len(unresolved)}
if __name__=='__main__':server.init();print(json.dumps(ingest(pathlib.Path(sys.argv[1])),indent=2))
