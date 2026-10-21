#!/usr/bin/env python3
"""Retain a completed official per-file Chrome download against its source manifest."""
import datetime,hashlib,json,os,pathlib,plistlib,re,subprocess,sys,urllib.parse
import server,ingest_asset_details

def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

class PendingDownload(Exception):pass

def origin_metadata(path,asset,required=False):
 result=subprocess.run(['/usr/bin/xattr','-px','com.apple.metadata:kMDItemWhereFroms',str(path)],capture_output=True)
 if result.returncode:
  if required:raise PendingDownload('Finalized download origin metadata is unavailable')
  return None
 try:urls=plistlib.loads(bytes.fromhex(result.stdout.decode()))
 except (ValueError,plistlib.InvalidFileException):
  if required:raise ValueError('Invalid download origin metadata')
  return None
 expected={str(asset.get('guid','')).lower(),str(asset.get('download_guid','')).lower()}-{''}
 endpoint_seen=False
 for url in urls:
  parsed=urllib.parse.urlparse(url);query=urllib.parse.parse_qs(parsed.query)
  if parsed.scheme!='https' or parsed.hostname!='ballyheritage.com' or parsed.path!='/DigProd/AssetContent/DownloadAsset':continue
  endpoint_seen=True;guid=query.get('assetGuid',[''])[0];workgroup=query.get('workgroupId',[''])[0]
  if guid.lower() in expected and workgroup=='99':
   return {'host':parsed.hostname,'path':parsed.path,'asset_guid':guid,'workgroup_id':workgroup,'source_url':urllib.parse.urlunparse(('https',parsed.netloc,parsed.path,'',urllib.parse.urlencode({'assetGuid':guid,'workgroupId':workgroup}),''))}
 if endpoint_seen or required:raise ValueError('Download origin does not match the selected Bally asset identity')
 return None

def ingest(receipt_path):
 receipt_bytes=receipt_path.read_bytes();evidence_sha=hashlib.sha256(receipt_bytes).hexdigest();r=json.loads(receipt_bytes)
 if r.get('provider') not in ('Chrome completed download event','Chrome finalized download with verified origin metadata') or not r.get('completed_at'):raise ValueError('Completed browser download evidence required')
 manifest_name=r['source_manifest']
 if pathlib.Path(manifest_name).name!=manifest_name or not re.fullmatch(r'(?:media-batch-|direct-source-)\d+[-\w]*\.json',manifest_name):raise ValueError('Invalid source manifest reference')
 manifest=json.loads((server.ROOT/'data'/manifest_name).read_text());assets=manifest['assets']
 if len(assets)!=1 or assets[0]['guid']!=r['source_guid']:raise ValueError('Direct download identity is ambiguous')
 a=assets[0];source=pathlib.Path(r['completed_path']).resolve();expected=a['filename']
 if source.parent!=(pathlib.Path.home()/'Downloads').resolve() or source.name.endswith('.crdownload'):raise ValueError('Unexpected download location or incomplete transfer')
 name=pathlib.Path(expected)
 if source.name!=expected and not re.fullmatch(re.escape(name.stem)+r' \(\d+\)'+re.escape(name.suffix),source.name):raise ValueError('Completed filename differs from source identity')
 before=source.stat()
 if not before.st_size:raise ValueError('Empty direct download')
 origin=origin_metadata(source,a,required=r['provider']=='Chrome finalized download with verified origin metadata')
 if r['provider']=='Chrome finalized download with verified origin metadata':
  requested=datetime.datetime.fromisoformat(r['requested_at'].replace('Z','+00:00')).timestamp()
  if not hasattr(before,'st_birthtime') or before.st_birthtime<requested-2:raise ValueError('Finalized file predates the recorded source request')
 with source.open('rb') as stream:
  head=stream.read(512).lstrip().lower()
 if name.suffix.lower() not in ('.html','.htm') and head.startswith((b'<!doctype html',b'<html',b'<head')):raise ValueError('Source returned an HTML response instead of the original file')
 folder=server.ROOT/'originals';folder.mkdir(exist_ok=True);tmp=folder/('pending-direct-'+evidence_sha+'.tmp');h=hashlib.sha256();size=0
 with source.open('rb') as stream,tmp.open('wb') as out:
  for block in iter(lambda:stream.read(1024*1024),b''):h.update(block);out.write(block);size+=len(block)
 after=source.stat()
 if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino) or size!=before.st_size:raise ValueError('Download changed during retention')
 sha=h.hexdigest();dest=folder/sha[:2]/sha;dest.parent.mkdir(exist_ok=True)
 if dest.exists():
  if digest(dest)!=sha:raise ValueError('Existing retained original differs')
  tmp.unlink()
 else:tmp.replace(dest)
 if digest(dest)!=sha:raise ValueError('Retained direct-file checksum mismatch')
 image_info={}
 if name.suffix.lower() in ('.jpg','.jpeg','.png','.tif','.tiff'):
  try:
   from PIL import Image
   with Image.open(dest) as image:image_info={'format':image.format,'width':image.width,'height':image.height};image.verify()
  except ImportError:pass
  except Exception as error:image_info={'format_warning':str(error)[:500]}
 evidence_name=str(receipt_path.relative_to(server.ROOT/'data'))
 transport_proof='completed Chrome download event' if r['provider']=='Chrome completed download event' else 'finalized Chrome file with matching asset GUID and workgroup in download-origin metadata, created after the recorded request'
 asset={**a,'sha256':sha,'bytes':size,'storage_key':str(dest.relative_to(server.ROOT)),'role':'original','version':None,'record_ids':[],'inventory_numbers':[],'captured_at':server.now(),'source_url':manifest.get('source_url'),'source_manifest':manifest_name,'source_download_kind':'official per-file browser download','source_download_evidence':evidence_name,'source_download_evidence_sha256':evidence_sha,'source_download_origin':origin,'image_info':image_info,'verification':'Official per-file source download; '+transport_proof+'; filename matched source GUID manifest; retained bytes SHA-256 verified. Remote expected checksum unavailable.'}
 with server.connect() as c:
  old=c.execute('SELECT body FROM media_assets WHERE guid=?',(asset['guid'],)).fetchone()
  if old:
   prior=json.loads(old[0]);asset={**prior,**asset,'record_ids':prior.get('record_ids',[]),'inventory_numbers':prior.get('inventory_numbers',[])}
  asset=ingest_asset_details.enrich(c,asset)
  c.execute('INSERT OR REPLACE INTO originals VALUES(?,?,?,?,?)',(sha,asset['storage_key'],size,expected,asset['guid']))
  c.execute('INSERT OR REPLACE INTO media_assets VALUES(?,?)',(asset['guid'],json.dumps(asset,ensure_ascii=False)))
  c.execute('INSERT INTO audit(at,action,record_id,body) VALUES(?,?,?,?)',(server.now(),'verified original direct file ingest',asset['guid'],json.dumps({'source_manifest':manifest_name,'receipt':receipt_path.name,'receipt_sha256':evidence_sha,'sha256':sha,'bytes':size})));c.commit()
 report={'originals':1,'guid':asset['guid'],'sha256':sha,'bytes':size,'filename_match':True,'retained_checksum_verified':True,'image_info':image_info}
 reports=server.ROOT/'data'/'direct-original-verifications';reports.mkdir(exist_ok=True);(reports/(evidence_sha+'.json')).write_text(json.dumps(report,indent=2));print(json.dumps(report))

