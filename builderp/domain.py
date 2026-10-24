"""Evidence, specification structure, and review rules ported to Python."""
import re
import unicodedata
from .store import AppError, sha

TYPES=('product_data','shop_drawings','sample','certification','test_report','quality_control','operation_and_maintenance','closeout','warranty','other')

def normalize(s): return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(s))).strip().lower()
def source_key(s): return re.sub(r'[\s\u00ad\u2010\u2011\u2012\u2013-]+','',normalize(s))
def source_includes(source,quote): return bool(source_key(quote)) and source_key(quote) in source_key(source)

def string(value,label,maximum,minimum=0):
    if not isinstance(value,str) or not minimum<=len(value)<=maximum: raise AppError(400,f'Invalid {label}.')
    return value

def requirement_input(b):
    if not isinstance(b,dict): raise AppError(400,'Invalid requirement.')
    result={k:string(b.get(k,''),k,limit,minimum) for k,limit,minimum in [('title',240,1),('text',6000,1),('quote',6000,1)]}
    result['condition']=string('' if b.get('condition') is None else b['condition'],'condition',1500)
    if b.get('type') not in TYPES: raise AppError(400,'Invalid requirement type.')
    result['type']=b['type']
    ids=b.get('evidenceIds')
    if not isinstance(ids,list) or not 1<=len(ids)<=30 or any(not isinstance(x,str) for x in ids): raise AppError(400,'Select 1–30 evidence IDs.')
    result['evidenceIds']=list(dict.fromkeys(ids))
    products=[] if b.get('products') is None else b['products']
    if not isinstance(products,list): raise AppError(400,'Products must be a list.')
    names=[x if isinstance(x,str) else x.get('name','') if isinstance(x,dict) else '' for x in products]
    result['products']=[x.strip() for x in names if isinstance(x,str) and x.strip() and len(x.strip())<=200][:60]
    return result

def validate_requirement(r,evidence):
    known={e['id']:e for e in evidence}
    ids=r['evidenceIds']; blocks=[known.get(i,{}) for i in ids]
    source=' '.join(e.get('text','') for e in blocks)
    blocking=[]; warnings=[]
    if not ids or any(i not in known for i in ids): blocking.append('Select valid source evidence for this requirement.')
    if not source_includes(source,r['quote']) or len(normalize(r['quote']))<8: blocking.append('The evidence quote must match the selected source text.')
    if any(e.get('source')=='ocr' for e in blocks): warnings.append('OCR text: compare this requirement with the original page.')
    if r.get('condition') and not source_includes(source,r['condition']): warnings.append('Confirm the condition against the original wording.')
    warnings.extend('Product selection needs source verification: '+p for p in r['products'] if not source_includes(source,p))
    return {'blocking':blocking,'warnings':warnings}

def build_batches(evidence,budget=6000):
    batches=[]; batch=[]; size=0
    for e in evidence:
        cost=len(e['text'])+100
        if batch and size+cost>budget: batches.append(batch); batch=[]; size=0
        batch.append(e); size+=cost
    if batch: batches.append(batch)
    return batches

