"""Source-field-aware archive search, without matches against capture timestamps or storage paths."""
FACETS={'class':'CLASS','subcategory':'SUBCATEGORY','season':'Object Season','logo':'Logo','product_line':'Product Line','author':'Author','color':'Colors','gender':'Object Gender'}
def values(value):return [v.strip() for v in str(value or '').split(';') if v.strip()]
def facets(rows):
 result={k:sorted({v for r in rows for v in values(r.get('metadata',{}).get(field))},key=str.casefold) for k,field in FACETS.items()}
 result['tags']=sorted({t for r in rows for t in r.get('tags',[])},key=str.casefold);return result

def filter_records(rows,query):
 def get(key):return query.get(key,[''])[0].strip()
 text=get('q').casefold()
 if text:
  rows=[r for r in rows if text in ' '.join([r.get('id',''),r.get('title',''),r.get('notes','')]+[str(v) for v in r.get('metadata',{}).values()]+r.get('tags',[])).casefold()]
 for key in ('category','rights_status'):
  val=get(key)
  if val:rows=[r for r in rows if r.get(key)==val]
 for key,field in FACETS.items():
  val=get(key)
  if val:rows=[r for r in rows if val in values(r.get('metadata',{}).get(field))]
 if get('year'):rows=[r for r in rows if str(r.get('year_start','')).startswith(get('year'))]
 tags=values(get('tags'));all_tags=get('tag_mode')!='any'
 if tags:
  rows=[r for r in rows if (all(t in r.get('tags',[]) for t in tags) if all_tags else any(t in r.get('tags',[]) for t in tags))]
 for field,key,kind in [('id','code','begins'),('Description','description','contains')]:
  needle=get(key).casefold()
  if needle:
   mode=get(key+'_mode') or kind
   if mode not in ('contains','equals','begins','ends'):raise ValueError('Invalid search operator')
   def match(r):
    hay=(r.get('id','') if field=='id' else r.get('metadata',{}).get(field,'')).casefold()
    return {'contains':lambda:needle in hay,'equals':lambda:needle==hay,'begins':lambda:hay.startswith(needle),'ends':lambda:hay.endswith(needle)}[mode]()
   rows=[r for r in rows if match(r)]
 for key in ('date_from','date_to'):
  if get(key):
   try:year=int(get(key))
   except ValueError:raise ValueError('Date filters require a year')
   if not 1000<=year<=2200:raise ValueError('Year out of range')
   if key=='date_from':rows=[r for r in rows if (r.get('year_end') or r.get('year_start') or 0)>=year]
   else:rows=[r for r in rows if r.get('year_start') and r['year_start']<=year]
 order=get('sort') or 'id'
 if order not in ('id','title','year_asc','year_desc'):raise ValueError('Invalid sort order')
 if order in ('id','title'):rows=sorted(rows,key=lambda r:(str(r.get(order,'')).casefold(),r['id']))
 else:rows=sorted(rows,key=lambda r:(r.get('year_start') is None, (-(r.get('year_start') or 0) if order=='year_desc' else r.get('year_start') or 0),r['id']))
 return rows
