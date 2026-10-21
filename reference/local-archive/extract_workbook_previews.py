#!/usr/bin/env python3
"""Recover embedded Excel previews and explicit row-to-object relationships.
Never labels workbook thumbnails as originals. Idempotent, checksum-addressed copies.
Usage: python3 extract_workbook_previews.py data/official-inventory-full.xlsx
"""
import hashlib,json,pathlib,posixpath,sys,zipfile,xml.etree.ElementTree as ET
import server

def extract(path):
 data=path.read_bytes();batch=hashlib.sha256(data).hexdigest();z=zipfile.ZipFile(path);rows=server.xlsx_rows(data)
 ns={'x':'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing','a':'http://schemas.openxmlformats.org/drawingml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
 added=unchanged=0;unresolved=[];manifest=[]
 with server.connect() as c:
  records={r['id']:r for r in server.all_records(c)}
  for name in z.namelist():
   if not name.startswith('xl/drawings/drawing') or not name.endswith('.xml'):continue
   relpath=posixpath.dirname(name)+'/_rels/'+posixpath.basename(name)+'.rels'
   if relpath not in z.namelist():continue
   rels={x.attrib['Id']:x.attrib['Target'] for x in ET.fromstring(z.read(relpath))}
   for anchor in ET.fromstring(z.read(name)):
    row=anchor.find('x:from/x:row',ns);blip=anchor.find('.//a:blip',ns)
    if row is None or blip is None:continue
    index=int(row.text)-1
    if not 0<=index<len(rows):unresolved.append({'row':index+2,'error':'No row metadata'});continue
    code=rows[index].get('Object Code');rid=blip.attrib.get('{'+ns['r']+'}embed');target=rels.get(rid,'')
    target=posixpath.normpath(posixpath.join(posixpath.dirname(name),target)) if not target.startswith('/') else target.lstrip('/')
    if code not in records or target not in z.namelist():unresolved.append({'row':index+2,'code':code,'file':target});continue
    b=z.read(target);sha=hashlib.sha256(b).hexdigest();suffix=pathlib.Path(target).suffix.lower();dest=server.ROOT/'assets'/(sha+suffix)
    if suffix not in ('.jpg','.jpeg','.png','.gif','.webp'):unresolved.append({'row':index+2,'error':'Unsupported image type'});continue
    if not dest.exists():dest.write_bytes(b)
    a={'path':'/assets/'+dest.name,'role':'derivative','derivative_type':'official Excel embedded preview','sha256':sha,'bytes':len(b),'original_status':'Not transferred','workbook_sha256':batch,'workbook_member':target,'workbook_row':index+2,'source_url':records[code]['source_url']}
    r=records[code];r.setdefault('images',[])
    if any(x.get('sha256')==sha for x in r['images']):unchanged+=1
    else:r['images'].append(a);server.save(c,r,'workbook preview ingest');added+=1
    manifest.append({'record_id':code,**a})
  c.commit()
 report={'workbook':path.name,'workbook_sha256':batch,'metadata_rows':len(rows),'previews_found':len(manifest),'added':added,'unchanged':unchanged,'unresolved':unresolved,'files':manifest}
 out=server.ROOT/'data'/(path.stem+'-previews.json');out.write_text(json.dumps(report,indent=2));return {k:v for k,v in report.items() if k!='files'}
if __name__=='__main__':server.init();print(json.dumps(extract(pathlib.Path(sys.argv[1])),indent=2))