def ingest_request(request_path,download_index):
 r=json.loads(request_path.read_text());manifest_name=r['source_manifest']
 if pathlib.Path(manifest_name).name!=manifest_name:raise ValueError('Invalid request manifest reference')
 manifest=json.loads((server.ROOT/'data'/manifest_name).read_text());assets=manifest['assets']
 if len(assets)!=1 or assets[0]['guid']!=r['source_guid']:raise ValueError('Request identity is ambiguous')
 a=assets[0]
 with server.connect() as c:
  if c.execute('SELECT 1 FROM media_assets WHERE guid=?',(a['guid'],)).fetchone():print(json.dumps({'already_verified':True,'guid':a['guid']}));return
 requested=datetime.datetime.fromisoformat(r['requested_at'].replace('Z','+00:00')).timestamp();matches=[]
 for path in set(download_index.get(a['filename'],[])):
  st=path.stat()
  if not hasattr(st,'st_birthtime') or st.st_birthtime<requested-2 or not st.st_size:continue
  try:proof=origin_metadata(path,a,required=True)
  except PendingDownload:continue
  except ValueError:continue
  matches.append((path,proof))
 if not matches:raise PendingDownload('No finalized source-bound file yet')
 if len(matches)!=1:raise ValueError('Multiple new files match the source identity; automatic assignment stopped')
 path,proof=matches[0];evidence={'provider':'Chrome finalized download with verified origin metadata','completed_at':server.now(),'requested_at':r['requested_at'],'source_manifest':manifest_name,'source_guid':a['guid'],'expected_filename':a['filename'],'completed_path':str(path),'source_tab_id':r.get('source_tab_id'),'origin':proof,'source_action':r.get('source_action'),'request_evidence':request_path.name}
 folder=server.ROOT/'data'/'direct-origin-finalization';folder.mkdir(exist_ok=True);key=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest();receipt_path=folder/(key+'.json');receipt_path.write_text(json.dumps(evidence,indent=2));ingest(receipt_path)

if __name__=='__main__':server.init();ingest(pathlib.Path(sys.argv[1]))
