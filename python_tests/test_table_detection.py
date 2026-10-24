"""Workflow tests run without OpenCV; native tests require requirements.txt."""
import importlib.util
import unittest
from unittest.mock import patch

import fitz
import test_app as fixtures
from builderp import pdf, table_jobs
from builderp.store import AppError, get_json, put_json, run, now


def drawing_pdf(pages=1, rotation=0, tables=True, scanned=False):
    with fitz.open() as doc:
        for _ in range(pages):
            page = doc.new_page(width=600, height=800)
            if tables:
                for top in (100, 460):
                    for x in range(60, 421, 90):
                        page.draw_line((x, top), (x, top + 200), width=1)
                    for y in range(top, top + 201, 40):
                        page.draw_line((60, y), (420, y), width=1)
                    page.insert_text((65, top - 8), 'FAN SCHEDULE', fontsize=10)
                # A narrow title-block grid on the far right should be filtered.
                for x in range(530, 591, 20):
                    page.draw_line((x, 100), (x, 700), width=1)
                for y in range(100, 701, 50):
                    page.draw_line((530, y), (590, y), width=1)
            if scanned:
                image = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes('png')
                doc.delete_page(-1)
                page = doc.new_page(width=600, height=800)
                page.insert_image(page.rect, stream=image)
            page.set_rotation(rotation)
        return doc.tobytes()


def proposed(page=1):
    return [dict(id=f'detected-{page}', page=page, box=[.1,.1,.5,.3], raw_box=[.11,.12,.48,.27])], dict(page=page,width=1200,height=1600,dpi=144,cells=20,tables=1)


