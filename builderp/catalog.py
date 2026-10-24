"""Fold scope-level aliases into independently specified product items."""
import re
from .domain import normalize,assess_product
STOP={'a','an','and','all','each','for','in','of','or','the','to','type','with'}
def singular(word): return word[:-1] if len(word)>=4 and word.lower().endswith('s') and not word.lower().endswith('ss') else word
def terms(name): return [singular(w) for w in re.split('[^a-z0-9]+',normalize(name)) if len(w)>1 and w not in STOP]
def alternatives(name): return [s.strip() for s in re.split(r'\s+or\s+|\s+/\s+',re.sub(r'\([^)]*\)',' ',name),flags=re.I) if s.strip()]
def heads(name): return {terms(a)[-1] for a in alternatives(name) if terms(a)}
def family_score(scope,detail):
    shared_heads=heads(scope)&heads(detail)
    shared=set(terms(scope))&set(terms(detail))
    return len(shared) if shared_heads and len(shared)>len(shared_heads) else 0

def canonical_name(label):
    return ' / '.join(' '.join(a.split()[:-1]+[singular(a.split()[-1])]) for a in alternatives(re.sub(r'[:.]\s*$','',label)))

def consolidate(products,excluded,evidence,h):
    known={e['id']:e for e in evidence}; positions={e['id']:i for i,e in enumerate(evidence)}
    def blocks(p): return [known[i] for i in p['evidenceIds'] if i in known]
    def is_scope(p): return bool(blocks(p)) and all(e.get('part')=='1' for e in blocks(p))
    detailed=[dict(p) for p in products if not is_scope(p)]; scope=[p for p in products if is_scope(p)]; synthesized=[]; merged=[]; headings=[]
    for e in evidence:
        match=re.match(r'^(?:[A-Z]|\d{1,2})[.)]\s+(.{3,80}?)\s*:\s*$',e['text'].strip()) if e.get('part')=='2' else None
        if match and not re.search(r'[.;]',match[1]) and len(match[1].split())<=8 and not re.search(r'^(?:provide|furnish|install|submit)\b|\b(?:not used|not applicable|n/a|manufacturers?|general|requirements?|description|performance|quality|execution|following)\b',match[1],re.I): headings.append((e,match[1]))
    def within(clause,owner): return bool(owner) and (clause==owner or clause.startswith(owner+'.'))
    def fold(source,targets):
        for target in targets: target['aliases']=target.get('aliases',[])+[{k:source.get(k,'') for k in ('id','name','page','clause')}]+source.get('aliases',[])
        names=', '.join(t['name'] for t in targets)
        merged.append(dict(source,selectable=False,mergedInto=names,classificationReason=f'Merged into {names}, the item specified in the product article.'))
    for source in scope:
        targets=[p for p in detailed+synthesized if family_score(source['name'],p['name'])]
        if not targets:
            matches=sorted([(family_score(source['name'],label),e,label) for e,label in headings],key=lambda x:x[0],reverse=True)
            if matches and matches[0][0]:
                _,e,label=matches[0]; name=canonical_name(label)
                assessment=assess_product(dict(name=name,quote=e['text'],entityType='product',usageStatus=source.get('usageStatus') or 'required',catalogRole='standalone_item',condition=source.get('condition')),[e])
                if assessment['selectable']:
                    description=''
                    for child in evidence[positions[e['id']]+1:]:
                        if not within(child.get('clause',''),e.get('clause','')) or len(description)>=240: break
                        description=(description+' '+child['text']).strip()
                    p=dict(name=name,description=description[:240]+('…' if len(description)>240 else ''),group=(e.get('article','')+' '+e.get('articleTitle','')).strip() or 'Unresolved source group',clause=e.get('clause',''),evidenceIds=[e['id']],quote=e['text'],id=h+':heading:'+e['id'],page=e['page'],section=e['section'],docId=source.get('docId'),**assessment)
                    synthesized.append(p); targets=[p]
        if targets: fold(source,targets)
    for host in synthesized:
        for detail in detailed[:]:
            if heads(detail['name']) and heads(detail['name'])<=heads(host['name']) and any(within(e.get('clause',''),host['clause']) for e in blocks(detail)):
                detailed.remove(detail); fold(detail,[host])
    merged_ids={p['id'] for p in merged}
    included=[p for p in scope if p['id'] not in merged_ids]+detailed+synthesized
    included.sort(key=lambda p:min((positions.get(i,10**9) for i in p['evidenceIds']),default=10**9))
    return included,excluded+merged

def suggestions(requirement,products):
    named=requirement.get('products',[])
    query=' '.join(requirement.get(k,'') for k in ('title','text','quote'))
    scores={}
    for p in products:
        if p.get('selectable') is False and p['id'] not in requirement.get('productIds',[]): continue
        score=0
        for label in [p['name']]+[a['name'] for a in p.get('aliases',[])]:
            for name in named:
                n,l=normalize(name),normalize(label); related=family_score(name,label)
                if n==l: score=max(score,100)
                elif related and (n in l or l in n): score=max(score,80+related)
                elif related>=2: score=max(score,60+related)
            related=family_score(query,label)
            shared=len(set(terms(query))&set(terms(label)))
            if score<60 and related and shared>=2: score=max(score,20+shared+related)
        if score: scores[p['id']]=score
    return scores