def correct_sections(doc):
    headings={}
    pattern=r"^(?:SECTION\s+)?(\d{5,6}|\d{2}[ -]\d{2}[ -]\d{2})(?:\s*[-–—:]?\s+([A-Z][A-Z0-9 &/()'’–—-]*))?$"
    for e in doc['evidence']:
        text=e['text'].strip(); match=re.match(pattern,text); box=e.get('box')
        if not match or (box and .3<box[1]<.85): continue
        if not text.startswith('SECTION ') and not match[2] and (not box or .15<box[1]<.85): continue
        headings.setdefault(e['page'],re.sub(r'[ -]','',match[1]))
    current=headings[min(headings)] if headings else 'Unresolved'; pages={}
    for page in range(1,doc['pages']+1):
        current=headings.get(page,current); pages[page]=current
    evidence=[dict(e,section=pages.get(e['page'],'Unresolved')) for e in doc['evidence']]
    decimal=re.compile(r'^(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)\s+(.+)$')
    measurement=re.compile(r'^(?:\d+(?:\.\d+)?\s+)?(?:[.\d]|BTU\b|W/|kg\b|mm\b|in\b|LEED\b|version\b)',re.I)
    def heading_title(s): return re.search('[a-z]',s,re.I) and not measurement.search(s) and not re.search(r'[.;]$',s) and len(s)<180
    anchors=[e for e in evidence if (m:=decimal.match(e['text'])) and heading_title(m[2]) and (e.get('bold') or m[2]==m[2].upper())]
    section=part=article=title=paragraph=item=context=''; article_x=None
    for e in evidence:
        if e['section']!=section:
            section=e['section']; part=article=title=paragraph=item=context=''; article_x=None
        text=e['text'].strip(); p=re.match(r'^PART\s+(\d+)\s*[-–—:]?\s*(.*)$',text,re.I); a=decimal.match(text)
        same=any(x['section']==e['section'] and x.get('box') and e.get('box') and abs(x['box'][0]-e['box'][0])<.018 and (not x.get('fontSize') or not e.get('fontSize') or abs(x['fontSize']-e['fontSize'])<1) for x in anchors)
        is_heading=a and heading_title(a[2]) and not a[1].startswith('0.') and (not part or a[1].split('.')[0]==part) and (e.get('bold') or a[2]==a[2].upper() or (same and a[2][0].isupper() and len(a[2].split())<12))
        e.pop('structuralWarning',None)
        if p: part=p[1]; article=title=paragraph=item=context=''; article_x=None
        elif is_heading:
            if article!=a[1]: paragraph=item=context=''
            article,title=a[1],a[2]; article_x=e['box'][0] if e.get('box') else None
        else:
            if a and not measurement.search(a[2]): e['structuralWarning']='Unconfirmed decimal heading; retained the preceding article.'
            if re.match(r'^(?:(?:approved|acceptable) )?manufacturers?\s*[:.]?$',text,re.I): context=text
            letter=re.match(r'^([A-Z])\.\s+(.+)',text); child=re.match(r'^(\d+)[.)]\s+(.+)',text)
            if letter and article and (article_x is None or not e.get('box') or e['box'][0]>=article_x-.01): paragraph=letter[1]; item=''; context=letter[2]
            elif child and paragraph: item=child[1]
        e.update(clause='.'.join(x for x in [article,paragraph,item] if x),article=article,articleTitle=title,part=part,context=context)
    return dict(doc,evidence=evidence,sections=list(dict.fromkeys(e['section'] for e in evidence)),structureVersion='layout-2')

SUBMITTAL=re.compile(r'^(?:supplemental\s+)?(?:action|informational|quality\s+control|closeout)?\s*submittals?\s*$|^(?:operation\s+and\s+)?maintenance\s+data$',re.I)
PRODUCT=re.compile(r'\b(?:products?|materials?|systems?|equipment|fabrication|fabricated|manufacturers?|assembl(?:y|ies))\b',re.I)

def tag_evidence(evidence):
    eligible=[]; checklist=False; section=''
    for e in evidence:
        match=re.match(r'^\s*SECTION\s+(\d[\d ]{3,10})',e['text'],re.I)
        if match and re.sub(r'\s','',match[1])!=section: section=re.sub(r'\s','',match[1]); checklist=False
        heading=re.sub(r'^\s*(?:\d+(?:\.\d+)*|[A-Z])[.)]?\s+','',e['text']).strip()
        if re.match(r'^(?:submittals?\s+(?:check\s*list|schedule|register|log|index)|check\s*list\s+(?:of\s+|for\s+)?submittals|list\s+of\s+submittals)\s*[:–—-]?$',heading,re.I): checklist=True
        if not checklist: eligible.append(dict(e))
    active=''; inside=False; ids=set()
    for e in eligible:
        text=e['text'].strip(); heading=re.sub(r'^\s*(?:\d+(?:\.\d+)*|[A-Z])[.)]?\s+','',text)
        if re.match(r'^(?:PART\s+\d+|SECTION\s+\d)',text,re.I): inside=False; active=''
        if SUBMITTAL.search(e.get('articleTitle','')) or SUBMITTAL.search(heading): inside=True; active=e.get('article',''); ids.add(e['id']); continue
        if inside:
            a=e.get('article','')
            if active and a and a!=active and not a.startswith(active+'.'): inside=False; active=''
            else: ids.add(e['id'])
    for e in eligible:
        e['role']='submittal' if e['id'] in ids else 'product' if e.get('part')=='2' or PRODUCT.search(e.get('articleTitle','')) or PRODUCT.search(e['text']) else 'other' if ids else 'submittal'
    return eligible,ids or {e['id'] for e in eligible}

