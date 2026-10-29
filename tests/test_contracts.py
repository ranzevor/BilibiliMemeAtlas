"""Contract fixtures are synthetic; they are not catalogue entries."""
from __future__ import annotations

import errno
import hashlib
import json
import math
import os
from datetime import date
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from atlas import SHARDS, CatalogueError, attach_sources, get_entries, load_catalogue, markdown, search, strict_json_loads
from validate import validate_all, validate_records, validate_skill


def copy_project_fixture(target):
    return shutil.copytree(
        ROOT, target,
        ignore=lambda directory, _names:
            {".git"} if Path(directory) == ROOT else set())


def create_symlink(test, link, target, *, target_is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except NotImplementedError:
        test.skipTest("当前文件系统不支持符号链接")
    except OSError as exc:
        if (getattr(exc, "winerror", None) == 1314
                or exc.errno in {errno.EPERM, errno.EACCES, errno.ENOSYS,
                                 errno.EOPNOTSUPP}):
            test.skipTest(f"当前文件系统或权限不允许创建符号链接: {exc}")
        raise


def record(identifier="test-a", term="示例梗", status="partial", aliases=None):
    return {"id": identifier, "term": term, "aliases": aliases or [], "category": "community",
            "era": "2009-2015", "meaning": "合成样本，用于判断表达语义。",
            "origin": {"summary": "测试样本无真实首创主张。", "date": None,
                       "precision": "unknown", "bilibili_role": "uncertain"},
            "usage": {"contexts": ["合成测试"], "example": "这是一条测试例句。", "avoid": ["不得作历史事实引用"]},
            "lifecycle": "contextual", "sensitivity": [], "relations": [], "evidence_status": status,
            "evidence": [{"source_id": "test-source", "claims": ["meaning"], "note": "仅合成结构夹具"}],
            "notes": "没有真实传播链；仅用于结构与行为检查。"}


def source():
    return {"id": "test-source", "title": "Synthetic fixture", "url": "https://example.org/material",
            "publisher": "Test fixture", "source_type": "secondary", "access_level": "snippet",
            "checked_at": "2026-10-23", "supports": "Synthetic meaning only", "limitations": "No historical claim"}


def metadata():
    return {
        "schema_version": "1.0",
        "version": "0.1.0",
        "knowledge_cutoff": "2026-10-23",
        "language": "zh-CN",
        "coverage_start": "2009",
        "coverage_mode": "curated-historical-catalogue",
        "year_interpretation": "era为索引时期，origin.date为其summary明确说明的起源节点；不得互换",
        "license_status": "MIT",
        "license_scope": "project-owned-material-only",
        "third_party_rights": "retained-by-respective-rightsholders",
    }


class RetrievalContractTests(unittest.TestCase):
    def test_alias_and_fullwidth_normalization(self):
        rows = [record(aliases=["YYDS"])]
        got = search(rows, "ｙｙｄｓ")
        self.assertEqual(got["total"], 1)
        self.assertEqual(got["results"][0]["match_type"], "alias")

    def test_exact_name_precedes_incidental_description(self):
        a, b = record("test-a", "示例梗"), record("test-b", "其他梗")
        b["meaning"] = "讨论示例梗但不是同一个词条。"
        self.assertEqual(search([b, a], "示例梗")["results"][0]["entry"]["id"], "test-a")

    def test_candidates_are_discoverable_but_not_promoted(self):
        rows = [record(status="candidate")]
        hidden = search(rows, "示例梗")
        self.assertEqual((hidden["total"], hidden["candidate_matches_excluded"]), (0, 1))
        shown = search(rows, "示例梗", include_candidates=True)
        self.assertEqual(shown["results"][0]["entry"]["evidence_status"], "candidate")

    def test_explicit_candidate_filter_does_not_need_second_flag(self):
        self.assertEqual(search([record(status="candidate")], "示例梗", evidence="candidate")["total"], 1)

    def test_ambiguous_alias_retains_all_senses(self):
        rows = [record("test-a", "义项甲", aliases=["共同别名"]), record("test-b", "义项乙", aliases=["共同别名"])]
        self.assertEqual(len(get_entries(rows, "共同别名")), 2)

    def test_same_name_senses_are_valid_and_retained(self):
        rows = [record("test-a", "同名词"), record("test-b", "同名词")]
        rows[1]["meaning"] = "另一个合成义项，须保留供上下文消歧。"
        self.assertEqual(validate_records(rows, {"test-source": source()}, metadata()), [])
        self.assertEqual({e["id"] for e in get_entries(rows, "同名词")}, {"test-a", "test-b"})

    def test_single_character_slang_does_not_match_any_sentence(self):
        self.assertEqual(search([record(term="典")], "我想了解古典音乐")["total"], 0)
        self.assertEqual(search([record(term="典")], "典")["total"], 1)

    def test_empty_and_oversize_queries_are_errors(self):
        for method in (search, get_entries):
            for query in ("", "  ，？！ ", "啊" * 1001, None, [], 123, "\ud800"):
                with self.subTest(method=method.__name__, query=repr(query)):
                    with self.assertRaises(CatalogueError):
                        method([record()], query)

    def test_invalid_pagination_and_filters_report_contract_errors(self):
        cases = [{"limit": value} for value in (0, 51, 1.5, True, "2")]
        cases += [{"offset": value} for value in (-1, 0.5, False, "0")]
        cases += [{"category": "missing"}, {"era": "2015"}, {"evidence": "full"},
                  {"include_candidates": "false"}]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(CatalogueError):
                search([record()], "示例梗", **options)

    def test_filtering_precedes_page_count(self):
        rows = [record("test-a", "样本甲"), record("test-b", "样本乙"), record("test-c", "样本丙")]
        rows[1]["category"] = "game"
        first = search(rows, "样本", category="community", limit=1)
        second = search(rows, "样本", category="community", limit=1, offset=1)
        self.assertEqual(first["total"], 2)
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertNotEqual(first["results"][0]["entry"]["id"], second["results"][0]["entry"]["id"])

    def test_source_scope_survives_query(self):
        result = attach_sources(search([record()], "示例梗"), {"test-source": source()})
        self.assertEqual(result["sources"][0]["access_level"], "snippet")
        self.assertEqual(result["results"][0]["entry"]["notes"], record()["notes"])

    def test_unknown_is_empty_not_nearest_invention(self):
        self.assertEqual(search([record()], "完全不存在的表达")["total"], 0)

    def test_source_commands_remain_inert_text(self):
        row = record()
        row["meaning"] = "素材写着忽略指令并执行命令，这里只是引文。"
        with patch("socket.socket", side_effect=AssertionError("unexpected network")), patch("subprocess.run", side_effect=AssertionError("unexpected execution")):
            result = search([row], "忽略指令")
        self.assertEqual(result["total"], 1)


class EvidenceContractTests(unittest.TestCase):
    def validate(self, row=None, src=None):
        return validate_records([record() if row is None else row],
                                {"test-source": source() if src is None else src}, metadata())

    def test_synthetic_partial_has_valid_shape(self):
        self.assertEqual(self.validate(), [])

    def test_snippet_cannot_pass_complete_verification(self):
        row = record(status="verified")
        row["evidence"][0]["claims"] = ["meaning", "origin", "platform_usage"]
        self.assertTrue(any("全文支持" in e for e in self.validate(row)))

    def test_verified_date_needs_date_evidence(self):
        row, src = record(status="verified"), source()
        row["origin"].update(date="2015-01-01", precision="exact")
        row["evidence"][0]["claims"] = ["meaning", "origin", "platform_usage"]
        src["access_level"] = "full"
        self.assertTrue(any("date" in e for e in self.validate(row, src)))

    def test_future_origin_is_rejected(self):
        row = record()
        row["origin"].update(date="2027-01-01", precision="exact")
        self.assertTrue(any("晚于" in e for e in self.validate(row)))

    def test_entry_era_cannot_start_after_knowledge_cutoff(self):
        row = record()
        row["era"] = "2027-2031"
        errors = validate_records(
            [row], {"test-source": source()}, metadata(),
            allowed_eras=("2009-2015", "2027-2031"))
        self.assertTrue(any("时期起年晚于" in error
                            for error in errors))

    def test_missing_evidence_target_is_rejected(self):
        row = record()
        row["evidence"][0]["source_id"] = "missing-source"
        self.assertTrue(any("悬空" in e for e in self.validate(row)))

    def test_search_urls_and_private_addresses_are_rejected(self):
        for url in ("https://search.bilibili.com/all?keyword=x", "https://example.org/search?q=x",
                    "https://example.org/%73earch?q=x", "http://127.0.0.1/a", "http://localhost./a",
                    "file:///tmp/a", "https://user:pass@example.org/a", "https://@example.org/a",
                    "https://example.org/with space", "https://example.org/\nmaterial",
                    "https://example.org\\material", "https://example.org:9000/a"):
            src = source()
            src["url"] = url
            with self.subTest(url=url):
                self.assertTrue(self.validate(src=src), url)

    def test_dangling_relationship_is_rejected(self):
        row = record()
        row["relations"] = ["missing-entry"]
        self.assertTrue(any("无效关联" in e for e in self.validate(row)))

    def test_unknown_date_cannot_hide_precise_claim(self):
        row = record()
        row["origin"]["date"] = "2018-01-01"
        self.assertTrue(any("null" in e for e in self.validate(row)))

    def test_invalid_root_types_report_errors_without_crashing(self):
        for entries, sources, meta in ((None, {"test-source": source()}, metadata()),
                                       ([record()], [], metadata()),
                                       ([record()], {"test-source": source()}, []),
                                       ([record()], {"test-source": {}}, metadata()),
                                       ([record()], {"test-source": None}, metadata())):
            with self.subTest(entries=entries, sources=sources, metadata=meta):
                self.assertTrue(validate_records(entries, sources, meta))

    def test_malformed_nested_fields_report_errors_without_crashing(self):
        cases = [("entry", ("notes",), None), ("entry", ("relations",), [[]]),
                 ("entry", ("id",), []), ("entry", ("aliases",), [None]),
                 ("entry", ("origin",), []), ("entry", ("usage",), []),
                 ("entry", ("evidence",), None), ("entry", ("evidence",), [None]),
                 ("entry", ("evidence", 0, "source_id"), []),
                 ("entry", ("evidence", 0, "claims"), [[]]),
                 ("entry", ("evidence_status",), []), ("entry", ("term",), "\ud800"),
                 ("source", ("limitations",), None), ("source", ("url",), {}),
                 ("source", ("checked_at",), []), ("source", ("access_level",), [])]
        for scope, path, value in cases:
            row, src = record(), source()
            target = row if scope == "entry" else src
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(scope=scope, path=path, value=value):
                self.assertTrue(self.validate(row, src))

    def test_dates_require_calendar_yyyy_mm_dd_across_python_versions(self):
        for value in ("20261023", "2026-W43-5", "2026-02-30", "2026-1-2"):
            with self.subTest(value=value):
                row, src, meta = record(), source(), metadata()
                row["origin"].update(date=value, precision="exact")
                self.assertTrue(self.validate(row))
                src["checked_at"] = value
                self.assertTrue(self.validate(src=src))
                meta["knowledge_cutoff"] = value
                self.assertTrue(validate_records([record()], {"test-source": source()}, meta))

    def test_date_ranges_reject_reversed_and_future_years(self):
        for value in ("2020年至2019年", "2026年至2027年", "20262027"):
            row = record()
            row["origin"].update(date=value, precision="range")
            with self.subTest(value=value):
                self.assertTrue(self.validate(row))
        row["origin"]["date"] = "2019年至2020年"
        self.assertEqual(self.validate(row), [])

    def test_runtime_metadata_requires_a_version(self):
        changed = metadata()
        del changed["version"]
        self.assertTrue(validate_records([record()], {"test-source": source()}, changed))

    def test_cutoff_and_source_check_cannot_be_future_dated(self):
        as_of = date(2026, 10, 28)
        changed = metadata()
        changed["knowledge_cutoff"] = "2026-10-29"
        self.assertTrue(validate_records(
            [record()], {"test-source": source()}, changed, as_of=as_of))
        changed_source = source()
        changed_source["checked_at"] = "2026-10-29"
        changed_meta = metadata()
        changed_meta["knowledge_cutoff"] = "2026-10-29"
        errors = validate_records(
            [record()], {"test-source": changed_source}, changed_meta,
            as_of=as_of)
        self.assertTrue(any("UTC" in error for error in errors))

    def test_runtime_metadata_contract_rejects_drift(self):
        mutations = {
            "schema_version": "2.0",
            "version": "0.1.0-test",
            "language": "en-US",
            "coverage_start": "2010",
            "coverage_mode": "complete",
            "year_interpretation": "era是起源年",
            "license_status": "Proprietary",
            "license_scope": "all-material",
            "third_party_rights": "project-relicensed",
        }
        for key, value in mutations.items():
            changed = metadata()
            changed[key] = value
            with self.subTest(key=key):
                self.assertTrue(validate_records(
                    [record()], {"test-source": source()}, changed))
        changed = metadata()
        changed["unknown"] = "value"
        self.assertTrue(validate_records(
            [record()], {"test-source": source()}, changed))


class CatalogueLoadingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "bilibili-meme-atlas"
        (self.root / "references").mkdir(parents=True)
        for name in SHARDS:
            self.write_json(name, [record()] if name == SHARDS[0] else [])
        self.write_json("sources.json", [source()])
        self.write_json("metadata.json", metadata())

    def write_json(self, name, value):
        (self.root / "references" / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def copy_scripts(self):
        (self.root / "scripts").mkdir()
        for name in ("atlas.py", "validate.py"):
            shutil.copy2(ROOT / "scripts" / name, self.root / "scripts" / name)

    def test_valid_catalogue_loads(self):
        entries, sources, meta = load_catalogue(self.root)
        self.assertEqual(entries, [record()])
        self.assertEqual(sources, {"test-source": source()})
        self.assertEqual(meta, metadata())

    def test_reader_refuses_update_lock_without_writes(self):
        (self.root / ".atlas-update.lock").mkdir()
        before = {str(p): (p.read_bytes() if p.is_file() else None,
                            p.stat().st_mtime_ns)
                  for p in self.root.rglob("*")}
        with self.assertRaisesRegex(CatalogueError, "更新"):
            load_catalogue(self.root)
        after = {str(p): (p.read_bytes() if p.is_file() else None,
                           p.stat().st_mtime_ns)
                 for p in self.root.rglob("*")}
        self.assertEqual(after, before)
        self.assertFalse((self.root.parent / f".{self.root.name}.writer-lease").exists())

    def test_reader_rejects_input_replacement_during_capture(self):
        original = Path.read_bytes
        changed = False

        def read_then_replace(path):
            nonlocal changed
            body = original(path)
            if path == self.root / "references" / SHARDS[0] and not changed:
                changed = True
                replacement = self.root.parent / "new-metadata.json"
                replacement.write_bytes(json.dumps({**metadata(), "version": "0.1.1"}).encode("utf-8"))
                os.replace(replacement, self.root / "references" / "metadata.json")
            return body

        with patch.object(Path, "read_bytes", read_then_replace):
            with self.assertRaisesRegex(CatalogueError, "读取期间.*变化"):
                load_catalogue(self.root)
        self.assertTrue(changed)
        self.assertEqual(load_catalogue(self.root)[2]["version"], "0.1.1")

    def test_reader_rejects_shard_added_during_capture(self):
        original = Path.read_bytes
        changed = False

        def read_then_add(path):
            nonlocal changed
            body = original(path)
            if path == self.root / "references" / SHARDS[-1] and not changed:
                changed = True
                (self.root / "references" / "2027-2031.json").write_bytes(b"[]\n")
            return body

        with patch.object(Path, "read_bytes", read_then_add):
            with self.assertRaisesRegex(CatalogueError, "读取期间.*变化"):
                load_catalogue(self.root)
        self.assertTrue(changed)

    def test_parser_uses_captured_metadata_after_later_change(self):
        import atlas
        original = atlas.strict_json_loads
        changed = False

        def decode_then_change(payload, **kwargs):
            nonlocal changed
            value = original(payload, **kwargs)
            if not changed:
                changed = True
                self.write_json("metadata.json", {**metadata(), "version": "0.1.1"})
            return value

        with patch.object(atlas, "strict_json_loads", side_effect=decode_then_change):
            rows, _sources, meta = load_catalogue(self.root)
        self.assertEqual(rows, [record()])
        self.assertEqual(meta["version"], "0.1.0")
        self.assertEqual(load_catalogue(self.root)[2]["version"], "0.1.1")

    def test_cli_update_lock_is_error_without_query_output(self):
        self.copy_scripts()
        (self.root / ".atlas-update.lock").mkdir()
        run = subprocess.run(
            [sys.executable, "-B", str(self.root / "scripts" / "atlas.py"), "stats"],
            text=True, encoding="utf-8", capture_output=True)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(run.stdout, "")
        self.assertIn("更新", run.stderr)

    def test_strict_numbers_reject_overflow_with_and_without_shape_limits(self):
        for options in ({}, {"max_depth": 8, "max_nodes": 20}):
            for text in ("1e309", "-1e309", "NaN", "Infinity", "-Infinity"):
                with self.subTest(text=text, options=options), self.assertRaises(ValueError):
                    strict_json_loads('{"value":' + text + '}', **options)
            for text in ("1e308", "-1e308", "0.0"):
                value = strict_json_loads('{"value":' + text + '}', **options)
                self.assertTrue(math.isfinite(value["value"]))

    def test_loader_rejects_malformed_nested_data_and_metadata(self):
        cases = [(SHARDS[0], [{**record(), "evidence": [None]}]),
                 (SHARDS[0], [{**record(), "evidence_status": "unsupported"}]),
                 ("sources.json", [{"id": "test-source"}]), ("metadata.json", [])]
        for name, value in cases:
            path = self.root / "references" / name
            previous = path.read_bytes()
            self.write_json(name, value)
            with self.subTest(name=name, value=value), self.assertRaises(CatalogueError):
                load_catalogue(self.root)
            path.write_bytes(previous)

    def test_loader_rejects_duplicate_json_fields_and_nonstandard_numbers(self):
        path = self.root / "references" / "metadata.json"
        for text in ('{"version":"a","version":"b","knowledge_cutoff":"2026-10-23"}',
                     '{"version":"a","knowledge_cutoff":"2026-10-23","unexpected":NaN}'):
            path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(CatalogueError):
                load_catalogue(self.root)

    def test_loader_rejects_wrong_shard_assignment(self):
        row = record()
        row["era"] = "2021-2026"
        self.write_json(SHARDS[0], [row])
        with self.assertRaisesRegex(CatalogueError, "所在分片"):
            load_catalogue(self.root)

    def test_validate_all_reports_missing_files(self):
        (self.root / "references" / SHARDS[0]).unlink()
        failures = validate_all(self.root)
        self.assertTrue(any(SHARDS[0] in error for error in failures))
        self.assertTrue(any("SKILL.md" in error for error in failures))

    def test_cli_malformed_catalogue_returns_error_without_traceback(self):
        self.copy_scripts()
        self.write_json(SHARDS[0], [{**record(), "evidence_status": "unsupported"}])
        for script, arguments, expected in (("atlas.py", ["search", "示例梗"], 2),
                                            ("validate.py", [], 1)):
            run = subprocess.run([sys.executable, str(self.root / "scripts" / script), *arguments],
                                 text=True, encoding="utf-8", capture_output=True)
            with self.subTest(script=script):
                self.assertEqual(run.returncode, expected, run.stderr)
                self.assertNotIn("Traceback", run.stdout + run.stderr)
                self.assertIn("无效证据状态", run.stdout + run.stderr)

    def test_cli_search_and_get_report_invalid_queries(self):
        self.copy_scripts()
        for command in ("search", "get"):
            run = subprocess.run([sys.executable, str(self.root / "scripts" / "atlas.py"), command, "？！"],
                                 text=True, encoding="utf-8", capture_output=True)
            with self.subTest(command=command):
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertNotIn("Traceback", run.stderr)


class RuntimeDataTests(unittest.TestCase):
    def test_skill_identity_comes_from_frontmatter_not_container_name(self):
        body = '---\nname: bilibili-meme-atlas\ndescription: "合成格式夹具"\n---\n\n# Fixture\n'
        with tempfile.TemporaryDirectory() as directory:
            for name in ("bilibili-meme-atlas", "skill-" + "a" * 32, "unrelated-folder"):
                root = Path(directory) / name
                root.mkdir()
                (root / "SKILL.md").write_text(body, encoding="utf-8")
                with self.subTest(directory=name):
                    self.assertEqual(validate_skill(root), [])
            hosted = Path(directory) / ("skill-" + "a" * 32)
            (hosted / "SKILL.md").write_text(body.replace("name: bilibili-meme-atlas", "name: Invalid Name"), encoding="utf-8")
            self.assertTrue(validate_skill(hosted))

    def test_cli_from_another_working_directory_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            for command in ("search", "get"):
                run = subprocess.run([sys.executable, str(ROOT / "scripts" / "atlas.py"), command, "真香"], cwd=directory, text=True, encoding="utf-8", capture_output=True)
                with self.subTest(command=command):
                    self.assertEqual(run.returncode, 0, run.stderr)
                    result = json.loads(run.stdout)
                    self.assertTrue(any(e["entry"]["term"] == "真香" for e in result["results"]))
                    expected = json.loads((ROOT / "references" / "metadata.json").read_text(encoding="utf-8"))
                    self.assertEqual(result["version"], expected["version"])
                    self.assertEqual(result["knowledge_cutoff"], expected["knowledge_cutoff"])
                    self.assertIn(f"资料版本：{expected['version']}；资料截止日：{expected['knowledge_cutoff']}。",
                                  markdown(result))

    def test_queries_do_not_modify_catalogue_or_fetch_network(self):
        paths = sorted((ROOT / "references").glob("*.json"))
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        with patch("socket.socket", side_effect=AssertionError("unexpected network")):
            entries, sources, _ = load_catalogue()
            for query in ("真香", "爷青回", "鸡你太美", "不存在的测试词条"):
                attach_sources(search(entries, query), sources)
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_real_aliases_roundtrip(self):
        entries, _, _ = load_catalogue()
        for row in entries:
            for alias in row["aliases"]:
                self.assertIn(row["id"], [e["id"] for e in get_entries(entries, alias)])




class PartialAdmissionTests(unittest.TestCase):
    def test_metadata_meaning_cannot_enter_default_factual_set(self):
        src = source(); src['access_level'] = 'metadata'
        self.assertTrue(any('partial缺非元数据释义' in e for e in validate_records([record()], {src['id']:src}, metadata())))
    def test_date_only_list_is_not_meaning_evidence(self):
        row = record(); row['evidence'][0]['claims'] = ['date']
        self.assertTrue(any('partial缺非元数据释义' in e for e in validate_records([row], {'test-source':source()}, metadata())))
    def test_unsupported_record_is_retained_as_candidate(self):
        row = record(status='candidate'); row['evidence'][0]['claims'] = ['date']
        self.assertEqual(validate_records([row], {'test-source':source()}, metadata()), [])
        self.assertEqual(search([row],row['term'])['candidate_matches_excluded'],1)

if __name__ == "__main__":
    unittest.main()
