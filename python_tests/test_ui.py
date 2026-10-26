"""Server-rendered UI integration checks; native detection and Gemini are mocked.
These do not substitute for browser layout/interaction checks.
"""
import json
import unittest
from html.parser import HTMLParser
from unittest.mock import patch
import test_app as fixtures
from test_table_detection import drawing_pdf, proposed


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.elements=[]; self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag,dict(attrs)))

    def attrs(self, tag, **matches):
        return [attrs for name,attrs in self.elements if name==tag and all(attrs.get(k)==v for k,v in matches.items())]


class UiTests(unittest.TestCase):
    setUp=fixtures.AppTests.setUp
    tearDown=fixtures.AppTests.tearDown
    post=fixtures.AppTests.post
    upload=fixtures.AppTests.upload
    prepared=fixtures.AppTests.prepared
    extraction=fixtures.AppTests.extraction
    generated=fixtures.AppTests.generated

    def page(self, path):
        response=self.client.get(path)
        self.assertEqual(response.status_code,200,response.data[:1500])
        return response.get_data(as_text=True)

    def submit(self,path,data):
        with self.client.session_transaction() as session:
            csrf=session['csrf']
        return self.client.post(path,data=dict(_csrf=csrf,**data),headers={'Origin':fixtures.ORIGIN})

    def test_all_templates_compile_and_empty_views_render(self):
        for name in self.app.jinja_env.list_templates():
            self.app.jinja_env.get_template(name)
        for path in ['/workspace','/settings']+['/projects/'+self.pid+'?tab='+tab for tab in ('specs','drawings','register','equipment','activity','source','chat')]:
            html=self.page(path)
            self.assertIn('app-sidebar',html)
            self.assertNotIn('Traceback',html)
        self.assertIn('Welcome back.',self.app.test_client().get('/login').get_data(as_text=True))
        self.assertIn('Create your account.',self.app.test_client().get('/signup').get_data(as_text=True))

    def test_populated_specs_source_products_chat_and_review(self):
        did,r=self.generated()
        for tab in ('source','products','chat'):
            html=self.page('/documents/'+did+'?tab='+tab)
            self.assertIn('boilers.pdf',html)
        source=Elements(self.page('/documents/'+did))
        self.assertTrue(source.attrs('a',href='/requirements/'+r['id']))
        html=self.page('/requirements/'+r['id'])
        parsed=Elements(html)
        for field in ('title','text','quote','evidenceIds','revision','confirmed'):
            self.assertTrue(any(attrs.get('name')==field for _,attrs in parsed.elements),field)
        register=Elements(self.page('/projects/'+self.pid+'?tab=register'))
        self.assertTrue(register.attrs('input',value=r['id']))
        self.assertTrue(register.attrs('form',action='/api/projects/'+self.pid+'/export'))
        for tab in ('source','chat'):
            response=self.client.get('/projects/'+self.pid+'?tab='+tab)
            self.assertEqual(response.status_code,302)
            self.assertEqual(response.location,'/documents/'+did+'?tab='+tab)
        response=self.client.get('/api/projects/'+self.pid+'/export?format=json&approved=0&ids='+r['id'])
        self.assertEqual(response.status_code,200)

    def test_drawing_html_forms_accept_edit_extract_and_equipment(self):
        did=self.upload('drawing',drawing_pdf())
        base='/documents/'+did
        self.page(base+'?tab=crops')
        with patch('builderp.table_jobs.detect_page',return_value=proposed()):
            detected=self.post('/api/documents/'+did+'/prepare-drawing-crops').json
        html=self.page(base+'?tab=crops')
        elements=Elements(html)
        canvas=next(attrs for tag,attrs in elements.elements if tag=='div' and attrs.get('class')=='crop-surface')
        self.assertEqual(json.loads(canvas['data-regions'])[0]['box'],[.1,.1,.5,.3])
        self.assertIn('data-reject-candidate',html)
        response=self.submit(base+'/tables',dict(revision='0',detection_revision=str(detected['revision']),ids='detected-1'))
        self.assertEqual(response.status_code,302,response.data)
        crops=self.client.get('/api/documents/'+did+'/crops').json
        edit=self.page(base+'?tab=crops&edit=detected-1&regions=saved')
        self.assertIn('Update region',edit)
        response=self.submit(base+'/crops',dict(revision=str(crops['revision']),page='1',crop_id='detected-1',x='.1',y='.1',w='.6',h='.4'))
        self.assertEqual(response.status_code,302,response.data)
        self.assertEqual(self.client.get('/api/documents/'+did+'/crops').json['crops'][0]['box'],[.1,.1,.6,.4])
        with patch('builderp.ai.generate',return_value={'schedule_title':'EXHAUST FAN SCHEDULE','sheet_number':'M-01','rows':[{'TAG':'EF-1','CFM':'100'}]}):
            response=self.submit(base+'/action',dict(action='extract'))
        self.assertEqual(response.status_code,302,response.data)
        response=self.submit('/projects/'+self.pid+'/equipment',{})
        self.assertEqual(response.status_code,302,response.data)
        html=self.page('/projects/'+self.pid+'?tab=equipment')
        self.assertIn('EF-1',html)
        self.assertIn('M-01',html)
        self.assertIn('data-export-equipment',html)
        self.assertTrue(Elements(html).attrs('form',action='/projects/'+self.pid+'/equipment'))

    def test_cloud_upload_ui_keeps_signed_upload_contract(self):
        self.app.config['R2_BUCKET']='preview-bucket'
        html=self.page('/projects/'+self.pid)
        forms=Elements(html).attrs('form',action='/projects/'+self.pid+'/upload')
        self.assertEqual(forms[0]['data-upload-project'],self.pid)
        self.assertEqual(forms[0]['enctype'],'multipart/form-data')
        response=self.client.get('/static/app.js')
        source=response.get_data(as_text=True); response.close()
        for endpoint in ('upload-url','upload-complete'):
            self.assertIn(endpoint,source)

    def test_project_form_escaping_and_document_chooser(self):
        self.page('/workspace')
        response=self.submit('/workspace',dict(name='<script>alert(1)</script>',description='A & B'))
        self.assertEqual(response.status_code,302,response.data)
        html=self.page(response.location)
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',html)
        self.upload(); self.upload('drawing',drawing_pdf())
        html=self.page('/workspace')
        self.assertIn('SPECIFICATIONS',html)
        self.assertEqual(self.client.get('/projects/'+self.pid+'?tab=unknown').status_code,200)

    def test_unsigned_in_users_cannot_access_project_pages(self):
        anonymous=self.app.test_client()
        response=anonymous.get('/projects/'+self.pid+'?tab=equipment')
        self.assertIn(response.status_code,(302,401))
        self.assertNotIn('Riverside Medical Center',response.get_data(as_text=True))


# Exercise new navigation/source reads against the existing Turso wire adapter, too.
import test_cloud

class RemoteUiTests(UiTests):
    setUp=test_cloud.RemoteAppTests.setUp
    tearDown=test_cloud.RemoteAppTests.tearDown
