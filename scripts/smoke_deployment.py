"""Exercise a deployed preview using an existing owner's test account.

SMOKE_URL, SMOKE_USERNAME, SMOKE_PASSWORD are read from environment.
Optional VERCEL_AUTOMATION_BYPASS_SECRET accesses protected previews.
Creates and deletes one disposable project. Never prints cookies, keys, or URLs
containing signatures. SET SMOKE_GEMINI=1 to enable a billable Gemini key test.
"""
import http.cookiejar
import io
import json
import os
import urllib.request
import fitz

origin=os.environ['SMOKE_URL'].rstrip('/')
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def request(path,body=None,method=None):
    headers={'Origin':origin,'Content-Type':'application/json'}
    if os.getenv('VERCEL_AUTOMATION_BYPASS_SECRET'):
        headers['x-vercel-protection-bypass']=os.environ['VERCEL_AUTOMATION_BYPASS_SECRET']
    req=urllib.request.Request(origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
    with opener.open(req,timeout=300) as response:return json.load(response)

request('/api/auth/login',dict(username=os.environ['SMOKE_USERNAME'],password=os.environ['SMOKE_PASSWORD']))
print('Sign-in passed')
result=request('/api/deployment-check');assert result['ok'];print('Native OpenCV and database passed')
pid=request('/api/projects',{'name':'Disposable deployment smoke test'})['id']
try:
    with fitz.open() as pdf:
        p=pdf.new_page(width=600,height=800)
        for x in range(60,421,90):p.draw_line((x,100),(x,300),width=1)
        for y in range(100,301,40):p.draw_line((60,y),(420,y),width=1)
        data=pdf.tobytes()
    ticket=request('/api/projects/'+pid+'/upload-url',dict(filename='smoke.pdf',size=len(data),docKind='drawing'))
    # Use a separate HTTP client so application cookies/bypass headers never go to R2.
    with urllib.request.urlopen(urllib.request.Request(ticket['url'],data=data,headers=ticket['headers'],method='PUT'),timeout=60) as response:
        assert response.status==200
    did=request('/api/projects/'+pid+'/upload-complete',dict(uploadId=ticket['uploadId']))['docId']
    detected=request('/api/documents/'+did+'/prepare-drawing-crops',{})
    assert detected['done'] and len(detected['proposals'])==1
    saved=request('/api/documents/'+did+'/accept-tables',dict(ids=[detected['proposals'][0]['id']],revision=0,detection_revision=detected['revision']))
    assert len(saved['crops'])==1
    print('R2 upload, native PDF detection, and crop approval passed')
    if os.getenv('SMOKE_GEMINI')=='1':
        request('/api/settings',{'test':True})
        assert request('/api/documents/'+did+'/process-drawing',{})['done']
        print('Live Gemini connection, embeddings, and crop extraction passed')
    print('Run the real-document browser checklist in DEPLOY-VERCEL.md next.')
finally:
    request('/api/projects/'+pid,method='DELETE')
    print('Disposable project removed; content-addressed PDF may remain in R2.')
