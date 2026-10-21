#!/usr/bin/env python3
"""Retain source asset Info fields and reconcile explicit inventory links."""
import hashlib,json,pathlib,re,sys
import server

def field_ids(value):
 try:parsed=json.loads(value or 'null')
 except (ValueError,TypeError):
  return value.split(',') if isinstance(value,str) and re.fullmatch(r'\d+(,\d+)*',value) else []
 return [str(v) for v in (parsed if isinstance(parsed,list) else [] if parsed is None else [parsed])]

def enrich(c,asset):
 if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_asset_details'").fetchone():return asset
 row=c.execute('SELECT body,evidence_sha256 FROM source_asset_details WHERE guid=?',(asset['guid'],)).fetchone()
 if not row:return asset
 record=json.loads(row[0]);metadata={};links=[];unresolved=[]
 for group in record['detail']['groups']:
  label=re.sub(r'[\s*:]+$','',group.get('label') or '')
  if not label:continue
  fields=group.get('fields',[])
  if label=='Images':
   field=next((f for f in fields if f.get('id')=='METADATA41'),{});refs=record.get('relationship_references',[]);expected=field_ids(field.get('source_value'))
   valid_refs=record.get('relationship_references_complete') and isinstance(refs,list) and all(isinstance(r,dict) for r in refs) and len(refs)==len(expected) and {str(r.get('ID')) for r in refs}==set(expected) and all(isinstance(r.get('Value'),str) and r['Value'].strip() for r in refs)
   candidates=[(r['Value'].strip(),str(r['ID'])) for r in refs] if valid_refs else [(choice.splitlines()[0].strip(),None) for choice in group.get('choices',[])]
   for code,source_id in candidates:
    inv=c.execute('SELECT object_code FROM inventories WHERE id=?',(code,)).fetchone()
    if inv and inv[0] and c.execute('SELECT 1 FROM records WHERE id=?',(inv[0],)).fetchone():links.append({'inventory_number':code,'object_code':inv[0],'source_inventory_asset_id':source_id})
    else:unresolved.append(code)
  else:
   text_fields=[f for f in fields if f.get('value') is not None]
   field=next((f for f in fields if f.get('id','').startswith('METADATA')),{});selected=field_ids(field.get('source_value'));terms=[t for t in (field.get('term_values') if isinstance(field.get('term_values'),list) else []) if isinstance(t,dict) and str(t.get('I')) in selected and isinstance(t.get('V'),str)];names=list(dict.fromkeys(t['V'] for t in terms))
   metadata[label]=text_fields[0]['value'] if text_fields else names[0] if len(names)==1 else names if names else group.get('text','').replace(group.get('label') or '', '',1).strip()
 asset['source_metadata']=metadata;asset['source_asset_detail']=record;asset['source_asset_detail_sha256']=row[1]
 asset['source_asset_detail_evidence_path']='data/asset-detail-records/'+row[1]+'.json'
 asset['source_detail_relationships']=links;asset['unresolved_source_inventory_codes']=unresolved
 asset['inventory_numbers']=sorted(set(asset.get('inventory_numbers',[])+[x['inventory_number'] for x in links]))
 asset['record_ids']=sorted(set(asset.get('record_ids',[])+[x['object_code'] for x in links]))
 return asset

def ingest(path):
 path=pathlib.Path(path);payload=json.loads(path.read_text());updated=0
 with server.connect() as c:
  c.execute('CREATE TABLE IF NOT EXISTS source_asset_details(guid TEXT PRIMARY KEY,body TEXT NOT NULL,evidence_sha256 TEXT NOT NULL)')
  c.execute('CREATE TABLE IF NOT EXISTS source_asset_detail_captures(guid TEXT,evidence_sha256 TEXT,captured_at TEXT,PRIMARY KEY(guid,evidence_sha256))')
  for record in payload['records']:
   if record['detail']['title']!=record['filename']:raise ValueError('Asset detail title does not match source identity')
   body=json.dumps(record,ensure_ascii=False,sort_keys=True);digest=hashlib.sha256(body.encode()).hexdigest()
   folder=server.ROOT/'data/asset-detail-records';folder.mkdir(exist_ok=True);evidence=folder/(digest+'.json')
   if evidence.exists():
    if hashlib.sha256(evidence.read_bytes()).hexdigest()!=digest:raise ValueError('Retained source metadata evidence differs')
   else:
    tmp=evidence.with_suffix('.json.tmp');tmp.write_text(body);tmp.replace(evidence)
   c.execute('INSERT OR IGNORE INTO source_asset_detail_captures VALUES(?,?,?)',(record['guid'],digest,record['captured_at']))
   prior=c.execute('SELECT evidence_sha256 FROM source_asset_details WHERE guid=?',(record['guid'],)).fetchone()
   if prior and prior[0]==digest:
    existing=c.execute('SELECT body FROM media_assets WHERE guid=?',(record['guid'],)).fetchone()
    if existing:
     asset=json.loads(existing[0])
     if asset.get('source_asset_detail_evidence_path')!='data/asset-detail-records/'+digest+'.json':c.execute('UPDATE media_assets SET body=? WHERE guid=?',(json.dumps(enrich(c,asset),ensure_ascii=False),record['guid']))
    continue
   c.execute('INSERT OR REPLACE INTO source_asset_details VALUES(?,?,?)',(record['guid'],body,digest))
   asset=c.execute('SELECT body FROM media_assets WHERE guid=?',(record['guid'],)).fetchone()
   if asset:c.execute('UPDATE media_assets SET body=? WHERE guid=?',(json.dumps(enrich(c,json.loads(asset[0])),ensure_ascii=False),record['guid']))
   updated+=1
  c.execute('INSERT INTO audit(at,action,record_id,body) VALUES(?,?,?,?)',(server.now(),'source asset Info detail ingest',path.name,json.dumps({'updated':updated,'captured':len(payload['records'])})));c.commit()
 print(json.dumps({'updated':updated,'captured':len(payload['records'])}))

if __name__=='__main__':ingest(sys.argv[1])