def preamble(r):
    text=normalize(' '.join(r.get(k,'') for k in ('title','text','quote')))
    if re.search(r'\b(?:as detailed below|as follows|as listed below|as specified below|the following (?:submittals?|items?|requirements?))\b',text): return True
    return r['type']=='other' and bool(re.search(r'\b(?:provide|submit|furnish)\s+submittals?\b',text)) and not re.search(r'product data|shop drawings?|samples?|certificat|test reports?|test results?|calculations?|warrant|closeout|maintenance|quality control submittal',text)

def same_requirement(a,b):
    return a['type']==b['type'] and (normalize(a['text'])==normalize(b['text']) or normalize(a['quote'])==normalize(b['quote']))

def assess_product(p,source):
    name=normalize(p['name']); quote=normalize(p['quote']); escaped=re.escape(name)
    entity=p.get('entityType') or 'unclear'; usage=p.get('usageStatus') or 'unclear'; role=p.get('catalogRole') or 'unclear'; reason=''
    labels=' '.join(normalize(e.get(k,'')) for e in source for k in ('articleTitle','context'))
    def same_label(label):
        label=normalize(label)
        return label and len(label)<140 and len(label.split())<=14 and not re.search(r'[.;]|\b(?:shall|must|provide|furnish|supply|install|test|inspect|clean|construct|fabricate|include|including|with)\b',label) and (name in label or label in name)
    label_match=any(same_label(e.get(k,'')) for e in source for k in ('context','articleTitle'))
    direct=bool(re.search(r'\b(?:provide|furnish|supply|procure)\s+(?:(?:an?|the)\s+)?'+escaped+r'\b',quote))
    if re.search(r'\b(?:incorporated|inc\.?|llc|ltd\.?|corporation)\s*$',name) or (re.fullmatch(r'(?:approved |acceptable )?manufacturers?\s*[:.]?',labels.strip()) and not re.search(r'manufactured by|product|material|valve|duct|pipe',quote)): entity='manufacturer'; role='attribute'; reason='Manufacturer names are attributes, not products.'
    if re.match(r'^(?:ASTM|ANSI|ISO|ASHRAE|SMACNA|NFPA)\s+[\w.-]+$',p['name'],re.I): entity='standard'; role='attribute'; reason='Reference standard, not a catalog product.'
    components=r'body|housing|casing|shell|frame|plate|seat|disc|stem|shaft|cover|liner|lining|coating|finish|gasket|seal|trim|wearing plate|striking plate|impeller|wheel|blade|tube|sheet|jacket'
    construction=re.search(r'\b(?:construct(?:ed)?|fabricat(?:e|ed)|made|form(?:ed)?|manufactur(?:e|ed)|compos(?:e|ed)|consist(?:s|ing)?)\s+(?:primarily\s+)?(?:of|from|with)\s+[^.;:]{0,100}\b'+escaped+r'\b',quote)
    material=re.search(r'\b(?:'+components+r')\s+(?:(?:shall|must)\s+be\s+|is\s+|are\s+|of\s+|:\s*)'+escaped+r'\b',quote) or re.search(r'\b'+escaped+r'\s+(?:[a-z][\w-]*\s+){0,2}(?:'+components+r')\b',quote)
    if construction or material or (entity=='material' and re.search(r'\b(?:shall be|must be|is|are)\s+'+escaped+r'\b',quote)):
        role='constituent_material' if entity=='material' else 'integral_component'; reason='Described within another product’s construction.'
    elif not direct and not label_match and (role=='integral_component' or re.search(r'\b(?:with|including|includes|equipped with|complete with|having|incorporating)\s+[^.;:]{0,80}\b'+escaped+r'\b',quote)):
        role='integral_component'; reason='Not separately specified as an independent item.'
    if re.search(r'\b(?:accessories|assemblies|components|equipment|items|materials|products|work)$',name) or re.search(r'^(?:all|assembled|associated|complete|entire|general|miscellaneous|related|various)\b|\b(?:associated with|related to|required for|used with|in connection with)\b',name): role='generic_reference'; reason='Generic or collective scope reference.'
    if re.match(r'^(?:(?:hydrostatically|visually|thoroughly|properly)\s+)?(?:test|inspect|clean|adjust|balance|commission|flush|maintain|operate|paint|protect|service|start|install|erect|apply|connect|mount|place|set)\b',quote) and re.search(r'\b'+escaped+r'\b',quote) and not re.search(r'\b(?:provide|furnish|supply|procure)\b',quote) and not label_match: role='generic_reference'; reason='Mentioned only as an installation or maintenance object.'
    if role=='unclear' and (direct or label_match):
        role='standalone_item'
        if 'entityType' not in p: entity='product'
        if 'usageStatus' not in p and direct: usage='required'
    if re.search(r'(?:^|[.;]\s*)'+escaped+r'\s+(?:(?:shall|must)\s+not\s+(?:be\s+)?(?:used|provided|installed|permitted)|(?:is|are)\s+(?:not\s+permitted|prohibited))|\bdo not (?:use|install|provide)\s+'+escaped+r'\b',quote): usage='conditional' if re.search(r'\b(?:unless|except|only if)\b',quote) else 'prohibited'
    if usage not in ('unclear','prohibited') and (p.get('usageStatus')=='conditional' or re.search(r'\b(?:unless|only where|only if|where indicated|when specified)\b',quote)): usage='conditional'
    selectable=entity in ('product','material') and role=='standalone_item' and usage in ('required','permitted','conditional')
    return dict(entityType=entity,usageStatus=usage,catalogRole=role,condition=(p.get('condition') or p['quote']) if usage=='conditional' else '',selectable=selectable,classificationReason=reason or ('' if selectable else 'Standalone product status or permitted usage could not be verified.'))

