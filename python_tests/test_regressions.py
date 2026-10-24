import unittest
from builderp.catalog import consolidate,family_score,suggestions
from builderp.domain import requirement_input,correct_sections,tag_evidence
from builderp.store import AppError
from builderp.equipment import match_rows

class RegressionTests(unittest.TestCase):
    def test_absent_condition_does_not_mask_invalid_types(self):
        valid=dict(title='Data',text='Submit product data.',quote='Submit product data.',type='product_data',evidenceIds=['p1-l1'],products=[])
        self.assertEqual(requirement_input(dict(valid,condition=None))['condition'],'')
        for value in [0,False,[],{},'x'*1501]:
            with self.assertRaises(AppError): requirement_input(dict(valid,condition=value))
        for value in [0,False,{},'boilers']:
            with self.assertRaises(AppError): requirement_input(dict(valid,products=value))

    def test_catalog_aliases_and_heading_recovery(self):
        evidence=[dict(id='scope',text='Provide steam boiler blowdown separators.',part='1',page=1,clause='1.1.A',section='235200'),dict(id='heading',text='B. Boiler Blowdown Separators or Tanks:',part='2',page=2,clause='2.1.B',section='235200',article='2.1',articleTitle='BOILERS'),dict(id='child',text='1. Tank: Construct of carbon steel.',part='2',page=2,clause='2.1.B.1',section='235200')]
        p=dict(id='scope-product',name='steam boiler blowdown separators',evidenceIds=['scope'],quote=evidence[0]['text'],page=1,clause='1.1.A',section='235200',usageStatus='required',selectable=True,docId='doc')
        included,excluded=consolidate([p],[],evidence,'hash')
        self.assertEqual(len(included),1)
        self.assertEqual(included[0]['name'],'Boiler Blowdown Separator / Tank')
        self.assertEqual(included[0]['evidenceIds'],['heading'])
        self.assertEqual(included[0]['aliases'][0]['id'],'scope-product')
        self.assertFalse(excluded[0]['selectable']); self.assertIn('mergedInto',excluded[0])

    def test_unmatched_scope_is_preserved(self):
        p=dict(id='p',name='boilers',evidenceIds=['a'],page=1,clause='1.1',selectable=True)
        included,excluded=consolidate([p],[],[dict(id='a',part='1',text='Boilers',clause='1.1')],'hash')
        self.assertEqual(included,[p]);self.assertEqual(excluded,[])

    def test_suggestions_match_item_family(self):
        self.assertGreater(family_score('safety relief valves','Water Relief Valves'),0)
        self.assertEqual(family_score('safety relief valves','Temperature Regulating Valve'),0)
        products=[dict(id='relief',name='Water Relief Valves'),dict(id='strainer',name='Y-type strainer')]
        scores=suggestions(dict(title='Gag for safety relief valves',text='',quote=''),products)
        self.assertIn('relief',scores); self.assertNotIn('strainer',scores)

    def test_checklist_stays_excluded_until_new_section(self):
        lines=['SECTION 23 52 00','SUBMITTAL CHECKLIST','Submit product data.','SECTION 23 52 00','Submit warranties.','SECTION 23 53 00','Submit test reports.']
        evidence=[dict(id=str(i),text=text) for i,text in enumerate(lines)]
        tagged,_=tag_evidence(evidence)
        self.assertNotIn('2',{e['id'] for e in tagged});self.assertNotIn('4',{e['id'] for e in tagged});self.assertIn('6',{e['id'] for e in tagged})

    def test_decimal_measurements_do_not_reassign_article(self):
        texts=['SECTION 23 52 00 BOILERS','PART 2 PRODUCTS','2.1 BOILERS','A. Body requirements:','0.5 mm thickness','B. Controls']
        evidence=[dict(id=str(i),text=text,page=1,box=[.1,.05 if i==0 else .2+i*.06,.8,.02],fontSize=12,bold=i in (1,2),source='pdf',section='Document',clause='') for i,text in enumerate(texts)]
        parsed=correct_sections(dict(evidence=evidence,pages=1,sections=[]))
        self.assertEqual(parsed['evidence'][-1]['clause'],'2.1.B')

    def test_equipment_dedupe_prefers_richer_source_row(self):
        rows=[dict(schedule_title='Exhaust fan schedule',table_id='a',document={'drawing_id':'d'},row={'TAG':'EF-1'}),dict(schedule_title='Exhaust fan schedule',table_id='b',document={'drawing_id':'d'},row={'TAG':'EF-1','CFM':'100','RPM':'500'})]
        result=match_rows(rows);self.assertEqual(len(result),1);self.assertEqual(result[0]['tableId'],'b')

if __name__=='__main__': unittest.main()
