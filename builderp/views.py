import json
from flask import request,render_template,redirect,g,session,flash
from . import auth,services,ai,equipment,drawings,pdf,table_jobs
from .domain import TYPES
from .store import AppError,all_rows,one,owned_doc,owned_project,owned_requirement

def register_views(app,response_file):
    @app.context_processor
    def navigation():
        user=g.get('user')
        return dict(nav_projects=all_rows('SELECT id,name FROM projects WHERE user_id=? ORDER BY created DESC',user['id']) if user else [])

    @app.template_filter('display_date')
    def display_date(value):
        from datetime import datetime,timezone
        return datetime.fromtimestamp(value/1000,timezone.utc).strftime('%b %d, %Y') if value else ''

    @app.get('/')
    def index(): return redirect('/workspace' if g.user else '/login')

    @app.route('/login',methods=['GET','POST'])
    @app.route('/signup',methods=['GET','POST'])
    def login():
        signup=request.path=='/signup'
        if request.method=='POST':
            user,token=auth.authenticate(request.form.get('username'),request.form.get('password'),signup)
            session.clear(); return auth.cookie(redirect('/workspace'),token)
        return render_template('login.html',signup=signup)

    @app.post('/logout')
    def logout():
        auth.logout(); session.clear(); return auth.cookie(redirect('/login'),'')

    @app.route('/workspace',methods=['GET','POST'])
    def workspace():
        u=auth.require_user()
        if request.method=='POST': return redirect('/projects/'+services.create_project(u['id'],request.form)['id'])
        projects=all_rows('SELECT p.*,(SELECT count(*) FROM project_docs d WHERE d.project_id=p.id) document_count,(SELECT count(*) FROM requirements r JOIN project_docs d ON d.id=r.doc_id WHERE d.project_id=p.id) requirement_count FROM projects p WHERE user_id=? ORDER BY created DESC',u['id'])
        return render_template('workspace.html',projects=projects)

    @app.route('/settings',methods=['GET','POST'])
    def settings():
        u=auth.require_user()
        if request.method=='POST':
            if request.form.get('action')=='password':
                auth.change_password(u,request.form); session.clear(); return auth.cookie(redirect('/login'),'')
            if u['role']!='admin': raise AppError(403,'Only the owner can change shared AI settings.')
            key=request.form.get('apiKey','').strip()
            if key: ai.save_key(key)
            if request.form.get('action')=='test': ai.generate('Return JSON {"ok":true}.','Connection check.',u['id']); ai.embed(['BuildERP connection check'],u['id'])
            flash('AI settings saved.' if key else 'AI connection verified.'); return redirect('/settings')
        return render_template('settings.html',configured=ai.configured(),chat_model=ai.chat_model(),embedding_model=ai.embed_model())

    @app.get('/projects/<pid>')
    def project(pid):
        u=auth.require_user(); data=services.project_detail(pid,u['id'])
        tab=request.args.get('tab','specs')
        if tab not in ('specs','register','equipment','source','chat','activity','drawings'): tab='specs'
        if tab in ('source','chat') and data['documents']:
            return redirect('/documents/'+data['documents'][0]['id']+'?tab='+tab)
        return render_template('project.html',**data,project_tab=tab,equipment=equipment.list_log(pid,u['id']))

    @app.post('/projects/<pid>/upload')
    def upload(pid):
        u=auth.require_user(); files=[f for f in request.files.getlist('file') if f and f.filename]
        if not files: raise AppError(400,'Select at least one PDF.')
        kind=request.form.get('docKind','spec'); last=None
        for file in files:
            last=services.upload(u['id'],pid,file.filename,file.read(pdf.MAX_SIZE+1),kind)
        if len(files)>1:
            return redirect('/projects/'+pid+('?tab=drawings' if kind=='drawing' else '?tab=specs'))
        return redirect('/documents/'+last['docId']+('?tab=crops' if kind=='drawing' else ''))

    @app.post('/projects/<pid>/delete')
    def delete_project(pid):
        u=auth.require_user()
        if request.form.get('confirm')!='yes': raise AppError(400,'Confirm project deletion.')
        services.delete_project(pid,u['id']); return redirect('/workspace')

    @app.post('/projects/<pid>/equipment')
    def generate_equipment(pid):
        u=auth.require_user(); equipment.generate_log(pid,u); return redirect('/projects/'+pid+'#equipment')

    @app.post('/equipment/<iid>')
    def equipment_review(iid):
        u=auth.require_user(); b=dict(request.form)
        try: b['revision']=int(b.get('revision','-1'))
        except ValueError: raise AppError(400,'Invalid revision.')
        result=equipment.patch_item(iid,u,b); return redirect('/projects/'+result['log']['project_id']+'#equipment')

    @app.get('/documents/<did>')
    def document(did):
        u=auth.require_user(); d=owned_doc(did,u['id']); parsed=services.parsed_for(d)
        try: page=int(request.args.get('page','1'))
        except ValueError: raise AppError(400,'Invalid page.')
        page=max(1,min(d['pages'] or 1,page))
        tab=request.args.get('tab','source')
        if tab not in ('source','products','chat','crops'): tab='source'
        messages=all_rows('SELECT * FROM messages WHERE doc_id=? ORDER BY created LIMIT 200',did)
        for m in messages: m['citations']=json.loads(m['citations'])
        crop_data=drawings.crops(did,u['id']) if d['doc_kind']=='drawing' else None
        detection=table_jobs.state(did,u['id']) if crop_data else None
        edit=next((c for c in (crop_data or {}).get('crops',[]) if c['id']==request.args.get('edit')),None)
        if edit: page=edit['page']
        preview=request.args.get('regions','detected')
        if preview not in ('detected','saved'): preview='detected'
        choices=all_rows('SELECT id,filename,doc_kind FROM project_docs WHERE project_id=? ORDER BY created',d['project_id'])
        findings=[]
        for row in all_rows('SELECT id,data,status FROM requirements WHERE doc_id=? ORDER BY updated',did):
            item=json.loads(row['data']); item.update(id=row['id'],status=row['status']); findings.append(item)
        return render_template('document.html',document_choices=choices,findings=findings,detection=detection,edit=edit,preview=preview,selected_region=request.args.get('region',''),accepted_ids={c['id'] for c in (crop_data or {}).get('crops',[])},document=d,project=owned_project(d['project_id'],u['id']),parsed=parsed,evidence=[e for e in (parsed or {}).get('evidence',[]) if e['page']==page],page=page,tab=tab,catalog=services.catalog(did,u['id']),messages=messages,crop_data=crop_data)

    @app.post('/documents/<did>/action')
    def document_action(did):
        u=auth.require_user(); d=owned_doc(did,u['id']); action=request.form.get('action')
        if action=='prepare': result=services.prepare(did,u)
        elif action=='generate': result=services.generate_log(did,u)
        elif action=='index': result=services.index_step(did,u)
        elif action=='detect': result=table_jobs.detect_step(did,u['id'])
        elif action=='extract': result=drawings.extract_step(did,u)
        elif action=='confirm-empty': result=services.confirm_empty(did,u,request.form.get('note',''))
        elif action=='delete':
            if request.form.get('confirm')!='yes': raise AppError(400,'Confirm document deletion.')
            services.delete_document(did,u['id']); return redirect('/projects/'+d['project_id'])
        else: raise AppError(400,'Invalid action.')
        flash('Completed.' if result.get('done') or result.get('status')=='prepared' or result.get('ok') else f'Progress saved ({result.get("progress",0)}%). Continue to process the next batch.')
        return redirect('/documents/'+did+('?tab=crops' if action in ('detect','extract') else ''))

    @app.post('/documents/<did>/catalog')
    def catalog_action(did):
        u=auth.require_user(); services.set_product_status(did,u['id'],request.form)
        return redirect('/documents/'+did+'?tab=products')

    @app.post('/documents/<did>/chat')
    def chat(did):
        u=auth.require_user(); d=owned_doc(did,u['id'])
        services.ask(d['project_id'],u,request.form.get('question',''),did)
        return redirect('/documents/'+did+'?tab=chat')

    @app.post('/documents/<did>/crops')
    def save_crop(did):
        u=auth.require_user(); existing=drawings.crops(did,u['id'])
        try: revision=int(request.form.get('revision','-1'))
        except ValueError: raise AppError(400,'Invalid revision.')
        crops=existing['crops']
        if request.form.get('remove'): crops=[c for c in crops if c['id']!=request.form['remove']]
        else:
            try: page=int(request.form.get('page','1')); box=[float(request.form[k]) for k in ('x','y','w','h')]
            except (ValueError,KeyError): raise AppError(400,'Enter a page and valid crop coordinates.')
            from .store import new_id
            editing=request.form.get('crop_id')
            if editing:
                if not any(c['id']==editing for c in crops): raise AppError(409,'This region no longer exists. Reload before saving.')
                crops=[dict(id=editing,page=page,box=box) if c['id']==editing else c for c in crops]
            else: crops=crops+[dict(id=new_id(),page=page,box=box)]
        drawings.save_crops(did,u['id'],dict(crops=crops,revision=revision))
        return redirect('/documents/'+did+'?tab=crops&page='+request.form.get('page','1'))

    @app.post('/documents/<did>/tables')
    def review_tables(did):
        u=auth.require_user()
        try:
            body=dict(revision=int(request.form.get('revision','-1')),
                      detection_revision=int(request.form.get('detection_revision','-1')),
                      ids=request.form.getlist('ids'))
        except ValueError: raise AppError(400,'Invalid revision.')
        if request.form.get('action')=='reset':
            table_jobs.reset(did,u['id'],dict(revision=body['detection_revision']))
            flash('Suggestions cleared. Click Detect tables to start again. Saved regions are preserved.')
        else:
            table_jobs.accept(did,u['id'],body)
            flash('Selected tables saved as editable regions. Review their borders before extraction.')
        return redirect('/documents/'+did+'?tab=crops')

    @app.route('/requirements/<rid>',methods=['GET','POST'])
    def review(rid):
        u=auth.require_user(); row=owned_requirement(rid,u['id']); d=owned_doc(row['doc_id'],u['id']); r=json.loads(row['data'])
        r.update(id=rid,revision=row['revision'],status=row['status'],reviewNote=row['review_note'])
        if request.method=='POST':
            try: revision=int(request.form.get('revision','-1'))
            except ValueError: raise AppError(400,'Invalid revision.')
            if request.form.get('action')=='products':
                services.product_submittals(rid,u,dict(mode=request.form.get('mode'),productIds=request.form.getlist('productIds'),revision=revision))
                return redirect('/projects/'+d['project_id'])
            data={k:request.form.get(k,'') for k in ('title','text','type','condition','quote')}
            data['evidenceIds']=[s for s in re_split(request.form.get('evidenceIds','')) if s]
            data['products']=[s.strip() for s in request.form.get('products','').split('\n') if s.strip()]
            services.review(rid,u,dict(data=data,status=request.form.get('status'),reviewNote=request.form.get('reviewNote',''),confirmed=request.form.get('confirmed')=='yes',revision=revision))
            flash('Review saved.'); return redirect('/requirements/'+rid)
        parsed=services.parsed_for(d) or {'evidence':[]}; known={e['id']:e for e in parsed['evidence']}
        selected=[known[i] for i in r['evidenceIds'] if i in known]
        try: page=int(request.args.get('page',str(r.get('page',1))))
        except ValueError: page=1
        page=max(1,min(d['pages'],page))
        from .catalog import suggestions
        available=services.catalog(d['id'],u['id'])
        suggested=suggestions(r,available['products'])
        available['products'].sort(key=lambda p:(-suggested.get(p['id'],0),p.get('group',''),p['name']))
        return render_template('review.html',suggested=suggested,requirement=r,document=d,project=owned_project(d['project_id'],u['id']),selected=selected,page=page,types=TYPES,catalog=available,evidence=[e for e in parsed['evidence'] if e['page']==page])

def re_split(text):
    import re
    return re.split(r'[\s,]+',text.strip())
