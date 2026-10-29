import os
import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from history import main,markdown_index,matrix,CATEGORIES
from test_contracts import create_symlink
ROOT=Path(__file__).resolve().parents[1]
class HistoryTests(unittest.TestCase):
    def test_matrix_has_each_year_category_once(self):
        data=matrix();end=int(data['cutoff'][:4]); keys=[(r['year'],r['category']) for r in data['cells']]
        self.assertEqual(len(keys),len(set(keys)))
        self.assertEqual(set(keys),{(y,c) for y in range(2009,end+1) for c in CATEGORIES})
    def test_unknown_year_is_not_assigned_to_each_era_year(self):
        data=matrix()
        for cell in data['cells']:
            self.assertNotIn('early-233',cell['date_supported_entry_ids'])
    def test_candidates_are_prioritized(self):
        data=matrix();priorities=[x['priority'] for x in data['research_queue']]
        self.assertEqual(priorities,sorted(priorities))
    def test_maintained_markdown_uses_current_matrix_counts(self):
        data=matrix();text=markdown_index()
        self.assertIn(f"当前队列{len(data['research_queue'])}条",text)
        self.assertIn(f"输出为{len(data['cells'])}个年份×分类格",text)
        self.assertEqual(text,(ROOT/'references'/'history-index.md').read_text(encoding='utf-8'))
    def test_output_must_be_new_and_outside_project(self):
        inside=ROOT/'history-output.json'
        inside.unlink(missing_ok=True)
        self.assertEqual(main(['--output',str(inside)]),2)
        self.assertFalse(inside.exists())
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);output=base/'history.json'
            self.assertEqual(main(['--output',str(output)]),0)
            self.assertEqual(__import__('json').loads(output.read_text(encoding='utf-8'))['format'],
                             'bilibili-meme-atlas-history-matrix/v2')
            self.assertEqual(main(['--output',str(output)]),2)
    def test_output_parent_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);real=base/'real';real.mkdir();linked=base/'linked'
            create_symlink(self, linked, real, target_is_directory=True)
            output=linked/'history.json'
            self.assertEqual(main(['--output',str(output)]),2)
            self.assertFalse((real/'history.json').exists())
