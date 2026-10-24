import io
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, request, jsonify, g, session, redirect, render_template
from werkzeug.exceptions import HTTPException
from . import auth,services,ai,equipment,drawings,pdf,table_jobs,cloud_uploads
from .exports import export_project
from .store import AppError,init_db,all_rows,one,run,sha,now,new_id,get_bytes,owned_doc,owned_project

def create_app(config=None):
    from .config import load_env
    load_env()
    app=Flask(__name__,static_folder='../public/static',static_url_path='/static')
    root=Path(os.getenv('BUILDERP_DATA_DIR','data')).resolve()
    app.config.from_mapping(DATABASE=str(root/'builderp.sqlite3'),OBJECT_DIR=str(root/'objects'),SECRET_KEY=os.getenv('SECRET_KEY'),SETTINGS_SECRET=os.getenv('SETTINGS_SECRET',''),R2_BUCKET=os.getenv('R2_BUCKET',''),GEMINI_API_KEY=os.getenv('GEMINI_API_KEY',''),GEMINI_CHAT_MODEL=os.getenv('GEMINI_CHAT_MODEL','gemini-2.5-flash'),EMBEDDING_MODEL=os.getenv('EMBEDDING_MODEL','gemini-embedding-001'),APP_ORIGIN=os.getenv('APP_ORIGIN','http://127.0.0.1:8000').rstrip('/'),MAX_CONTENT_LENGTH=pdf.MAX_SIZE+1024*1024,MAX_FORM_MEMORY_SIZE=1024*1024,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax')
    app.config.update({k:os.getenv(k,'') for k in ('TURSO_DATABASE_URL','TURSO_AUTH_TOKEN','R2_ACCOUNT_ID','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY')})
    app.config['VERCEL']=os.getenv('VERCEL')=='1'
    app.config['AUTO_MIGRATE']=not bool(app.config['TURSO_DATABASE_URL'])
    if config: app.config.update(config)
    if app.config['VERCEL']:
        missing=[k for k in ('TURSO_DATABASE_URL','TURSO_AUTH_TOKEN','R2_BUCKET','R2_ACCOUNT_ID','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY','SECRET_KEY','SETTINGS_SECRET') if not app.config.get(k)]
        if missing: raise RuntimeError('Missing deployment configuration: '+', '.join(missing))
        if not os.getenv('APP_ORIGIN') and not (config or {}).get('APP_ORIGIN'):
            app.config['APP_ORIGIN']='https://'+os.environ['VERCEL_URL']
    if app.config['TURSO_DATABASE_URL'] and not app.config['TURSO_AUTH_TOKEN']:
        raise RuntimeError('TURSO_AUTH_TOKEN is required with TURSO_DATABASE_URL.')
    if not app.config['SECRET_KEY']:
        secret_file=Path(app.config['DATABASE']).parent/'session-secret'
        secret_file.parent.mkdir(parents=True,exist_ok=True)
        try:
            fd=os.open(secret_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f: f.write(secrets.token_hex(32))
        except FileExistsError: pass
        app.config['SECRET_KEY']=secret_file.read_text().strip()
    app.config.setdefault('COOKIE_SECURE',app.config['APP_ORIGIN'].startswith('https://'))
    app.config['SESSION_COOKIE_SECURE']=app.config['COOKIE_SECURE']
    if app.config['AUTO_MIGRATE']:
        with app.app_context(): init_db()

    # PyMuPDF is not safe across threads. Fluid Compute may overlap requests.
    # Serialize work per warm Python process; Vercel can scale across processes.
    import threading
    native_lock=threading.Lock()
    @app.before_request
    def serialize_native_work():
        native_lock.acquire(); g.native_lock_held=True

    @app.teardown_request
    def release_native_work(error=None):
        if g.pop('native_lock_held',False): native_lock.release()

    @app.teardown_appcontext
    def close_db(error=None):
        connection=g.pop('db',None)
        if connection: connection.close()

    @app.before_request
    def guard():
        expected=urlsplit(app.config['APP_ORIGIN']).netloc
        if request.host!=expected and not app.config.get('TESTING'): raise AppError(400,'Unrecognized application host. Check APP_ORIGIN.')
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin=request.headers.get('Origin')
            if origin and origin!=app.config['APP_ORIGIN']: raise AppError(403,'Request origin was not accepted.')
            if request.path.startswith('/api/'):
                if origin!=app.config['APP_ORIGIN']: raise AppError(403,'Request origin was not accepted.')
            else:
                token=request.form.get('_csrf','')
                if not token or not secrets.compare_digest(token,session.get('csrf','')): raise AppError(403,'This form expired. Reload and try again.')
        if 'csrf' not in session: session['csrf']=secrets.token_hex(32)
        g.user=auth.current_user()

    @app.after_request
    def secure_headers(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        r2_connect=(' https://'+app.config['R2_ACCOUNT_ID']+'.r2.cloudflarestorage.com') if app.config.get('R2_ACCOUNT_ID') else ''
        response.headers['Content-Security-Policy']=("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:"+r2_connect+"; frame-src 'self'"+r2_connect+"; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; connect-src 'self'"+r2_connect)
        return response

    @app.context_processor
    def common(): return dict(user=g.get('user'),csrf=session.get('csrf',''),direct_upload=bool(app.config['R2_BUCKET']),cloud_deployment=app.config['VERCEL'])

    from .turso import TursoError
    @app.errorhandler(TursoError)
    def database_error(error):
        return app_error(AppError(503,str(error),'database_unavailable'))

    @app.errorhandler(AppError)
    def app_error(error):
        if request.path.startswith('/api/'): return jsonify(error=str(error),code=error.code),error.status
        if error.status==401: return redirect('/login')
        return render_template('error.html',message=str(error),status=error.status),error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith('/api/'): return jsonify(error=error.description,code='request_failed'),error.code
        return render_template('error.html',message=error.description,status=error.code),error.code

    @app.errorhandler(Exception)
    def unexpected(error):
        app.logger.error('BuildERP request failed (%s)',type(error).__name__)
        if app.testing: raise error
        if request.path.startswith('/api/'): return jsonify(error='This action could not finish. Saved work is safe; please retry.',code='internal_error'),500
        return render_template('error.html',message='This action could not finish. Saved work is safe; please retry.',status=500),500

    def response_file(content,mime,name,inline=False):
        from flask import send_file
        if app.config['VERCEL'] and len(content)>4_000_000:
            from .store import put_bytes
            object_key='python-downloads/'+new_id()
            put_bytes(object_key,content)
            return redirect(cloud_uploads.presign(object_key,ResponseContentType=mime,ResponseContentDisposition=('inline' if inline else 'attachment')+"; filename*=UTF-8''"+__import__('urllib.parse',fromlist=['quote']).quote(name)),code=302)
        return send_file(io.BytesIO(content),mimetype=mime,download_name=name,as_attachment=not inline)

    @app.route('/api/<path:path>',methods=['GET','POST','PATCH','DELETE','PUT'])
    def api(path):
        parts=path.split('/'); resource=parts[0]; key=parts[1] if len(parts)>1 else None; action=parts[2] if len(parts)>2 else None; method=request.method
        if len(parts)>3: raise AppError(404,'This action is not available.')
        if resource=='health' and method=='GET': return {'status':'ok','version':'python-1.0'}
        b=request.get_json(silent=True) or {}
        if not isinstance(b,dict): raise AppError(400,'Expected a JSON object.')
        if resource=='auth' and method=='POST':
            if key in ('login','signup'):
                user,token=auth.authenticate(b.get('username'),b.get('password'),key=='signup'); session.clear(); return auth.cookie(jsonify(user=user),token)
            if key=='logout': auth.logout(); session.clear(); return auth.cookie(jsonify(ok=True),'')
        u=auth.require_user(); uid=u['id']
        if resource=='deployment-check' and method=='GET':
            from .diagnostics import check
            return check(u)
        if resource=='me' and method=='GET': return dict(user=u,aiConfigured=ai.configured(),chatModel=ai.chat_model(),embeddingModel=ai.embed_model())
        if resource=='password' and method=='POST': return auth.change_password(u,b)
        if resource=='settings' and method=='POST':
            if u['role']!='admin': raise AppError(403,'Only the owner can change shared AI settings.')
            if b.get('apiKey'): ai.save_key(str(b['apiKey']).strip())
            if b.get('test'): ai.generate('Return JSON {"ok":true}.','Connection check.',uid); ai.embed(['BuildERP connection check'],uid)
            return {'ok':True}
        if resource=='projects':
            if not key:
                if method=='GET': return {'projects':all_rows('SELECT p.*,(SELECT COUNT(*) FROM project_docs d WHERE d.project_id=p.id) document_count FROM projects p WHERE user_id=? ORDER BY created DESC',uid)}
                if method=='POST': return services.create_project(uid,b)
            elif not action:
                if method=='GET': return services.project_detail(key,uid)
                if method=='DELETE': return services.delete_project(key,uid)
            elif action=='upload-url' and method=='POST': return cloud_uploads.begin(key,uid,b)
            elif action=='upload-complete' and method=='POST': return cloud_uploads.complete(key,uid,b)
            elif action=='upload' and method=='POST':
                file=request.files.get('file')
                if not file: raise AppError(400,'Select a PDF.')
                return services.upload(uid,key,file.filename,file.read(pdf.MAX_SIZE+1),request.form.get('docKind','spec'))
            elif action=='equipment-log':
                if method=='GET': return equipment.list_log(key,uid)
                if method=='POST': return equipment.generate_log(key,u)
            elif action=='chat' and method=='POST': return services.ask(key,u,b.get('question',''),b.get('docId',''))
            elif action=='export' and method=='GET':
                ids=[x for x in request.args.get('ids','').split(',') if x]
                return response_file(*export_project(key,uid,request.args.get('format','json'),request.args.get('approved')=='1' or (bool(ids) and 'approved' not in request.args),ids))
        if resource=='documents' and key:
            d=owned_doc(key,uid)
            if method=='DELETE' and not action: return services.delete_document(key,uid)
            if method=='GET':
                if action=='evidence': return services.parsed_for(d) or {'evidence':[],'pages':d['pages']}
                if action=='pdf':
                    if app.config['R2_BUCKET']: return redirect(cloud_uploads.presign('pdf/'+d['hash'],ResponseContentType='application/pdf'),code=302)
                    data=get_bytes('pdf/'+d['hash'])
                    if not data: raise AppError(404,'Original PDF is unavailable.')
                    return response_file(data,'application/pdf',d['filename'],True)
                if action=='page':
                    try: page=int(request.args.get('page','1'))
                    except ValueError: raise AppError(400,'Invalid page.')
                    ids=set(request.args.get('evidence','').split(',')); boxes=[e['box'] for e in (services.parsed_for(d) or {}).get('evidence',[]) if e['id'] in ids and e['page']==page and e.get('box')]
                    if request.args.get('regions')=='detected':
                        boxes += [p['box'] for p in table_jobs.state(key,uid)['proposals'] if p['page']==page and (not request.args.get('region') or p['id']==request.args['region'])]
                    elif request.args.get('regions')=='saved':
                        boxes += [p['box'] for p in drawings.crops(key,uid)['crops'] if p['page']==page]
                    return response_file(pdf.render_page(get_bytes('pdf/'+d['hash']),page,boxes),'image/png','page.png',True)
                if action=='messages': return {'messages':all_rows('SELECT * FROM messages WHERE doc_id=? ORDER BY created LIMIT 200',key)}
                if action=='products': return services.catalog(key,uid)
                if action=='crops': return drawings.crops(key,uid)
                if action=='table-detection': return table_jobs.state(key,uid)
            if method=='POST':
                if action=='prepare': return services.prepare(key,u)
                if action=='process':
                    prepared=services.prepare(key,u)
                    return services.generate_log(key,u) if prepared['status']=='prepared' else prepared
                if action=='generate': return services.generate_log(key,u)
                if action=='index': return services.index_step(key,u)
                if action=='confirm-empty': return services.confirm_empty(key,u,b.get('note',''))
                if action=='products': return services.set_product_status(key,uid,b)
                if action=='crops': return drawings.save_crops(key,uid,b)
                if action=='prepare-drawing-crops': return table_jobs.detect_step(key,uid)
                if action=='accept-tables': return table_jobs.accept(key,uid,b)
                if action=='reset-table-detection': return table_jobs.reset(key,uid,b)
                if action=='process-drawing': return drawings.extract_step(key,u)
        if resource=='requirements' and key:
            if action=='products' and method=='POST': return services.product_submittals(key,u,b)
            if not action and method=='PATCH': return services.review(key,u,b)
        if resource=='equipment-items' and key and method=='PATCH': return equipment.patch_item(key,u,b)
        raise AppError(404,'This action is not available.')

    from .views import register_views
    register_views(app,response_file)
    return app
