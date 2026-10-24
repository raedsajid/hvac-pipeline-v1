from .store import write
import json
import re
from pathlib import Path
from .store import AppError, owned_project, all_rows, one, run, get_json, transaction, new_id, now,event
from .drawings import rows_key
from .services import parsed_for
MAP=json.loads(Path(__file__).with_name('equipment_map.json').read_text())
FAMILIES=[['EF','GEF','SA','TX'],['CUH','EUH','HWUH','ECUH','REC'],['P','HWP']]
TAG=re.compile(r'\b([A-Z]{1,6}\d{0,2})[-–—\s]*(\d{1,3})(?:\.(\d{1,3}))?(?:[-–—](\d{1,3}))?([A-Z]?)\b',re.I)

def expand_tags(value):
    text=str(value).strip().replace('–','-').replace('—','-')
    if re.search(r'\d{2,}[A-Z]|[A-Z]{2,}\d{4,}|CGM|F\d-',text,re.I): return []
    matches=list(TAG.finditer(text)); result=[]
    for i,m in enumerate(matches):
        prefix,num,decimal,sub,suffix=m.groups()
        result.append(f'{prefix.upper()}-{int(num)}'+(f'.{int(decimal)}' if decimal else '')+(f'-{int(sub)}' if sub else '')+(suffix.upper() if suffix else ''))
        if decimal or sub: continue
        trail=text[m.end():matches[i+1].start() if i+1<len(matches) else len(text)]
        follow=re.match(r'^([\s,;/&\d]+(?:\s*(?:and|&)\s*\d+)*)',trail,re.I)
        if follow and not re.search('[a-z]',follow[1],re.I): result.extend(f'{prefix.upper()}-{int(n)}' for n in re.findall(r'\d{1,3}',follow[1]))
    return list(dict.fromkeys(result))

def row_tags(row):
    preferred=[]; fallback=[]
    for key,value in row.items():
        if value is None or '%' in str(value): continue
        tags=expand_tags(value)
        if re.search(r'designation|unit\s*no\.?|^tag$|mark|equip|^no\.?$|sym|^unit$',key,re.I): preferred+=tags
        elif re.fullmatch(r'[A-Z]{1,6}\d{0,2}[-–—\s]?\d{1,3}(?:\.\d{1,3})?(?:[-–—]\d{1,3})?[A-Z]?',str(value),re.I): fallback+=tags
    return list(dict.fromkeys(preferred or fallback))

def candidates_for(types):
    hits={}
    for typ in types:
        family=next((f for f in FAMILIES if typ in f),[])
        for row in MAP:
            tags=[row['tag']] if row.get('tag') else row.get('tags',[])
            source='map' if typ in tags else 'family' if any(t in family for t in tags) else None
            spec=row.get('spec_number')
            if not source or not spec: continue
            old=hits.get(spec)
            hit={'spec_number':spec,'description':row.get('description'),'source':source,'support':row.get('support',1)}
            if not old or (source=='map' and old['source']!='map'): hits[spec]=hit
            elif old: old['support']=max(old['support'],hit['support'])
    return sorted(hits.values(),key=lambda c:(0 if c['source']=='map' else 1,-c['support'],c['spec_number']))

def match_rows(rows):
    results={}
    for entry in rows:
        payload=entry.get('row_payload') or entry; title=payload.get('schedule_title','')
        if re.search(r'load\s+summary|outdoor\s+air\s+requirement|air\s+requirement|ventilation\s+schedule.*nyc|nyc\s+mc',title,re.I): continue
        row=payload.get('row',{}); tags=row_tags(row); types=list(dict.fromkeys(t.split('-')[0] for t in tags))
        hits=[]
        if len(types)>1: hits=candidates_for(['/'.join(types)]) or candidates_for(['/'.join(reversed(types))])
        hits=hits or candidates_for(types); top=hits[0] if hits else {}
        band='no_match' if not hits else 'auto' if sum(c['source']=='map' for c in hits)==1 and top['source']=='map' else 'review'
        doc=payload.get('document',{}); meta=payload.get('schedule_metadata',{}); did=doc.get('drawing_id','')
        for tag in tags:
            item={'drawingDocId':did,'unitTag':tag,'sheetNumber':next((str(doc.get(k) or meta.get(k)) for k in ('sheet_number','drawing_number','erp_drawing_number','number') if doc.get(k) or meta.get(k)),''),'scheduleTitle':title,'tableId':payload.get('table_id',''),'rowIndex':payload.get('row_index',0),'band':band,'matchMethod':top.get('source'),'proposedCsi':top.get('spec_number'),'proposedDescription':top.get('description'),'selectedCsi':top.get('spec_number') if band=='auto' else None,'selectedDescription':top.get('description') if band=='auto' else None,'status':'proposed' if band=='auto' else 'needs_review','basisOfDesign':meta.get('basis_of_design'),'rawFields':row,'candidates':hits,'equipmentTypes':types}
            key=(did,tag); old=results.get(key)
            if not old or sum(v not in (None,'',[]) for v in row.values())>sum(v not in (None,'',[]) for v in old['rawFields'].values()): results[key]=item
    return list(results.values())

