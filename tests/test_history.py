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
    def test_markdown_queue_lists_every_entry_in_priority_order(self):
        queue=matrix()['research_queue']
        section=markdown_index().split('## 研究队列\n',1)[1]
        ids=[line.split(' | `',1)[1].split('`',1)[0]
             for line in section.splitlines()
             if line.startswith('| ') and ' | `' in line]
        self.assertEqual(ids,[item['entry_id'] for item in queue])
        self.assertEqual(len(ids),len(set(ids)))
        self.assertTrue(any(item['status']=='candidate' for item in queue))
        self.assertIn('2026年已收录的传播事件',markdown_index())

    def test_2026_navigation_covers_existing_autumn_batch(self):
        from atlas import load_catalogue
        entries,_,_=load_catalogue(ROOT)
        page=(ROOT/'references'/'history-map.md').read_text(encoding='utf-8')
        section=page.split('## 2026年夏末至秋季的已收录传播\n',1)[1].split('\n## ',1)[0]
        entry_ids=('recent-26-shanshenbu','recent-26-gouxiong','recent-26-world-tour',
                   'recent-26-zhuanben','recent-26-suanle','recent-26-feidudu',
                   'recent-26-bushaoxin','recent-26-hongguoti','recent-26-boji',
                   'recent-26-dashui','recent-26-huangmao','recent-26-bluefish',
                   'recent-26-yuzai','recent-26-delta-call')
        by_id={entry['id']:entry for entry in entries}
        for entry_id in entry_ids:
            with self.subTest(entry_id=entry_id):
                self.assertIn(by_id[entry_id]['term'],section)

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
