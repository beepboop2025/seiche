"""Release guards for static question answers, source clocks and discovery."""
import copy
import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest

ROOT=next(p for p in pathlib.Path(__file__).resolve().parents if (p/'scripts/build_question_pages.py').exists())
spec=importlib.util.spec_from_file_location('question_pages_builder',ROOT/'scripts/build_question_pages.py')
builder=importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

class QuestionPageTests(unittest.TestCase):
    def setUp(self):
        self.catalog=json.loads((builder.PUBLIC/'questions/pages.json').read_text())

    def test_published_answers_and_editorial_sitemap_dates_match_review(self):
        subprocess.run([sys.executable,str(ROOT/'scripts/build_question_pages.py'),'--check'],check=True,capture_output=True)

    def test_retained_observation_cannot_be_relabelled_as_future_evidence(self):
        self.catalog['pages'][0]['observations']=['2099-01-01']
        with self.assertRaises(ValueError):builder.validate(self.catalog)

    def test_duplicate_stable_addresses_are_rejected(self):
        self.catalog['pages'].append(copy.deepcopy(self.catalog['pages'][0]))
        with self.assertRaises(ValueError):builder.validate(self.catalog)

    def test_no_answer_without_evidence_or_limits(self):
        for field in ['tables','limits','sources']:
            altered=copy.deepcopy(self.catalog);altered['pages'][0][field]=[]
            with self.assertRaises(ValueError):builder.validate(altered)

    def test_alias_noindex_does_not_cover_live_discovery_origins(self):
        if self.catalog['brand']!='Seiche':return
        headers=(builder.PUBLIC/'_headers').read_text()
        blocks=headers.split('\nhttps://seiche.pages.dev')[1:]
        self.assertTrue(blocks)
        for path in ['/robots.txt','/sitemap.xml','/llms.txt','/.well-known/ai-catalog.json']:
            for block in blocks:
                rule=block.splitlines()[0]
                self.assertFalse(path==rule or rule.endswith('*') and path.startswith(rule[:-1]))
        self.assertNotIn('https://seiche.pages.dev/*\n',headers)

if __name__=='__main__':unittest.main()
