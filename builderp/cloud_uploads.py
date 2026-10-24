"""Authenticated direct-to-R2 uploads avoid Vercel's request payload ceiling."""
from flask import current_app
from . import pdf,services
from .store import AppError,consume_limit,new_id,now,one,run,owned_project,s3


def presign(key,method='get_object',**extra):
    client=s3()
    if not client: raise AppError(503,'R2 storage is not configured.')
    return client.generate_presigned_url(method,Params=dict(Bucket=current_app.config['R2_BUCKET'],Key=key,**extra),ExpiresIn=600)


def begin(pid,uid,body):
    owned_project(pid,uid)
    filename=body.get('filename'); size=body.get('size'); kind=body.get('docKind','spec')
    if not isinstance(filename,str) or not filename.lower().endswith('.pdf') or len(filename)>240 or kind not in ('spec','drawing'):
        raise AppError(400,'Select a specification or drawing PDF.')
    if type(size)!=int or not 8<=size<=pdf.MAX_SIZE: raise AppError(413,'Choose a PDF up to 10 MB.')
    consume_limit('upload-ticket:'+uid,500)
    upload_id=new_id()
    url=presign('python-staging/'+upload_id,'put_object',ContentType='application/pdf')
    run('INSERT INTO python_uploads(id,user_id,project_id,filename,kind,size,expires) VALUES (?,?,?,?,?,?,?)',upload_id,uid,pid,filename,kind,size,now()+600000)
    return dict(uploadId=upload_id,url=url,headers={'Content-Type':'application/pdf'})


def complete(pid,uid,body):
    owned_project(pid,uid)
    upload_id=body.get('uploadId')
    if not isinstance(upload_id,str): raise AppError(400,'Missing upload ID.')
    ticket=one('SELECT * FROM python_uploads WHERE id=? AND user_id=? AND project_id=?',upload_id,uid,pid)
    if not ticket: raise AppError(404,'Upload not found.')
    if ticket['doc_id']:
        from .store import owned_doc
        owned_doc(ticket['doc_id'],uid)
        return {'docId':ticket['doc_id'],'reused':True}
    if ticket['expires']<now(): raise AppError(410,'Upload expired. Select the PDF again.')
    client=s3(); key='python-staging/'+upload_id; bucket=current_app.config['R2_BUCKET']
    try:
        meta=client.head_object(Bucket=bucket,Key=key)
        if meta['ContentLength']!=ticket['size'] or meta['ContentLength']>pdf.MAX_SIZE:
            raise AppError(413,'Uploaded file size does not match the selected PDF.')
        # Bounded read defends against object replacement between HEAD and GET.
        stream=client.get_object(Bucket=bucket,Key=key)['Body']
        try: data=stream.read(pdf.MAX_SIZE+1)
        finally: stream.close()
    except AppError: raise
    except Exception as exc: raise AppError(502,'R2 upload could not be read. Finish uploading and retry.') from exc
    if len(data)!=ticket['size']: raise AppError(400,'Uploaded file size changed. Upload again.')
    result=services.upload(uid,pid,ticket['filename'],data,ticket['kind'])
    run('UPDATE python_uploads SET doc_id=? WHERE id=? AND user_id=?',result['docId'],upload_id,uid)
    try: client.delete_object(Bucket=bucket,Key=key)
    except Exception: pass  # A lifecycle rule cleans up abandoned staging objects.
    return result