class DetectionWorkflowTests(unittest.TestCase):
    setUp = fixtures.AppTests.setUp
    tearDown = fixtures.AppTests.tearDown
    post = fixtures.AppTests.post
    upload = fixtures.AppTests.upload

    def url(self, action):
        return '/api/documents/' + self.did + '/' + action

    def start(self, pages=1):
        self.did = self.upload('drawing', drawing_pdf(pages=pages))
        with patch('builderp.table_jobs.detect_page', side_effect=lambda data,p: proposed(p)):
            for _ in range(pages):
                result = self.post(self.url('prepare-drawing-crops'))
                self.assertEqual(result.status_code, 200, result.data)
        return result.json

    def approve(self, revision=0, detection_revision=1, ids=None):
        return self.post(self.url('accept-tables'), dict(revision=revision,
                         detection_revision=detection_revision, ids=ids or ['detected-1']))

    def test_resumable_page_detection_and_read_only_status(self):
        self.did = self.upload('drawing', drawing_pdf(pages=2))
        self.assertEqual(self.client.get(self.url('table-detection')).json['cursor'], 0)
        with patch('builderp.table_jobs.detect_page', return_value=proposed()) as detector:
            first = self.post(self.url('prepare-drawing-crops')).json
            self.assertEqual(first['cursor'], 1)
            self.assertEqual(first['progress'], 50)
            self.assertFalse(first['done'])
            self.assertEqual(detector.call_args.args[1], 1)
        with patch('builderp.table_jobs.detect_page', side_effect=AppError(422, 'Unreadable page.')):
            self.assertEqual(self.post(self.url('prepare-drawing-crops')).status_code, 422)
        paused = self.client.get(self.url('table-detection')).json
        self.assertEqual(paused['cursor'], 1)
        self.assertEqual(paused['error'], 'Unreadable page.')
        with patch('builderp.table_jobs.detect_page', return_value=proposed(2)) as detector:
            last = self.post(self.url('prepare-drawing-crops')).json
            self.assertEqual(detector.call_args.args[1], 2)
            self.assertTrue(last['done'])
            self.assertFalse(last['busy'])
            self.assertEqual(len(last['proposals']), 2)
            self.post(self.url('prepare-drawing-crops'))
            self.assertEqual(detector.call_count, 1)
        self.assertEqual(self.client.get(self.url('crops')).json['crops'], [])

    def test_accept_preserves_manual_and_is_idempotent(self):
        self.start()
        manual = dict(id='manual',page=1,box=[.01,.01,.1,.1])
        self.post(self.url('crops'), dict(revision=0,crops=[manual]))
        accepted = self.approve(revision=1).json
        self.assertEqual(accepted['revision'], 2)
        self.assertEqual(accepted['crops'][0], manual)
        edited = accepted['crops']; edited[1]['box'] = [.1,.1,.4,.2]
        self.post(self.url('crops'),dict(revision=2,crops=edited))
        with self.app.app_context():
            put_json('python/schedule-rows/'+self.did+'.json', {'rows':[{'table_id':'detected-1'}]})
        again = self.approve(revision=3).json
        self.assertEqual(again['revision'], 3)
        self.assertEqual(again['crops'], edited)
        with self.app.app_context():
            self.assertEqual(len(get_json('python/schedule-rows/'+self.did+'.json')['rows']),1)

    def test_stale_and_invalid_approval_rejected(self):
        self.start()
        self.assertEqual(self.approve(detection_revision=0).status_code,409)
        self.assertEqual(self.approve(revision=10).status_code,409)
        self.assertEqual(self.approve(ids=['missing']).status_code,400)
        self.assertEqual(self.post(self.url('accept-tables'),dict(ids=[],revision=0,detection_revision=1)).status_code,400)
        self.assertEqual(self.approve(ids=['detected-1','detected-1']).status_code,200)
        self.assertEqual(len(self.client.get(self.url('crops')).json['crops']),1)

    def test_reset_preserves_saved_crops_and_extraction(self):
        self.start(); self.approve()
        with self.app.app_context():
            put_json('python/schedule-rows/'+self.did+'.json', {'rows':[{'table_id':'detected-1'}]})
        self.assertEqual(self.post(self.url('reset-table-detection'),dict(revision=0)).status_code,409)
        reset=self.post(self.url('reset-table-detection'),dict(revision=1)).json
        self.assertEqual(reset['cursor'],0); self.assertEqual(reset['revision'],2)
        self.assertFalse(reset['done']); self.assertEqual(reset['proposals'],[])
        self.assertEqual(len(self.client.get(self.url('crops')).json['crops']),1)
        with self.app.app_context(): self.assertEqual(len(get_json('python/schedule-rows/'+self.did+'.json')['rows']),1)

    def test_detection_and_extraction_locks(self):
        self.start()
        with self.app.app_context():
            run("UPDATE python_jobs SET lease=? WHERE doc_id=? AND kind='detect-tables'",now()+10000,self.did)
        self.assertEqual(self.post(self.url('reset-table-detection'),dict(revision=1)).status_code,409)
        self.assertEqual(self.post(self.url('prepare-drawing-crops')).status_code,409)
        with self.app.app_context():
            run("INSERT INTO python_jobs(doc_id,kind,lease) VALUES (?,?,?)",self.did,'drawing',now()+10000)
        self.assertEqual(self.approve().status_code,409)
        self.assertEqual(self.client.get(self.url('crops')).json['crops'],[])

    def test_expired_detection_cannot_commit(self):
        self.did=self.upload('drawing',drawing_pdf())
        def expired(*args):
            run("UPDATE python_jobs SET lease=0 WHERE doc_id=? AND kind='detect-tables'",self.did)
            return proposed()
        with patch('builderp.table_jobs.detect_page',side_effect=expired):
            self.assertEqual(self.post(self.url('prepare-drawing-crops')).status_code,409)
        self.assertEqual(self.client.get(self.url('table-detection')).json['cursor'],0)

    def test_only_accepted_tables_are_extracted(self):
        self.start(pages=2)
        self.assertEqual(self.post(self.url('process-drawing')).status_code,409)
        self.assertEqual(self.approve(detection_revision=2).status_code,200)
        with patch('builderp.ai.generate', return_value={'schedule_title':'Fan','rows':[{'TAG':'EF-1'}]}) as ai:
            result=self.post(self.url('process-drawing'))
            self.assertTrue(result.json['done'])
            self.assertIn('detected-1',ai.call_args.args[1])
            with fitz.open(stream=ai.call_args.args[3],filetype='pdf') as crop:
                self.assertEqual(len(crop),1)
                self.assertAlmostEqual(crop[0].rect.width,300)
            self.post(self.url('process-drawing'))
            self.assertEqual(ai.call_count,1)

    def test_empty_detection_and_missing_dependency(self):
        self.did=self.upload('drawing',drawing_pdf(tables=False))
        with patch('builderp.table_jobs.load_detector',side_effect=AppError(503,'Install requirements.txt.')):
            self.assertEqual(self.post(self.url('prepare-drawing-crops')).status_code,503)
        self.assertEqual(self.client.get(self.url('table-detection')).json['cursor'],0)
        with patch('builderp.table_jobs.detect_page',return_value=([],{'page':1,'tables':0})):
            self.assertTrue(self.post(self.url('prepare-drawing-crops')).json['done'])
        view=self.client.get('/documents/'+self.did+'?tab=crops')
        self.assertIn(b'No ruled tables were found',view.data)

    def test_review_forms_preview_and_edit_work_without_javascript(self):
        self.start()
        view=self.client.get('/documents/'+self.did+'?tab=crops')
        self.assertEqual(view.status_code,200)
        self.assertIn(b'Accept selected tables',view.data)
        with self.client.session_transaction() as session: csrf=session['csrf']
        response=self.client.post('/documents/'+self.did+'/tables',data=dict(_csrf=csrf,revision=0,detection_revision=1,ids='detected-1'))
        self.assertEqual(response.status_code,302)
        view=self.client.get('/documents/'+self.did+'?tab=crops&edit=detected-1&regions=saved')
        self.assertIn(b'Edit saved region',view.data)
        response=self.client.post('/documents/'+self.did+'/crops',data=dict(_csrf=csrf,revision=1,crop_id='detected-1',page=1,x=.1,y=.12,w=.5,h=.2))
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.get(self.url('crops')).json['crops'][0]['box'],[.1,.12,.5,.2])
        for region in ('detected','saved'):
            image=self.client.get(self.url('page')+'?page=1&regions='+region)
            self.assertEqual(image.status_code,200)
            self.assertTrue(image.data.startswith(b'\x89PNG'))

    def test_tenant_isolation_and_origin_checks(self):
        self.start()
        other=self.app.test_client()
        self.post('/api/auth/signup',dict(username='other',password=fixtures.PASSWORD),other)
        for action in ('table-detection','crops','page?regions=detected'):
            self.assertEqual(other.get(self.url(action)).status_code,404)
        for action in ('prepare-drawing-crops','accept-tables','reset-table-detection'):
            self.assertEqual(self.post(self.url(action),client=other).status_code,404)
            self.assertEqual(self.client.post(self.url(action),json={}).status_code,403)
        self.assertEqual(self.client.post('/documents/'+self.did+'/tables',data={}).status_code,403)
        self.did=self.upload('spec')
        self.assertEqual(self.post(self.url('prepare-drawing-crops')).status_code,400)

    def test_candidate_limit_does_not_commit_partial_page(self):
        self.did=self.upload('drawing',drawing_pdf())
        candidates=[dict(id=str(i),page=1,box=[.1,.1,.2,.2]) for i in range(201)]
        with patch('builderp.table_jobs.detect_page',return_value=(candidates,{})):
            self.assertEqual(self.post(self.url('prepare-drawing-crops')).status_code,422)
        self.assertEqual(self.client.get(self.url('table-detection')).json['proposals'],[])