COLUMNS={'drawingDocId':'drawing_doc_id','unitTag':'unit_tag','sheetNumber':'sheet_number','scheduleTitle':'schedule_title','tableId':'table_id','rowIndex':'row_index','band':'band','matchMethod':'match_method','proposedCsi':'proposed_csi','proposedDescription':'proposed_description','selectedCsi':'selected_csi','selectedDescription':'selected_description','status':'status','basisOfDesign':'basis_of_design','rawFields':'raw_fields','candidates':'candidates','equipmentTypes':'equipment_types'}
JSON_FIELDS={'rawFields','candidates','equipmentTypes'}

def list_log(pid,uid):
    owned_project(pid,uid); log=one('SELECT * FROM equipment_logs WHERE project_id=?',pid); items=[]; catalog={}
    if log:
        for row in all_rows('SELECT * FROM equipment_log_items WHERE log_id=? ORDER BY schedule_title,unit_tag',log['id']):
            items.append(dict(id=row['id'],revision=row['revision'],updated=row['updated'],**{k:json.loads(row[v]) if k in JSON_FIELDS else row[v] for k,v in COLUMNS.items()}))
    for d in all_rows("SELECT * FROM project_docs WHERE project_id=? AND doc_kind='spec'",pid):
        for section in (parsed_for(d) or {}).get('sections',[]):
            if re.match(r'^\d{5,6}$',section): catalog[section]={'csi':section,'title':d['filename']}
    return {'log':log,'items':items,'counts':dict(total=len(items),**{b:sum(i['band']==b for i in items) for b in ('auto','review','no_match')}),'catalog':list(catalog.values())}

def generate_log(pid,user):
    owned_project(pid,user['id']); rows=[]
    for d in all_rows("SELECT * FROM project_docs WHERE project_id=? AND doc_kind='drawing'",pid):
        source=get_json(rows_key(d['id']))
        if source is None: source=get_json(f'cache/drawings-v1/{d["hash"]}/schedule-rows.json',{'rows':[]})
        for entry in source['rows']:
            payload=entry.get('row_payload') or entry
            rows.append(dict(payload,document=dict(payload.get('document',{}),drawing_id=d['id'])))
    if not rows: raise AppError(409,'Extract schedule rows from manual drawing crops first.')
    matches=match_rows(rows)
    with transaction():
        log=one('SELECT * FROM equipment_logs WHERE project_id=?',pid)
        if not log:
            lid=new_id(); write('INSERT INTO equipment_logs VALUES (?,?,0,?,?)',lid,pid,now(),now()); log=one('SELECT * FROM equipment_logs WHERE id=?',lid)
        prior={ (r['drawing_doc_id'],r['unit_tag']):r for r in all_rows('SELECT * FROM equipment_log_items WHERE log_id=?',log['id']) }
        # Preserve human decisions while rebuilding proposals.
        for m in matches:
            old=prior.get((m['drawingDocId'],m['unitTag']))
            if old and old['status'] in ('confirmed','rejected'): continue
            values=[json.dumps(m[k]) if k in JSON_FIELDS else m[k] for k in COLUMNS]
            if old: write('DELETE FROM equipment_log_items WHERE id=?',old['id'])
            cols=['id','log_id']+list(COLUMNS.values())+['revision','updated']
            write('INSERT INTO equipment_log_items('+','.join(cols)+') VALUES ('+','.join('?' for _ in cols)+')',old['id'] if old else new_id(),log['id'],*values,(old['revision']+1) if old else 0,now())
        write('UPDATE equipment_logs SET revision=revision+1,updated=? WHERE id=?',now(),log['id']); event(pid,f'Generated equipment log · {len(matches)} units')
    return list_log(pid,user['id'])

def patch_item(iid,user,b):
    row=one('SELECT i.*,l.project_id FROM equipment_log_items i JOIN equipment_logs l ON l.id=i.log_id JOIN projects p ON p.id=l.project_id WHERE i.id=? AND p.user_id=?',iid,user['id'])
    if not row: raise AppError(404,'Equipment item not found.')
    csi=b.get('selectedCsi',row['selected_csi']); description=b.get('selectedDescription',row['selected_description']); status=b.get('status',row['status'])
    if status not in ('needs_review','proposed','confirmed','rejected') or (csi is not None and (not isinstance(csi,str) or len(csi)>32)) or (description is not None and (not isinstance(description,str) or len(description)>240)): raise AppError(400,'Invalid equipment review.')
    if status=='confirmed' and not csi: raise AppError(422,'Select a CSI section before confirming.')
    if not run('UPDATE equipment_log_items SET selected_csi=?,selected_description=?,status=?,revision=revision+1,updated=? WHERE id=? AND revision=?',csi,description,status,now(),iid,b.get('revision')): raise AppError(409,'Equipment item changed. Reload before saving.')
    return list_log(row['project_id'],user['id'])
