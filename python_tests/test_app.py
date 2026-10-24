import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import fitz
from openpyxl import load_workbook
from builderp import create_app,services,drawings,equipment,ai
from builderp.store import db,one,run,sha,now,put_json,get_json,job_lock,AppError
from builderp.domain import validate_requirement,source_includes,correct_sections,assess_product,tag_evidence,parse_extraction

ORIGIN='http://127.0.0.1:8000'
PASSWORD='correct horse battery staple'

def pdf_bytes(text=None,rotation=0):
    with fitz.open() as pdf:
        page=pdf.new_page()
        page.insert_text((45,50),'SECTION 23 52 00 BOILERS',fontsize=12)
        page.insert_text((45,90),'PART 1 GENERAL',fontsize=12)
        page.insert_text((45,120),'1.2 SUBMITTALS',fontsize=12)
        page.insert_text((50,150),text or 'A. Submit product data for boilers.',fontsize=11)
        page.insert_text((45,200),'PART 2 PRODUCTS',fontsize=12)
        page.insert_text((45,230),'2.1 BOILERS',fontsize=12)
        page.insert_text((50,260),'A. Provide boilers with controls.',fontsize=11)
        if rotation: page.set_rotation(rotation)
        return pdf.tobytes()

class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app({'TESTING':True,'VERCEL':False,'TURSO_DATABASE_URL':'','TURSO_AUTH_TOKEN':'','AUTO_MIGRATE':True,'DATABASE':self.tmp.name+'/test.db','OBJECT_DIR':self.tmp.name+'/objects','SECRET_KEY':'test-secret','SETTINGS_SECRET':'test-settings','APP_ORIGIN':ORIGIN,'R2_BUCKET':'','GEMINI_API_KEY':''})
        self.client=self.app.test_client()
        response=self.post('/api/auth/signup',{'username':'owner','password':PASSWORD})
        self.user=response.json['user']
        self.pid=self.post('/api/projects',{'name':'Riverside Medical Center','description':'Mechanical specifications'}).json['id']

    def tearDown(self): self.tmp.cleanup()
    def post(self,path,data=None,client=None): return (client or self.client).post(path,json=data or {},headers={'Origin':ORIGIN})
    def patch(self,path,data): return self.client.patch(path,json=data,headers={'Origin':ORIGIN})
    def upload(self,kind='spec',data=None,pid=None):
        response=self.client.post('/api/projects/'+(pid or self.pid)+'/upload',data={'file':(io.BytesIO(data or pdf_bytes()),'boilers.pdf'),'docKind':kind},headers={'Origin':ORIGIN})
        self.assertEqual(response.status_code,200,response.data)
        return response.json['docId']
    def prepared(self):
        did=self.upload()
        self.assertEqual(self.post('/api/documents/'+did+'/prepare').json['status'],'parsed')
        self.assertEqual(self.post('/api/documents/'+did+'/prepare').json['status'],'prepared')
        return did
    def extraction(self,did):
        ev=self.client.get('/api/documents/'+did+'/evidence').json['evidence']
        obligation=next(e for e in ev if 'Submit product' in e['text']); product=next(e for e in ev if 'Provide boilers' in e['text'])
        return {'requirements':[{'title':'Product data','text':obligation['text'],'type':'product_data','condition':None,'evidenceIds':[obligation['id']],'quote':obligation['text'],'products':[]}],'products':[{'name':'boilers','description':'Boilers with controls','entityType':'product','usageStatus':'required','catalogRole':'standalone_item','evidenceIds':[product['id']],'quote':product['text']}]}
    def generated(self):
        did=self.prepared()
        with patch('builderp.ai.generate',return_value=self.extraction(did)):
            response=self.post('/api/documents/'+did+'/generate')
        self.assertEqual(response.status_code,200,response.data); self.assertTrue(response.json['done'])
        rows=self.client.get('/api/projects/'+self.pid).json['requirements']
        self.assertEqual(len(rows),1)
        return did,rows[0]
    def review_body(self,r,status='approved',**overrides):
        body=dict(data={k:r[k] for k in ('title','text','type','condition','evidenceIds','quote','products')},status=status,revision=r['revision'],confirmed=True,reviewNote='')
        body.update(overrides); return body

    def test_owner_member_and_login_compatibility(self):
        self.assertEqual(self.user['role'],'admin')
        other=self.app.test_client(); response=self.post('/api/auth/signup',dict(username='member',password=PASSWORD),other)
        self.assertEqual(response.json['user']['role'],'member')
        self.assertEqual(self.post('/api/settings',dict(apiKey='AIza'+'x'*30),other).status_code,403)
        with self.app.app_context():
            stored=one('SELECT password FROM users WHERE id=?',self.user['id'])['password']; salt,hashed=stored.split(':')
            from builderp.auth import derive
            self.assertEqual(derive(PASSWORD,salt),hashed)
        self.post('/api/auth/logout'); self.assertEqual(self.client.get('/api/me').status_code,401)
        self.assertEqual(self.post('/api/auth/login',dict(username='owner',password=PASSWORD)).status_code,200)

    def test_csrf_origin_and_form_token(self):
        self.assertEqual(self.client.post('/api/projects',json={'name':'bad'}).status_code,403)
        self.assertEqual(self.client.post('/api/projects',json={'name':'bad'},headers={'Origin':'https://attacker.example'}).status_code,403)
        self.assertEqual(self.client.post('/workspace',data={'name':'bad'}).status_code,403)
        self.client.get('/workspace')
        with self.client.session_transaction() as sess: token=sess['csrf']
        response=self.client.post('/workspace',data={'name':'form project','_csrf':token})
        self.assertEqual(response.status_code,302)

    def test_password_invalidates_all_sessions(self):
        other=self.app.test_client(); self.post('/api/auth/login',dict(username='owner',password=PASSWORD),other)
        self.assertEqual(self.post('/api/password',dict(current=PASSWORD,password='a different long password')).status_code,200)
        self.assertEqual(other.get('/api/me').status_code,401); self.assertEqual(self.client.get('/api/me').status_code,401)

    def test_upload_validation_and_reuse(self):
        data=pdf_bytes(); did=self.upload(data=data); second=self.upload(data=data)
        self.assertEqual(did,second)
        response=self.client.post('/api/projects/'+self.pid+'/upload',data={'file':(io.BytesIO(b'%PDF-not really'),'fake.pdf')},headers={'Origin':ORIGIN})
        self.assertEqual(response.status_code,400)
        response=self.client.post('/api/projects/'+self.pid+'/upload',data={'file':(io.BytesIO(data),'file.txt')},headers={'Origin':ORIGIN})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.get('/api/documents/'+did+'/pdf').data,data)

    def test_ownership_every_document_surface(self):
        did,r=self.generated(); other=self.app.test_client(); self.post('/api/auth/signup',dict(username='outsider',password=PASSWORD),other)
        for path in [f'/api/projects/{self.pid}',f'/api/documents/{did}/evidence',f'/api/documents/{did}/pdf',f'/api/documents/{did}/page',f'/api/documents/{did}/messages',f'/api/documents/{did}/products',f'/api/projects/{self.pid}/export']:
            self.assertEqual(other.get(path).status_code,404,path)
        self.assertEqual(other.patch('/api/requirements/'+r['id'],json=self.review_body(r),headers={'Origin':ORIGIN}).status_code,404)

    def test_pdf_prepare_structure_and_render(self):
        did=self.prepared(); parsed=self.client.get('/api/documents/'+did+'/evidence').json
        self.assertEqual(parsed['sections'],['235200'])
        obligation=next(e for e in parsed['evidence'] if 'Submit product' in e['text'])
        self.assertEqual(obligation['clause'],'1.2.A')
        response=self.client.get(f'/api/documents/{did}/page?page=1&evidence={obligation["id"]}')
        self.assertEqual(response.status_code,200); self.assertTrue(response.data.startswith(b'\x89PNG'))
        self.assertEqual(self.client.get(f'/api/documents/{did}/page?page=2').status_code,404)

    def test_rotated_pdf_boxes(self):
        from builderp.pdf import parse_pdf,subset
        parsed=parse_pdf(pdf_bytes(rotation=90))
        for e in parsed['evidence']:
            x,y,w,h=e['box']; self.assertTrue(0<=x<=1 and 0<=y<=1 and x+w<=1.000001 and y+h<=1.000001)
        crop=subset(pdf_bytes(rotation=90),1,[.1,.1,.5,.5]); self.assertTrue(crop.startswith(b'%PDF-'))

    def test_ocr_step(self):
        with fitz.open() as doc: doc.new_page(); data=doc.tobytes()
        did=self.upload(data=data); self.post('/api/documents/'+did+'/prepare')
        with patch('builderp.ai.generate',return_value={'lines':['SECTION 23 52 00 BOILERS','A. Submit product data for boilers.']}):
            self.assertEqual(self.post('/api/documents/'+did+'/prepare').json['status'],'ocr')
        evidence=self.client.get('/api/documents/'+did+'/evidence').json
        self.assertFalse(evidence['scannedPages']); self.assertEqual(evidence['evidence'][0]['source'],'ocr')

    def test_generation_is_resumable_and_idempotent(self):
        did=self.prepared()
        with patch('builderp.ai.generate',side_effect=AppError(429,'Quota reached.')):
            self.assertEqual(self.post('/api/documents/'+did+'/generate').status_code,429)
        with self.app.app_context(): self.assertEqual(one("SELECT lease FROM python_jobs WHERE doc_id=? AND kind='submittals'",did)['lease'],0)
        with patch('builderp.ai.generate',return_value=self.extraction(did)) as provider:
            self.assertTrue(self.post('/api/documents/'+did+'/generate').json['done']); self.assertTrue(self.post('/api/documents/'+did+'/generate').json['done']); self.assertEqual(provider.call_count,1)
        self.assertEqual(len(self.client.get('/api/projects/'+self.pid).json['requirements']),1)

    def test_capacity_failure_splits_batch(self):
        did=self.prepared(); data=self.extraction(did)
        def provider(system,text,uid):
            return dict(requirements=[r for r in data['requirements'] if '['+r['evidenceIds'][0]+']' in text],products=[p for p in data['products'] if '['+p['evidenceIds'][0]+']' in text])
        responses=[AppError(502,'Truncated','truncated_output')]
        def generate(system,text,uid):
            if responses: raise responses.pop()
            return provider(system,text,uid)
        with patch('builderp.ai.generate',side_effect=generate): self.assertTrue(self.post('/api/documents/'+did+'/generate').json['done'])
        self.assertEqual(len(self.client.get('/api/projects/'+self.pid).json['requirements']),1)

    def test_invalid_extraction_does_not_advance(self):
        did=self.prepared()
        with patch('builderp.ai.generate',return_value={'requirements':[{}],'products':[]}): self.assertEqual(self.post('/api/documents/'+did+'/generate').status_code,502)
        with self.app.app_context(): self.assertEqual(one("SELECT cursor FROM python_jobs WHERE doc_id=? AND kind='submittals'",did)['cursor'],0)

    def test_review_guard_and_optimistic_concurrency(self):
        did,r=self.generated(); body=self.review_body(r,confirmed=False)
        self.assertEqual(self.patch('/api/requirements/'+r['id'],body).status_code,422)
        body=self.review_body(r); body['data']['quote']='invented wording from nowhere'
        self.assertEqual(self.patch('/api/requirements/'+r['id'],body).status_code,422)
        body=self.review_body(r)
        self.assertEqual(self.patch('/api/requirements/'+r['id'],body).status_code,200)
        self.assertEqual(self.patch('/api/requirements/'+r['id'],body).status_code,409)

    def test_warning_and_rejection_notes(self):
        did,r=self.generated(); body=self.review_body(r); body['data']['products']=['unmentioned valve']
        self.assertEqual(self.patch('/api/requirements/'+r['id'],body).status_code,422)
        body['reviewNote']='Confirmed applicability against source.'
        self.assertEqual(self.patch('/api/requirements/'+r['id'],body).status_code,200)
        r=self.client.get('/api/projects/'+self.pid).json['requirements'][0]
        self.assertEqual(self.patch('/api/requirements/'+r['id'],self.review_body(r,'rejected')).status_code,422)
        self.assertEqual(self.patch('/api/requirements/'+r['id'],self.review_body(r,'rejected',reviewNote='Duplicate obligation after source review.')).status_code,200)

    def test_products_and_derived_rows(self):
        did,r=self.generated(); catalog=self.client.get('/api/documents/'+did+'/products').json; product=catalog['products'][0]
        body=dict(productIds=[product['id']],revision=0,mode='individual')
        self.assertEqual(self.post('/api/requirements/'+r['id']+'/products',body).status_code,200)
        self.assertEqual(len(self.client.get('/api/projects/'+self.pid).json['requirements']),2)
        body['mode']='combined'; self.assertEqual(self.post('/api/requirements/'+r['id']+'/products',body).status_code,200)
        rows=self.client.get('/api/projects/'+self.pid).json['requirements']; self.assertEqual(len(rows),1); self.assertEqual(rows[0]['products'],['boilers'])
        self.assertEqual(rows[0]['status'],'needs_review'); self.assertEqual(rows[0]['revision'],1)
        self.assertEqual(self.post('/api/documents/'+did+'/products',dict(productId=product['id'],status='excluded')).status_code,200)
        self.assertEqual(len(self.client.get('/api/documents/'+did+'/products').json['excludedProducts']),1)

    def test_exports_and_formula_safety(self):
        did,r=self.generated()
        self.assertEqual(self.client.get('/api/projects/'+self.pid+'/export?approved=1').status_code,409)
        body=self.review_body(r); body['data']['title']='=HYPERLINK("bad")'; self.patch('/api/requirements/'+r['id'],body)
        response=self.client.get('/api/projects/'+self.pid+'/export?approved=1&format=xlsx')
        self.assertEqual(response.status_code,200,response.data)
        workbook=load_workbook(io.BytesIO(response.data)); cell=workbook['Submittal register']['F2']
        self.assertEqual(cell.data_type,'s'); self.assertTrue(cell.value.startswith('='))
        response=self.client.get('/api/projects/'+self.pid+'/export?approved=1&format=json'); self.assertEqual(response.json['exportStatus'],'approved')
        self.assertEqual(self.client.get('/api/projects/'+self.pid+'/export?ids=unknown').status_code,422)
        self.assertEqual(self.client.get('/api/projects/'+self.pid+'/export?format=md').status_code,200)

    def test_empty_result_confirmation(self):
        did=self.prepared()
        with patch('builderp.ai.generate',return_value={'requirements':[],'products':[]}): self.post('/api/documents/'+did+'/generate')
        self.assertEqual(self.client.get('/api/projects/'+self.pid+'/export?approved=1').status_code,409)
        self.assertEqual(self.post('/api/documents/'+did+'/confirm-empty',dict(note='Reviewed the full PDF; no submittals found.')).status_code,200)
        self.assertEqual(self.client.get('/api/projects/'+self.pid+'/export?approved=1').status_code,200)

    def test_chat_index_and_citation_validation(self):
        did=self.prepared()
        with patch('builderp.ai.embed',return_value=[[1.0]+[0.0]*767]): self.assertTrue(self.post('/api/documents/'+did+'/index').json['done'])
        with patch('builderp.ai.embed',return_value=[[1.0]+[0.0]*767]), patch('builderp.ai.generate',return_value={'answer':'Submit product data [S1].','sources':['S1']}):
            response=self.post('/api/projects/'+self.pid+'/chat',dict(question='What is required?',docId=did))
        self.assertEqual(response.status_code,200); self.assertEqual(response.json['citations'][0]['docId'],did)
        self.assertEqual(len(self.client.get('/api/documents/'+did+'/messages').json['messages']),2)
        with patch('builderp.ai.embed',return_value=[[1.0]+[0.0]*767]),patch('builderp.ai.generate',return_value={'answer':'Unsupported [S6].','sources':['S1']}):
            self.assertEqual(self.post('/api/projects/'+self.pid+'/chat',dict(question='What else?',docId=did)).status_code,502)

    def test_manual_crop_and_equipment_pipeline(self):
        did=self.upload('drawing'); initial=self.client.get('/api/documents/'+did+'/crops').json
        self.assertEqual(initial['crops'],[])
        crop={'id':'one','page':1,'box':[.05,.05,.8,.8]}
        self.assertEqual(self.post('/api/documents/'+did+'/crops',dict(crops=[crop],revision=0)).status_code,200)
        self.assertEqual(self.post('/api/documents/'+did+'/crops',dict(crops=[crop],revision=0)).status_code,409)
        with patch('builderp.ai.generate',return_value={'schedule_title':'EXHAUST FAN SCHEDULE','sheet_number':'M-01','rows':[{'TAG':'EF-1','CFM':'100'}]}): self.assertTrue(self.post('/api/documents/'+did+'/process-drawing').json['done'])
        response=self.post('/api/projects/'+self.pid+'/equipment-log'); self.assertEqual(response.status_code,200,response.data)
        item=response.json['items'][0]; self.assertEqual(item['unitTag'],'EF-1')
        body=dict(revision=item['revision'],selectedCsi='233423',selectedDescription='Fans',status='confirmed')
        self.assertEqual(self.patch('/api/equipment-items/'+item['id'],body).status_code,200)
        self.assertEqual(self.patch('/api/equipment-items/'+item['id'],body).status_code,409)
        response=self.post('/api/projects/'+self.pid+'/equipment-log'); self.assertEqual(response.json['items'][0]['status'],'confirmed')

    def test_crop_validation(self):
        did=self.upload('drawing')
        for box in [[0,0,2,1],[0,0,0,1],[0,0,float('nan'),1]]:
            self.assertEqual(self.post('/api/documents/'+did+'/crops',dict(crops=[dict(page=1,box=box)],revision=0)).status_code,400)
        self.assertEqual(self.post('/api/documents/'+did+'/process-drawing').status_code,409)

    def test_shared_document_private_decisions(self):
        data=pdf_bytes(); did=self.upload(data=data); pid2=self.post('/api/projects',dict(name='Second')).json['id']; did2=self.upload(data=data,pid=pid2)
        self.assertNotEqual(did,did2)
        self.post('/api/documents/'+did+'/prepare')
        with patch('builderp.ai.generate',return_value=self.extraction(did)): self.post('/api/documents/'+did+'/generate')
        self.assertFalse(self.client.get('/api/projects/'+pid2).json['requirements'])

    def test_key_encryption_and_wrong_secret(self):
        key='AIza'+'x'*30; self.assertEqual(self.post('/api/settings',dict(apiKey=key)).status_code,200)
        with self.app.app_context():
            saved=one("SELECT value FROM settings WHERE key='gemini_key'")['value']; self.assertNotIn(key,saved); self.assertEqual(ai.api_key(),key)
            self.app.config['SETTINGS_SECRET']='wrong'
            with self.assertRaises(AppError): ai.api_key()

    def test_job_lock(self):
        did=self.prepared()
        with self.app.app_context():
            with job_lock(did,'submittals'):
                with self.assertRaises(AppError):
                    with job_lock(did,'submittals'): pass
            self.assertEqual(one("SELECT lease FROM python_jobs WHERE doc_id=? AND kind='submittals'",did)['lease'],0)

    def test_delete_project_is_scoped(self):
        did,r=self.generated(); other=self.post('/api/projects',dict(name='Keep me')).json['id']
        response=self.client.delete('/api/projects/'+self.pid,headers={'Origin':ORIGIN}); self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('/api/projects/'+self.pid).status_code,404); self.assertEqual(self.client.get('/api/projects/'+other).status_code,200)
        with self.app.app_context(): self.assertFalse(one('SELECT id FROM requirements WHERE id=?',r['id']))

    def test_missing_legacy_evidence_blocks_reparse_and_approved_export(self):
        did,r=self.generated()
        self.assertEqual(self.patch('/api/requirements/'+r['id'],self.review_body(r)).status_code,200)
        with self.app.app_context():
            d=one('SELECT hash FROM project_docs WHERE id=?',did)
            run('DELETE FROM python_objects WHERE key=?',services.parsed_key(d['hash']))
        self.assertEqual(self.post('/api/documents/'+did+'/prepare').status_code,409)
        self.assertEqual(self.client.get('/api/projects/'+self.pid+'/export?approved=1').status_code,409)

    def test_invalid_project_name_is_a_validation_error(self):
        for name in [None,False,42,[],{}]:
            self.assertEqual(self.post('/api/projects',dict(name=name)).status_code,400)

    def test_pages_render_and_escape_source(self):
        did,r=self.generated(); drawing=self.upload('drawing',pdf_bytes('A. Drawing schedule here.'))
        for path in ['/workspace','/settings','/projects/'+self.pid,'/requirements/'+r['id']]+['/documents/'+did+'?tab='+t for t in ('source','products','chat')]+['/documents/'+drawing+'?tab=crops']:
            response=self.client.get(path); self.assertEqual(response.status_code,200,path)
            self.assertIn(b'Content-Security-Policy',str(response.headers).encode())
        self.post('/api/projects',dict(name='<script>alert(1)</script>'))
        response=self.client.get('/workspace'); self.assertNotIn(b'<script>alert(1)</script>',response.data); self.assertIn(b'&lt;script&gt;',response.data)