class DetectionGeometryTests(unittest.TestCase):
    def test_bounded_rendering_respects_rotation(self):
        for rotation in (0,90,180,270):
            image,geometry=table_jobs.render_for_detection(drawing_pdf(rotation=rotation),1)
            pix=fitz.Pixmap(image)
            self.assertEqual((pix.width,pix.height),(geometry['width'],geometry['height']))
            self.assertEqual(pix.n,1)
            self.assertLessEqual(pix.width*pix.height,table_jobs.MAX_PIXELS)
            self.assertEqual(geometry['page_width'],800 if rotation in (90,270) else 600)
        with fitz.open() as doc:
            doc.new_page(width=10000,height=8000)
            _,geometry=table_jobs.render_for_detection(doc.tobytes(),1)
        self.assertLessEqual(geometry['width']*geometry['height'],table_jobs.MAX_PIXELS)
        self.assertLessEqual(max(geometry['width'],geometry['height']),table_jobs.MAX_SIDE)
        self.assertLess(geometry['dpi'],200)

    def test_cropbox_offsets_preserve_selected_content(self):
        with fitz.open() as doc:
            p=doc.new_page(width=800,height=1000)
            p.insert_text((240,340),'INSIDE')
            p.insert_text((40,50),'OUTSIDE')
            p.set_cropbox(fitz.Rect(200,300,700,900))
            for rotation in (0,90,180,270):
                p.set_rotation(rotation)
                with fitz.open(stream=pdf.subset(doc.tobytes(),1,[0,0,1,1]),filetype='pdf') as cropped:
                    self.assertEqual(cropped[0].cropbox,fitz.Rect(200,300,700,900))
                    self.assertIn('INSIDE',cropped[0].get_text())
                    self.assertNotIn('OUTSIDE',cropped[0].get_text())
                with fitz.open(stream=pdf.subset(doc.tobytes(),1,[.1,.1,.5,.5]),filetype='pdf') as cropped:
                    expected=fitz.Rect(.1*p.rect.width,.1*p.rect.height,.6*p.rect.width,.6*p.rect.height)*p.derotation_matrix
                    expected=fitz.Rect(expected.x0+200,expected.y0+300,expected.x1+200,expected.y1+300)
                    self.assertEqual(cropped[0].cropbox,expected)

    def test_normalization_padding_and_invalid_regions(self):
        geometry=dict(width=1200,height=1600,page_width=600,page_height=800)
        self.assertEqual(table_jobs.normalize_box((120,160,600,800),geometry,False),[.1,.1,.5,.5])
        padded=table_jobs.normalize_box((0,0,1200,1600),geometry)
        self.assertEqual(padded,[0,0,1,1])
        for box in [(0,0,0,1),(0,0,float('nan'),1),(2000,0,100,100)]:
            with self.assertRaises(AppError): table_jobs.normalize_box(box,geometry)


