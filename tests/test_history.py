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
        self.assertIn('recent-real-person', {item['entry_id'] for item in queue})
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
        self.assertIn('上传者', entry['origin']['summary'])
        self.assertEqual(entry['evidence_status'], 'partial')
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertEqual(self.sources['early-src-kuroko-materials']['access_level'], 'full')
        self.assertIn('meaning', refs['early-src-kuroko-materials']['claims'])
        self.assertNotIn('origin', refs['early-src-kuroko-materials']['claims'])
        result = search(self.entries, entry['term'])
        self.assertIn(entry['id'], [row['entry']['id'] for row in result['results']])

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


class MidEraEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atlas import load_catalogue
        cls.entries, cls.sources, cls.metadata = load_catalogue(ROOT)
        cls.by_id = {entry['id']: entry for entry in cls.entries}

    def test_supported_queue_entries_are_available_to_default_search(self):
        from atlas import search
        identifiers = (
            'early-kuroko', 'mid-face-position', 'mid-thanks-treat', 'mid-excellent',
            'mid-forgive', 'mid-good-grief', 'mid-heart-pierced', 'mid-i-can',
            'mid-landlord-son', 'mid-lawyer-letter', 'mid-main-start',
            'mid-standard-ending', 'mid-take-photo', 'mid-teary', 'mid-welcome-home',
            'mid-famous-scene', 'mid-rich-power', 'mid-such-operation',
            'mid-mianjin-family', 'mid-nianshi-king', 'mid-no-work',
            'mid-soul-injection', 'mid-pretty-warning', 'mid-shadow-lord',
            'mid-battle-song', 'mid-call', 'mid-deja-vu', 'mid-different-song',
            'mid-singing-awe', 'mid-skr',
        )
        for identifier in identifiers:
            with self.subTest(identifier=identifier):
                entry = self.by_id[identifier]
                result = search(self.entries, entry['term'])
                self.assertIn(identifier, [row['entry']['id'] for row in result['results']])

    def test_candidate_filter_is_independent_of_a_corpus_status_change(self):
        from copy import deepcopy
        from atlas import search
        entries = deepcopy(self.entries)
        entry = next(row for row in entries if row['id'] == 'early-kuroko')
        entry['evidence_status'] = 'candidate'
        result = search(entries, entry['term'])
        self.assertNotIn(entry['id'], [row['entry']['id'] for row in result['results']])
        self.assertGreaterEqual(result['candidate_matches_excluded'], 1)
        result = search(entries, entry['term'], include_candidates=True)
        self.assertIn(entry['id'], [row['entry']['id'] for row in result['results']])

    def test_partial_without_meaning_evidence_is_rejected_without_writing(self):
        from copy import deepcopy
        from update import UpdateError, digest_record, fingerprint, plan
        original = self.by_id['early-kuroko']
        replacement = deepcopy(original)
        replacement['evidence'] = [
            ref for ref in replacement['evidence'] if 'meaning' not in ref['claims']
        ]
        before = fingerprint(ROOT)
        proposal = {
            'format': 'bilibili-meme-atlas-update/v1',
            'base_manifest_sha256': before,
            'checked_at': self.metadata['knowledge_cutoff'],
            'sources': [],
            'entries': [{'action': 'replace', 'before_sha256': digest_record(original),
                         'record': replacement}],
        }
        with self.assertRaisesRegex(UpdateError, '释义'):
            plan(ROOT, proposal)
        self.assertEqual(fingerprint(ROOT), before)

    def test_related_labels_remain_searchable_without_becoming_exact_aliases(self):
        from atlas import get_entries, search
        cases = (
            ('mid-forgive', '原谅色'),
            ('mid-rich-power', '为所欲为'),
            ('mid-no-work', '窃格瓦拉'),
        )
        for identifier, label in cases:
            with self.subTest(identifier=identifier, label=label):
                entry = self.by_id[identifier]
                self.assertNotIn(label, entry['aliases'])
                self.assertIn(label, entry['tags'])
                self.assertNotIn(identifier, [row['id'] for row in get_entries(self.entries, label)])
                result = search(self.entries, label)
                self.assertIn(identifier, [row['entry']['id'] for row in result['results']])

    def test_danmaku_meanings_retain_context_and_polarity(self):
        self.assertIn('弹幕颜色', self.by_id['mid-face-position']['meaning'])
        self.assertIn('美食', self.by_id['mid-battle-song']['meaning'])
        self.assertIn('调侃', self.by_id['mid-singing-awe']['meaning'])
        self.assertIn('不是打电话', self.by_id['mid-call']['meaning'])
        self.assertIn('竹鼠', self.by_id['mid-pretty-warning']['meaning'])

    def test_remix_and_source_material_have_separate_creators(self):
        entry = self.by_id['mid-nianshi-king']
        for name in ('小可儿', '赵本山', '疯猴PME'):
            self.assertIn(name, entry['origin']['summary'])
        self.assertEqual(entry['origin']['bilibili_role'], 'adaptation')
        self.assertEqual(entry['origin']['date'], '2018-02-11')
        reference = next(ref for ref in entry['evidence']
                         if ref['source_id'] == 'mid-src-nianshi-authors')
        self.assertNotIn('date', reference['claims'])
        self.assertEqual(self.sources['mid-src-nianshi']['access_level'], 'metadata')

    def test_representative_publication_and_event_dates_are_not_coining_dates(self):
        self.assertEqual(self.by_id['mid-mianjin-family']['origin']['date'], '2018-03-23')
        self.assertIn('不是原选秀', self.by_id['mid-mianjin-family']['origin']['summary'])
        self.assertEqual(self.by_id['mid-lawyer-letter']['origin']['date'], '2019-04-12')
        self.assertIn('不是短语', self.by_id['mid-lawyer-letter']['notes'])
        self.assertIsNone(self.by_id['mid-welcome-home']['origin']['date'])
        self.assertIsNone(self.by_id['mid-good-grief']['origin']['date'])

    def test_song_composition_performance_and_platform_dates_remain_distinct(self):
        deja = self.by_id['mid-deja-vu']
        self.assertIsNone(deja['origin']['date'])
        source = self.sources['mid-src-dejavu-rodgers-interview']
        self.assertIn('1997', source['supports'])
        self.assertIn('创作', source['limitations'])
        self.assertIn('不等同', source['limitations'])
        different = self.by_id['mid-different-song']
        self.assertIn('大壮演唱', different['origin']['summary'])
        self.assertIn('高进作曲', different['origin']['summary'])
        self.assertEqual(self.sources['mid-src-different-credits']['access_level'], 'metadata')
        self.assertEqual(different['evidence_status'], 'partial')

    def test_shared_source_body_support_and_existing_reference_note_agree(self):
        self.assertEqual(self.sources['mid-src-2017-publisher']['access_level'], 'full')
        entry = self.by_id['mid-prawn']
        reference = next(ref for ref in entry['evidence']
                         if ref['source_id'] == 'mid-src-2017-publisher')
        self.assertIn('正文', reference['note'])
        self.assertNotIn('摘要', reference['note'])
        self.assertEqual(self.sources['mid-src-kubenchina']['access_level'], 'full')
        reference = next(ref for ref in self.by_id['mid-mianjin-family']['evidence']
                         if ref['source_id'] == 'mid-src-kubenchina')
        self.assertEqual(set(reference['claims']), {'meaning', 'origin', 'platform_usage'})

    def test_unresolved_first_dates_remain_in_research_queue(self):
        queued = {row['entry_id'] for row in matrix()['research_queue']}
        for identifier in ('mid-no-work', 'mid-pretty-warning', 'mid-deja-vu'):
            with self.subTest(identifier=identifier):
                entry = self.by_id[identifier]
                self.assertEqual(entry['evidence_status'], 'verified')
                self.assertIsNone(entry['origin']['date'])
                self.assertIn(identifier, queued)


class QueueFrontEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atlas import load_catalogue
        cls.entries, cls.sources, _ = load_catalogue(ROOT)
        cls.by_id = {entry['id']: entry for entry in cls.entries}

    def test_real_person_usage_is_tied_to_the_actual_program(self):
        entry = self.by_id['recent-real-person']
        self.assertEqual(entry['evidence_status'], 'partial')
        self.assertIn('《马上有奇迹》', entry['origin']['summary'])
        self.assertIn('首创来源', entry['notes'])
        self.assertEqual(self.sources['recent-src-gala26-program']['access_level'], 'full')

    def test_world_tour_meaning_is_limited_to_creator_described_ai_video(self):
        entry = self.by_id['recent-26-world-tour']
        self.assertEqual(entry['evidence_status'], 'partial')
        self.assertIn('音乐是否由AI生成须按具体作品判断', entry['meaning'])
        self.assertEqual(self.sources['recent-src-26-tour-michael']['access_level'], 'full')
        self.assertIn('尚未确定最早创作者', entry['notes'])

    def test_green_cat_records_a_locatable_creator_post_without_claiming_first_origin(self):
        entry = self.by_id['recent-green-alien-cat']
        self.assertEqual(entry['evidence_status'], 'partial')
        self.assertIn('2026年3月8日', entry['origin']['summary'])
        self.assertEqual(self.sources['recent-src-green-cat-original-post']['access_level'], 'full')
        self.assertIn('尚未确认绿色外星猫形象本身的创作者', entry['notes'])


class RealPersonQueueEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atlas import load_catalogue
        cls.entries, cls.sources, _ = load_catalogue(ROOT)
        cls.by_id = {entry['id']: entry for entry in cls.entries}

    def test_real_person_has_full_contextual_meaning_source(self):
        entry = self.by_id['recent-real-person']
        self.assertEqual(entry['evidence_status'], 'partial')
        self.assertIn('确认自己被看见', entry['meaning'])
        self.assertIn('寻求即时回应', entry['meaning'])
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('meaning', refs['recent-src-real-person-context']['claims'])
        self.assertEqual(self.sources['recent-src-real-person-context']['access_level'], 'full')
        self.assertIsNone(entry['origin']['date'])
        self.assertIn('首创来源', entry['notes'])


class AkarinQueueEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atlas import load_catalogue
        cls.entries, cls.sources, _ = load_catalogue(ROOT)
        cls.by_id = {entry['id']: entry for entry in cls.entries}

    def test_akarin_has_full_origin_and_meaning_sources(self):
        entry = self.by_id['early-akarin']
        self.assertEqual(entry['evidence_status'], 'partial')
        self.assertIn('赤座灯里', entry['meaning'])
        self.assertIn('存在感低', entry['meaning'])
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('origin', refs['early-src-akarin-bili-guide']['claims'])
        self.assertIn('meaning', refs['early-src-akarin-360-2015']['claims'])
        self.assertEqual(self.sources['early-src-akarin-bili-guide']['access_level'], 'full')

    def test_akarin_has_a_dated_early_bilibili_example_without_claiming_first_use(self):
        entry = self.by_id['early-akarin']
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('platform_usage', refs['early-src-akarin-bili-2011']['claims'])
        self.assertEqual(self.sources['early-src-akarin-bili-2011']['access_level'], 'metadata')
        self.assertEqual(self.sources['early-src-akarin-bili-2011']['url'], 'https://www.bilibili.com/video/BV1Rx411c7GK/')
        self.assertIn('2011年8月13日', self.sources['early-src-akarin-bili-2011']['supports'])
        self.assertIn('2011年8月13日', entry['notes'])
        self.assertIn('不是B站首发', entry['notes'])
        self.assertIsNone(entry['origin']['date'])


class CirnoNineQueueEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atlas import load_catalogue
        cls.entries, cls.sources, _ = load_catalogue(ROOT)
        cls.by_id = {entry['id']: entry for entry in cls.entries}

    def test_cirno_nine_is_grounded_in_the_original_manual(self):
        entry = self.by_id['early-cirno-nine']
        self.assertEqual(entry['evidence_status'], 'partial')
        self.assertIn('琪露诺', entry['meaning'])
        self.assertIn('9.バカ', entry['origin']['summary'])
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('origin', refs['early-src-cirno-manual']['claims'])
        self.assertEqual(self.sources['early-src-cirno-manual']['access_level'], 'full')
        self.assertEqual(self.sources['early-src-cirno-touhou-media']['access_level'], 'full')

    def test_cirno_nine_has_a_dated_early_bilibili_usage_record(self):
        entry = self.by_id['early-cirno-nine']
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('platform_usage', refs['early-src-cirno-bili-2010']['claims'])
        self.assertEqual(self.sources['early-src-cirno-bili-2010']['access_level'], 'metadata')
        self.assertIn('2010年9月6日', self.sources['early-src-cirno-bili-2010']['supports'])
        self.assertIsNone(entry['origin']['date'])
        self.assertIn('不证明中文首创者', entry['notes'])


class CoachBasketballQueueEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atlas import load_catalogue
        cls.entries, cls.sources, _ = load_catalogue(ROOT)
        cls.by_id = {entry['id']: entry for entry in cls.entries}

    def test_coach_phrase_has_full_original_scene_and_template_sources(self):
        entry = self.by_id['early-coach-learn']
        self.assertEqual(entry['evidence_status'], 'partial')
        self.assertIn('バスケがしたいです', entry['origin']['summary'])
        self.assertIn('教练，我想XXX', entry['meaning'])
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('origin', refs['early-src-coach-hujiang-2013']['claims'])
        self.assertIn('meaning', refs['early-src-coach-sina-2022']['claims'])
        self.assertEqual(self.sources['early-src-coach-hujiang-2013']['access_level'], 'full')
        self.assertEqual(self.sources['early-src-coach-sina-2022']['access_level'], 'full')

    def test_coach_phrase_has_a_dated_early_bilibili_usage_record(self):
        entry = self.by_id['early-coach-learn']
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('platform_usage', refs['early-src-coach-bili-2014']['claims'])
        self.assertEqual(self.sources['early-src-coach-bili-2014']['access_level'], 'full')
        self.assertIn('2014年1月2日', self.sources['early-src-coach-bili-2014']['supports'])
        self.assertIsNone(entry['origin']['date'])
        self.assertIn('尚未定位中文首创者', entry['notes'])


class FffGroupQueueEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atlas import load_catalogue
        cls.entries, cls.sources, _ = load_catalogue(ROOT)
        cls.by_id = {entry['id']: entry for entry in cls.entries}

    def test_fff_group_is_distinguished_from_anime_adaptation(self):
        entry = self.by_id['early-fff']
        self.assertEqual(entry['evidence_status'], 'partial')
        self.assertIn('异端审问会·FFF团', entry['meaning'])
        self.assertIn('动画改编中的火刑', entry['meaning'])
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('meaning', refs['early-src-fff-kadokawa-spinout']['claims'])
        self.assertEqual(self.sources['early-src-fff-kadokawa-spinout']['access_level'], 'full')
        self.assertEqual(self.sources['early-src-fff-novel4']['access_level'], 'full')

    def test_fff_group_has_a_dated_early_bilibili_record(self):
        entry = self.by_id['early-fff']
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('platform_usage', refs['early-src-fff-bili-2010']['claims'])
        self.assertEqual(self.sources['early-src-fff-bili-2010']['access_level'], 'metadata')
        self.assertIn('2010年1月24日', self.sources['early-src-fff-bili-2010']['supports'])
        self.assertIsNone(entry['origin']['date'])
        self.assertIn('仍未通过原作正文', entry['notes'])


class GermanOrthopedicsQueueEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atlas import load_catalogue
        cls.entries, cls.sources, _ = load_catalogue(ROOT)
        cls.by_id = {entry['id']: entry for entry in cls.entries}

    def test_german_orthopedics_distinguishes_blood_relation_and_pseudo_siblings(self):
        entry = self.by_id['early-german-orthopedics']
        self.assertEqual(entry['evidence_status'], 'partial')
        self.assertIn('有血缘关系', entry['meaning'])
        self.assertIn('伪骨科', entry['meaning'])
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('origin', refs['early-src-german-wiktionary']['claims'])
        self.assertIn('meaning', refs['early-src-german-bili-2018']['claims'])
        self.assertEqual(self.sources['early-src-german-bili-2018']['access_level'], 'full')
        self.assertIn('不能确认事件真实性', entry['origin']['summary'])

    def test_german_orthopedics_has_a_dated_bilibili_usage_record(self):
        entry = self.by_id['early-german-orthopedics']
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('platform_usage', refs['early-src-german-bili-2015']['claims'])
        self.assertEqual(self.sources['early-src-german-bili-2015']['access_level'], 'metadata')
        self.assertIn('2015年10月10日', self.sources['early-src-german-bili-2015']['supports'])
        self.assertIsNone(entry['origin']['date'])
        self.assertIn('原始微博帖', entry['notes'])


class HiyoriDubQueueEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atlas import load_catalogue
        cls.entries, cls.sources, _ = load_catalogue(ROOT)
        cls.by_id = {entry['id']: entry for entry in cls.entries}

    def test_hiyori_dub_distinguishes_fan_localization_from_original_dialogue(self):
        entry = self.by_id['early-hiyori-dub']
        self.assertEqual(entry['evidence_status'], 'partial')
        self.assertIn('本土化版本', entry['meaning'])
        self.assertIn('并非按日文原文逐句翻译', entry['origin']['summary'])
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('origin', refs['early-src-hiyori-interview']['claims'])
        self.assertEqual(self.sources['early-src-hiyori-nanfang-2010']['access_level'], 'full')
        self.assertEqual(self.sources['early-src-hiyori-beijing-2010']['access_level'], 'full')

    def test_hiyori_dub_has_a_dated_bilibili_collection_record(self):
        entry = self.by_id['early-hiyori-dub']
        refs = {ref['source_id']: ref for ref in entry['evidence']}
        self.assertIn('platform_usage', refs['early-src-hiyori-bili']['claims'])
        self.assertEqual(self.sources['early-src-hiyori-bili']['access_level'], 'metadata')
        self.assertIn('2014-04-18', self.sources['early-src-hiyori-bili']['supports'])
        self.assertIn('不能据此确认B站最早上传', entry['notes'])
