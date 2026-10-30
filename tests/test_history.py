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


class HistoricalEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atlas import load_catalogue
        cls.entries, cls.sources, _ = load_catalogue(ROOT)
        cls.by_id = {entry['id']: entry for entry in cls.entries}

    def test_related_family_terms_are_not_exact_aliases(self):
        from atlas import get_entries, search
        entry = self.by_id['early-german-orthopedics']
        self.assertNotIn('伪骨科', entry['aliases'])
        self.assertIn('伪骨科', entry['tags'])
        self.assertEqual(get_entries(self.entries, '伪骨科'), [])
        result = search(self.entries, '伪骨科')
        self.assertIn(entry['id'], [row['entry']['id'] for row in result['results']])

    def test_lancer_attribute_is_not_universalized(self):
        entry = self.by_id['early-lancer-luck']
        self.assertIn('库丘林', entry['meaning'])
        self.assertIn('迪卢木多', entry['meaning'])
        self.assertIn('并非所有Lancer', entry['meaning'])
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('meaning', refs['early-src-lancer-mooncell']['claims'])
        self.assertEqual(refs['early-src-lancer-bili-quiz']['claims'], ['platform_usage'])

    def test_kuroko_uploader_is_not_promoted_to_original_creator(self):
        from atlas import search
        entry = self.by_id['early-kuroko']
        self.assertEqual(entry['origin']['bilibili_role'], 'imported')
        self.assertIn('sm13060333', entry['origin']['summary'])
        self.assertEqual(entry['evidence_status'], 'candidate')
        self.assertFalse(any(
            'meaning' in ref['claims'] and
            self.sources[ref['source_id']]['access_level'] in ('full', 'snippet')
            for ref in entry['evidence']))
        result = search(self.entries, entry['term'])
        self.assertNotIn(entry['id'], [row['entry']['id'] for row in result['results']])
        self.assertGreaterEqual(result['candidate_matches_excluded'], 1)
        included = search(self.entries, entry['term'], include_candidates=True)
        self.assertIn(entry['id'], [row['entry']['id'] for row in included['results']])

    def test_senbonzakura_author_date_is_separate_from_arrangement(self):
        entry = self.by_id['early-senbonzakura']
        self.assertEqual(entry['origin']['date'], '2011-09-17')
        self.assertEqual(entry['origin']['precision'], 'exact')
        original = self.sources['early-src-senbonzakura-official']
        self.assertEqual((original['source_type'], original['access_level']), ('primary', 'full'))
        self.assertIn('2011年9月17日', original['supports'])
        arrangement = self.sources['early-src-guzheng']
        self.assertEqual(arrangement['access_level'], 'full')
        self.assertIn('2014年7月', arrangement['supports'])

    def test_song_and_pv_keep_separate_dates(self):
        entry = self.by_id['early-millennium-food-song']
        self.assertEqual(entry['origin']['date'], '2012-06-29')
        self.assertIn('2012-07-13', entry['origin']['summary'])
        self.assertNotEqual(entry['id'], self.by_id['early-food-princess']['id'])
        self.assertEqual(self.sources['early-src-luotianyi-character']['access_level'], 'full')
        self.assertIn('未列原曲和PV的具体投稿日期',
                      self.sources['early-src-luotianyi-character']['limitations'])

    def test_music_distribution_nodes_are_not_collapsed(self):
        self.assertIn('2010年1月27日', self.sources['early-src-renai-disc']['supports'])
        self.assertIn('2011年', self.sources['early-src-renai-official']['limitations'])
        self.assertIn('3月3日', self.sources['early-src-platinum-official']['supports'])
        self.assertIn('2012年7月25日', self.sources['early-src-platinum-disc']['supports'])
        self.assertEqual(self.by_id['early-love-circulation']['origin']['date'], None)
        self.assertEqual(self.by_id['early-platinum-disco']['origin']['date'], '2012')

    def test_upgraded_meanings_are_available_to_default_search(self):
        from atlas import search
        identifiers = (
            'early-german-orthopedics', 'early-lancer-luck', 'early-nico-nico-ni',
            'early-red-blue-cp', 'early-god-sync', 'early-little-education',
            'early-source-of-all', 'early-dali', 'early-downfall', 'early-geping',
            'early-cirno-classroom', 'early-food-princess', 'early-love-circulation',
            'early-millennium-food-song', 'early-platinum-disco',
            'early-senbonzakura', 'early-skate-shoes',
        )
        for identifier in identifiers:
            with self.subTest(identifier=identifier):
                entry = self.by_id[identifier]
                result = search(self.entries, entry['term'])
                self.assertIn(identifier, [row['entry']['id'] for row in result['results']])

    def test_verified_records_with_unknown_dates_remain_queued(self):
        queued = {row['entry_id'] for row in matrix()['research_queue']}
        unknown = [entry for entry in self.entries
                   if entry['evidence_status'] == 'verified' and entry['origin']['date'] is None]
        self.assertTrue(unknown)
        for entry in unknown:
            with self.subTest(identifier=entry['id']):
                self.assertIn(entry['id'], queued)
        self.assertNotIn('early-senbonzakura', queued)

    def test_shared_source_reading_range_is_synchronized(self):
        source = self.sources['early-src-guichu-history']
        self.assertEqual(source['access_level'], 'full')
        entry = self.by_id['early-hebei']
        self.assertNotIn('摘要', entry['notes'])
        for ref in entry['evidence']:
            if ref['source_id'] == source['id']:
                self.assertNotIn('摘要', ref['note'])