@unittest.skipUnless(importlib.util.find_spec('cv2'), 'OpenCV unavailable; install requirements.txt to run native detector tests')
class NativeDetectorTests(unittest.TestCase):
    def test_separate_tables_and_title_block_filter(self):
        candidates,info=table_jobs.detect_page(drawing_pdf(),1)
        self.assertEqual(len(candidates),2)
        self.assertGreater(info['cells'],20)
        for box,top in zip(candidates,(100,460)):
            self.assertLess(abs(box['raw_box'][0]-.1),.01)
            self.assertLess(abs(box['raw_box'][1]-top/800),.01)
            self.assertGreater(box['box'][3],box['raw_box'][3])
        again,_=table_jobs.detect_page(drawing_pdf(),1)
        self.assertEqual([p['id'] for p in candidates],[p['id'] for p in again])

    def test_scanned_and_rotated_grids(self):
        for rotation in (0,90,180,270):
            candidates,_=table_jobs.detect_page(drawing_pdf(rotation=rotation,scanned=True),1)
            # The original right-edge title strip rotates away from that edge;
            # inspect the two known schedule regions independently of that heuristic.
            schedules=[c for c in candidates if c['raw_box'][2]*c['raw_box'][3]>.1]
            self.assertEqual(len(schedules),2)
            for candidate in schedules:
                cropped=pdf.subset(drawing_pdf(rotation=rotation,scanned=True),1,candidate['box'])
                self.assertTrue(cropped.startswith(b'%PDF-'))

    def test_blank_and_border_only_pages(self):
        self.assertEqual(table_jobs.detect_page(drawing_pdf(tables=False),1)[0],[])
        with fitz.open() as doc:
            p=doc.new_page(width=600,height=800)
            p.draw_rect(fitz.Rect(60,100,420,300),width=1)
            self.assertEqual(table_jobs.detect_page(doc.tobytes(),1)[0],[])

    def test_input_errors_and_work_bound(self):
        from builderp.table_detection import detect_grid_lines
        for data in (b'',b'not an image'):
            with self.assertRaises(ValueError): detect_grid_lines(data)
        with self.assertRaises(ValueError): detect_grid_lines(b'',line_kernel_divisor=0)
        with patch('builderp.table_detection.detect_cells',return_value=[None]*2501):
            with self.assertRaises(AppError): table_jobs.detect_page(drawing_pdf(),1)


if __name__=='__main__': unittest.main()
