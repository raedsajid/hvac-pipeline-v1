import io
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import test_app as fixtures
import test_table_detection as table_fixtures
from builderp import create_app,store,turso


class HttpEmulator:
    """Execute the actual wire protocol on SQLite, including conditional batches.
    This verifies our adapter, not Turso's network, latency, or service limits.
    """
    def __init__(self,path): self.path=path; self.streams={}; self.serial=0; self.calls=[]
    def __call__(self,request,timeout=0):
        body=json.loads(request.data);self.calls.append(body)
        baton=body.get('baton')
        conn=self.streams.pop(baton) if baton else sqlite3.connect(self.path,isolation_level=None)
        def execute(stmt):
            cursor=conn.execute(stmt['sql'],tuple(turso.decode(v) for v in stmt.get('args',[])))
            rows=cursor.fetchall();names=[c[0] for c in cursor.description or []]
            return dict(cols=[{'name':n} for n in names],rows=[[turso.encode(v) for v in r] for r in rows],affected_row_count=max(0,cursor.rowcount))
        results=[];closed=False
        for req in body['requests']:
            try:
                if req['type']=='close':conn.close();closed=True;response={'type':'close'}
                elif req['type']=='execute':response={'type':'execute','result':execute(req['stmt'])}
                else:
                    values=[];errors=[]
                    for step in req['batch']['steps']:
                        condition=step.get('condition')
                        if condition and values[condition['step']] is None:values.append(None);errors.append(None);continue
                        try:values.append(execute(step['stmt']));errors.append(None)
                        except sqlite3.Error:values.append(None);errors.append({'message':'SQL failed'})
                    response={'type':'batch','result':{'step_results':values,'step_errors':errors}}
                results.append({'type':'ok','response':response})
            except sqlite3.Error:results.append({'type':'error','error':{'message':'SQL failed'}})
        self.serial+=1;new_baton=None if closed else str(self.serial)
        if new_baton:self.streams[new_baton]=conn
        return io.BytesIO(json.dumps({'baton':new_baton,'base_url':None,'results':results}).encode())
    def close(self):
        for conn in self.streams.values():conn.close()


class RemoteAppTests(fixtures.AppTests):
    def setUp(self):
        self.remote_tmp=tempfile.TemporaryDirectory()
        self.emulator=HttpEmulator(self.remote_tmp.name+'/remote.db')
        self.transport=patch('builderp.turso.urllib.request.urlopen',side_effect=self.emulator)
        self.transport.start()
        original=fixtures.create_app
        def remote_app(config):
            config.update(TURSO_DATABASE_URL='libsql://test.turso.io',TURSO_AUTH_TOKEN='test-token',AUTO_MIGRATE=False)
            app=original(config)
            with app.app_context():store.init_db()
            return app
        self.factory=patch.object(fixtures,'create_app',side_effect=remote_app)
        self.factory.start()
        fixtures.AppTests.setUp(self)
    def tearDown(self):
        fixtures.AppTests.tearDown(self);self.factory.stop();self.transport.stop();self.emulator.close();self.remote_tmp.cleanup()


class ProtocolTests(unittest.TestCase):
    def test_failure_rolls_back_entire_batch(self):
        with tempfile.TemporaryDirectory() as d:
            wire=HttpEmulator(d+'/db')
            with patch('builderp.turso.urllib.request.urlopen',side_effect=wire):
                c=turso.Connection('libsql://test.turso.io','test')
                c.execute('CREATE TABLE test(id INTEGER PRIMARY KEY)')
                c.execute('BEGIN IMMEDIATE')
                c.queue('INSERT INTO test VALUES (?)',(1,))
                c.queue('INSERT INTO test VALUES (?)',(1,))
                c.queue('INSERT INTO test VALUES (?)',(2,))
                with self.assertRaises(turso.TursoError):c.execute('COMMIT')
                c.rollback()
                self.assertEqual(c.execute('SELECT * FROM test').fetchall(),[])
                c.close()
            wire.close()
    def test_interrupted_write_is_not_retried(self):
        c=turso.Connection('libsql://test.turso.io','secret')
        with patch('builderp.turso.urllib.request.urlopen',side_effect=TimeoutError) as request:
            for _ in range(2):
                with self.assertRaises(turso.TursoError):c.execute('INSERT INTO test VALUES (1)')
            self.assertEqual(request.call_count,1)
    def test_cloud_configuration_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError,'Missing deployment configuration'):
            create_app({'VERCEL':True,'TURSO_DATABASE_URL':'','SECRET_KEY':'','AUTO_MIGRATE':False})


class UploadTests(unittest.TestCase):
    setUp=fixtures.AppTests.setUp
    tearDown=fixtures.AppTests.tearDown
    post=fixtures.AppTests.post
    def test_direct_upload_and_idempotent_completion(self):
        from unittest.mock import MagicMock
        client=MagicMock();client.generate_presigned_url.return_value='https://test.r2.cloudflarestorage.com/signed'
        data=fixtures.pdf_bytes()
        self.app.config.update(R2_BUCKET='test')
        with patch('builderp.cloud_uploads.s3',return_value=client),patch('builderp.store.s3',return_value=client):
            ticket=self.post('/api/projects/'+self.pid+'/upload-url',dict(filename='test.pdf',size=len(data),docKind='drawing'))
            self.assertEqual(ticket.status_code,200,ticket.data)
            client.head_object.return_value={'ContentLength':len(data)}
            client.get_object.return_value={'Body':io.BytesIO(data)}
            result=self.post('/api/projects/'+self.pid+'/upload-complete',dict(uploadId=ticket.json['uploadId']))
            self.assertEqual(result.status_code,200,result.data)
            again=self.post('/api/projects/'+self.pid+'/upload-complete',dict(uploadId=ticket.json['uploadId']))
            self.assertEqual(result.json['docId'],again.json['docId'])
            client.get_object.assert_called_once()
            client.put_object.assert_called_once()
            self.assertEqual(self.client.get('/api/documents/'+result.json['docId']+'/pdf').status_code,302)
    def test_upload_ownership_size_and_expiry(self):
        from unittest.mock import MagicMock
        client=MagicMock();client.generate_presigned_url.return_value='https://test.example/upload'
        with patch('builderp.cloud_uploads.s3',return_value=client):
            path='/api/projects/'+self.pid
            self.assertEqual(self.post(path+'/upload-url',dict(filename='a.pdf',size=11*1024*1024)).status_code,413)
            ticket=self.post(path+'/upload-url',dict(filename='a.pdf',size=100)).json['uploadId']
            client.head_object.return_value={'ContentLength':200}
            self.assertEqual(self.post(path+'/upload-complete',dict(uploadId=ticket)).status_code,413)
            with self.app.app_context():store.run('UPDATE python_uploads SET expires=0 WHERE id=?',ticket)
            self.assertEqual(self.post(path+'/upload-complete',dict(uploadId=ticket)).status_code,410)
            other=self.app.test_client();self.post('/api/auth/signup',dict(username='other',password=fixtures.PASSWORD),other)
            self.assertEqual(self.post(path+'/upload-complete',dict(uploadId=ticket),other).status_code,404)
            client.get_object.assert_not_called()


class RemoteDetectionTests(table_fixtures.DetectionWorkflowTests):
    setUp=RemoteAppTests.setUp
    tearDown=RemoteAppTests.tearDown