class DomainTests(unittest.TestCase):
    def test_hyphenation(self): self.assertTrue(source_includes('Provide a Y- type strainer.','Y-type strainer'))
    def test_product_relationships(self):
        cases=[({'name':'carbon steel','quote':'Tank: Construct of carbon steel.','entityType':'material','usageStatus':'required','catalogRole':'standalone_item'},False),({'name':'fiberglass insulation','quote':'Provide fiberglass insulation.','entityType':'material','usageStatus':'required','catalogRole':'standalone_item'},True),({'name':'boilers','quote':'Boilers shall not be used.','entityType':'product','usageStatus':'required','catalogRole':'standalone_item'},False)]
        for p,expected in cases: self.assertEqual(assess_product(p,[])['selectable'],expected)
    def test_equipment_tags(self):
        self.assertEqual(equipment.expand_tags('EF-01, 2, 3'),['EF-1','EF-2','EF-3'])
        self.assertEqual(equipment.expand_tags('CGM1234'),[])
        self.assertEqual(equipment.row_tags({'TAG':'AHU-1','MODEL':'ABC12345'}),['AHU-1'])
    def test_submittal_role_boundary(self):
        evidence=[dict(id='a',text='1.2 SUBMITTALS',article='1.2',articleTitle='SUBMITTALS'),dict(id='b',text='Submit product data.',article='1.2',articleTitle='SUBMITTALS'),dict(id='c',text='PART 2 PRODUCTS',part='2',article='',articleTitle=''),dict(id='d',text='Provide boilers.',part='2',article='2.1',articleTitle='BOILERS')]
        tagged,allowed=tag_evidence(evidence); self.assertIn('b',allowed); self.assertNotIn('d',allowed); self.assertEqual(tagged[-1]['role'],'product')

if __name__=='__main__': unittest.main()