def parse_extraction(raw,evidence,allowed,hash_value):
    if not isinstance(raw,dict) or not isinstance(raw.get('requirements'),list) or not isinstance(raw.get('products'),list) or len(raw['requirements'])>100 or len(raw['products'])>150: raise AppError(502,'AI returned invalid extraction data. Retry this batch.','invalid_extraction')
    known={e['id']:e for e in evidence}; requirements=[]; products=[]; omitted=0
    for value in raw['requirements']:
        try: r=requirement_input(value)
        except AppError as exc: raise AppError(502,str(exc),'invalid_extraction') from exc
        if preamble(r) or not set(r['evidenceIds']) & allowed: continue
        e=next((known[i] for i in r['evidenceIds'] if i in known),{})
        r.update(id=sha(r['type']+':'+normalize(r['quote'])),section=e.get('section','Unresolved'),clause=e.get('clause',''),page=e.get('page',1),status='needs_review',**validate_requirement(r,evidence))
        if not any(same_requirement(r,x) for x in requirements): requirements.append(r)
    for value in raw['products']:
        try:
            if not isinstance(value,dict): raise ValueError()
            p=dict(value); string(p.get('name'),'product name',200,1)
            ids=p.get('evidenceIds');
            if not isinstance(ids,list) or not 1<=len(ids)<=30 or any(not isinstance(i,str) or i not in known for i in ids): raise ValueError()
            blocks=[known[i] for i in ids]; source=' '.join(e['text'] for e in blocks)
            quote=p.get('quote') or ''
            if not isinstance(quote,str): raise ValueError()
            if len(normalize(quote))<8 and source_includes(source,p['name']) and (not quote or source_includes(source,quote)): quote=source
            if not 8<=len(quote)<=6000 or not source_includes(source,quote) or not source_includes(source,p['name']): raise ValueError()
            p['quote']=quote
            p.update(assess_product(p,blocks)); e=blocks[0]
            p.update(id=sha(hash_value+':'+e['section']+':'+(e.get('article') or e['id'])+':'+normalize(p['name'])),page=e['page'],section=e['section'],clause=e.get('clause',''),group=(e.get('article','')+' '+e.get('articleTitle','')).strip() or 'Unresolved source group',description=p.get('description') or '')
            products.append(p)
        except (AppError,ValueError,TypeError,KeyError): omitted+=1
    return {'requirements':requirements,'products':products,'omittedProducts':omitted}
