import io
import json
import re
from datetime import datetime, timezone
from openpyxl import Workbook
from .services import project_detail, parsed_for
from .store import AppError
from .domain import validate_requirement

def export_project(pid,uid,format='json',approved=False,selected=()):
    data=project_detail(pid,uid); documents=data['documents']; requirements=data['requirements']
    if not documents: raise AppError(409,'Upload a document before exporting.')
    chosen=set(selected)
    if chosen:
        rows=[r for r in requirements if r['id'] in chosen]
        if len(rows)!=len(chosen) or (approved and any(r['status']!='approved' for r in rows)): raise AppError(422,'Select valid approved rows from this project.')
    else:
        if approved:
            incomplete=any(d['doc_kind']=='spec' and ((d['log_version']!=7 and d['status']!='ready') or (not any(r['docId']==d['id'] for r in requirements) and not d['empty_review'])) for d in documents)
            if incomplete or any(r['status'] not in ('approved','rejected') for r in requirements): raise AppError(409,'Review all requirements and confirm every empty specification before exporting an approved register.')
        rows=[r for r in requirements if not approved or r['status']=='approved']
    if approved:
        sources={d['id']:parsed_for(d) for d in documents}
        for r in rows:
            source=sources.get(r['docId'])
            if not source or validate_requirement(r,source['evidence'])['blocking']:
                raise AppError(409,'Approved export requires available, matching source evidence. Restore missing evidence or review affected rows.')
    headers=['Source file','Section','Clause','Page','Type','Title','Requirement','Condition','Products','Status','Warnings','Evidence IDs','Source quote','Review note']
    matrix=[[r.get('filename',''),r.get('section',''),r.get('clause',''),r.get('page',1),r['type'],r['title'],r['text'],r.get('condition',''),'; '.join(r['products']),r['status'],'; '.join(r.get('blocking',[])+r.get('warnings',[])),', '.join(r['evidenceIds']),r['quote'],r.get('reviewNote','')] for r in rows]
    files=[]; source_rows=[['Source file','Evidence ID','Page','Section','Clause','Text','Method']]
    for d in documents:
        evidence=(parsed_for(d) or {}).get('evidence',[])
        for e in evidence: source_rows.append([d['filename'],e['id'],e['page'],e['section'],e.get('clause',''),e['text'],e['source']])
        sections=list(dict.fromkeys(r['section'] for r in rows if r['docId']==d['id']))
        files.append(dict(id=d['id'],filename=d['filename'],status=d['status'],pages=d['pages'],emptyReview=d['empty_review'],sections=[dict(section=s,requirements=[r for r in rows if r['docId']==d['id'] and r['section']==s]) for s in sections],evidence=evidence))
    summary=[['File','Status','No-submittals review']]+[[d['filename'],d['status'],d['empty_review'] or 'Not confirmed'] for d in documents]
    state='approved' if approved else 'draft'; name=re.sub(r'[^a-zA-Z0-9_-]','_',data['project']['name'])[:80]+'_'+state
    if format=='json':
        content=json.dumps(dict(schemaVersion='1.0',exportStatus=state,exportedAt=datetime.now(timezone.utc).isoformat(),project={k:data['project'][k] for k in ('name','description')},documents=files),ensure_ascii=False,indent=2).encode(); mime='application/json'
    elif format=='md':
        def md(s): return str(s).replace('|','\\|').replace('\n',' ').replace('\r',' ')
        text=f'# {md(data["project"]["name"])} — {state.title()} register\n\n'
        text+='Human-reviewed register.\n\n' if approved else 'Draft: includes unresolved findings. See row statuses and warnings.\n\n'
        text+='| '+' | '.join(headers)+' |\n| '+' | '.join('---' for _ in headers)+' |\n'
        text+='\n'.join('| '+' | '.join(md(v) for v in r)+' |' for r in matrix)
        content=text.encode(); mime='text/markdown'
    elif format=='xlsx':
        wb=Workbook(); wb.remove(wb.active)
        for title,table in [('Submittal register',[headers]+matrix),('Source evidence',source_rows),('Processing status',summary)]:
            sheet=wb.create_sheet(title)
            for values in table:
                sheet.append([re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(v or ''))[:32767] for v in values])
                for cell in sheet[sheet.max_row]: cell.data_type='s'  # source text must never become an Excel formula
            sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
            for cell in sheet[1]:
                from openpyxl.styles import Font,PatternFill
                cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='153B35')
                sheet.column_dimensions[cell.column_letter].width=25
        out=io.BytesIO(); wb.save(out); content=out.getvalue(); mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else: raise AppError(400,'Unsupported export format.')
    return content,mime,f'{name}.{format}'
