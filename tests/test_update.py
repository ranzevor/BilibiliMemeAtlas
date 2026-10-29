"""Synthetic tests for read-only discovery and source-bound incremental updates."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import date
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from atlas import SHARDS, CatalogueError, load_catalogue, search, shard_files
from build import projections
from update import (UPDATE_FORMAT, UpdateError, apply, digest_record, discover,
                    fingerprint, plan, public_json, read_proposal, recover,
                    recovery_info)
from validate import expected_rights_registry
import writer_lock
from test_contracts import copy_project_fixture


BASE_SOURCE = {"id": "existing-src", "title": "Synthetic reference A", "url": "https://example.org/original",
               "publisher": "Fixture", "source_type": "community", "access_level": "snippet",
               "checked_at": "2026-10-23", "supports": "Synthetic statement only", "limitations": "Not a historical source"}


def entry(identifier, term, *, era="2021-2026", source="existing-src", status="partial"):
    return {"id": identifier, "term": term, "aliases": [], "category": "community", "era": era,
            "meaning": "合成词义，用于输入输出边界测试。", "origin": {"summary": "合成测试无历史出处。", "date": None,
            "precision": "unknown", "bilibili_role": "uncertain"},
            "usage": {"contexts": ["虚构测试"], "example": "这是合成例句。", "avoid": ["不得写成真实梗史"]},
            "lifecycle": "contextual", "sensitivity": [], "relations": [], "evidence_status": status,
            "evidence": [{"source_id": source, "claims": ["meaning"], "note": "仅供测试"}],
            "notes": "合成夹具，不可用于真实知识库。"}


def source(identifier="new-src", *, checked="2026-10-23", level="full"):
    return {"id": identifier, "title": "Synthetic reference B", "url": "https://example.org/synthetic-new",
            "publisher": "Fixture", "source_type": "primary", "access_level": level,
            "checked_at": checked, "supports": "Only synthetic test wording", "limitations": "Synthetic fixture"}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "bilibili-meme-atlas"
        copy_project_fixture(self.root)
        reference = self.root / "references"
        self.save("2009-2015.json", [])
        self.save("2016-2020.json", [])
        self.save("2021-2026.json", [entry("original", "起点样本")])
        self.save("sources.json", [BASE_SOURCE])
        self.save("metadata.json", {
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
        })
        self.save("rights.json", expected_rights_registry(
            self.root, source_ids=["existing-src"]))
        for name, text in projections(self.root).items():
            (self.root / name).write_bytes(text.encode("utf-8"))

    def save(self, name, obj):
        (self.root / "references" / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def batch(self, *, sources=None, entries=None, checked="2026-10-23"):
        return {"format": UPDATE_FORMAT, "base_manifest_sha256": fingerprint(self.root),
                "checked_at": checked, "sources": sources or [], "entries": entries or []}

    def sample(self, term="新样本", identifier="new-item"):
        return self.batch(sources=[{"action": "add", "record": source()}],
                          entries=[{"action": "add", "record": entry(identifier, term, source="new-src")}])

    def snapshot(self):
        return {p.name: p.read_bytes() for p in (self.root / "references").iterdir() if p.is_file()}

    def test_preview_does_not_modify_any_files(self):
        before = self.snapshot()
        summary, change = plan(self.root, self.sample())
        self.assertEqual(summary["added_entries"], ["new-item"])
        self.assertEqual(summary["added_sources"], ["new-src"])
        self.assertEqual(summary["version"], "0.1.1")
        self.assertIn("references/catalog.md", change)
        self.assertEqual(before, self.snapshot())

    def test_apply_add_is_bounded_and_searchable(self):
        batch = self.sample()
        result = apply(self.root, batch)
        rows, sources, metadata = load_catalogue(self.root)
        self.assertEqual(len(rows), 2)
        self.assertIn("new-src", sources)
        self.assertEqual(search(rows, "新样本")["results"][0]["entry"]["id"], "new-item")
        self.assertEqual(metadata["version"], "0.1.1")
        self.assertEqual(result["added_entries"], ["new-item"])
        self.assertEqual(projections(self.root)["references/corpus-summary.json"],
                         (self.root / "references/corpus-summary.json").read_text())
        self.assertFalse((self.root / ".atlas-update.lock").exists())
        with self.assertRaisesRegex(UpdateError, "基线已变化"):
            apply(self.root, batch)

    def assert_query_rejects_interleaved_update(self, filename):
        batch = self.sample()
        original = Path.read_bytes
        updated = False

        def update_before_read(path):
            nonlocal updated
            if path == self.root / "references" / filename and not updated:
                updated = True
                apply(self.root, batch)
            return original(path)

        with patch.object(Path, "read_bytes", update_before_read):
            with self.assertRaisesRegex(CatalogueError, "读取期间.*变化"):
                load_catalogue(self.root)
        self.assertTrue(updated)
        rows, _sources, meta = load_catalogue(self.root)
        self.assertEqual(meta["version"], "0.1.1")
        self.assertEqual(search(rows, "新样本")["total"], 1)

    def test_query_rejects_update_between_shards_and_sources(self):
        self.assert_query_rejects_interleaved_update("sources.json")

    def test_query_rejects_update_between_sources_and_metadata(self):
        self.assert_query_rejects_interleaved_update("metadata.json")

    def test_query_refuses_a_paused_update(self):
        original = __import__("update").atomic_write
        observed = False

        def write_and_query(path, text):
            nonlocal observed
            original(path, text)
            if not observed:
                observed = True
                before = self.snapshot()
                with self.assertRaisesRegex(CatalogueError, "更新"):
                    load_catalogue(self.root)
                self.assertEqual(self.snapshot(), before)

        with patch("update.atomic_write", side_effect=write_and_query):
            result = apply(self.root, self.sample())
        self.assertTrue(observed)
        self.assertEqual(result["version"], "0.1.1")

    def test_projections_read_each_corpus_input_once(self):
        original = Path.read_bytes
        counts = {}
        inputs = set(SHARDS) | {"sources.json", "metadata.json"}

        def counted(path):
            if path.parent == self.root / "references" and path.name in inputs:
                counts[path.name] = counts.get(path.name, 0) + 1
            return original(path)

        with patch.object(Path, "read_bytes", counted):
            generated = projections(self.root)
        self.assertEqual(counts, {name: 1 for name in inputs})
        self.assertEqual(generated["references/catalog.md"].encode("utf-8"),
                         (self.root / "references/catalog.md").read_bytes())

    def test_replace_record_requires_original_sha_and_preserves_id(self):
        current = load_catalogue(self.root)[0][0]
        proposed = deepcopy(current)
        proposed["notes"] = "更新后的合成资料缺口说明。"
        batch = self.batch(entries=[{"action": "replace", "record": proposed,
                                     "before_sha256": digest_record(current)}])
        apply(self.root, batch)
        self.assertEqual(load_catalogue(self.root)[0][0]["notes"], proposed["notes"])
        with self.assertRaisesRegex(UpdateError, "源记录已变化"):
            plan(self.root, {**self.batch(), "entries": batch["entries"]})

    def test_add_duplicate_id_or_url_fails_without_changes(self):
        before = self.snapshot()
        bad_id = self.sample(identifier="original")
        with self.assertRaisesRegex(UpdateError, "相同 ID"):
            plan(self.root, bad_id)
        bad_url = self.batch(sources=[{"action": "add", "record": {**source(), "url": BASE_SOURCE["url"]}}])
        with self.assertRaisesRegex(UpdateError, "URL 已登记"):
            plan(self.root, bad_url)
        self.assertEqual(before, self.snapshot())

    def test_alias_collision_requires_exact_distinct_sense_declaration(self):
        original = self.sample(term="起点样本")
        with self.assertRaisesRegex(UpdateError, "明确区分义项"):
            plan(self.root, original)
        original["entries"][0]["distinct_sense_of"] = ["original"]
        got, _ = plan(self.root, original)
        self.assertEqual(got["added_entries"], ["new-item"])

    def test_snippet_metadata_cannot_silently_promote_verified(self):
        draft = self.sample()
        draft["sources"][0]["record"]["access_level"] = "metadata"
        with self.assertRaisesRegex(UpdateError, "释义依据"):
            plan(self.root, draft)
        draft["entries"][0]["record"]["evidence_status"] = "verified"
        with self.assertRaisesRegex(UpdateError, "完整证据"):
            plan(self.root, draft)

    def test_dangling_source_is_rejected_before_writes(self):
        base = self.snapshot()
        draft = self.batch(entries=[{"action": "add", "record": entry("new-item", "新样本", source="missing") }])
        with self.assertRaisesRegex(Exception, "悬空来源"):
            apply(self.root, draft)
        self.assertEqual(base, self.snapshot())

    def test_future_partition_is_created_when_spread_year_advances(self):
        future_as_of = date(2027, 1, 2)
        future_source = source(checked="2027-01-02")
        new_entry = entry("new-era", "新时期样本", era="2027-2031", source="new-src")
        batch = self.batch(sources=[{"action": "add", "record": future_source}],
                           entries=[{"action": "add", "record": new_entry}], checked="2027-01-02")
        apply(self.root, batch, as_of=future_as_of)
        self.assertEqual(shard_files(self.root)[-1], "2027-2031.json")
        self.assertEqual(
            load_catalogue(self.root, as_of=future_as_of)[0][-1]["era"],
            "2027-2031")
        self.assertIn(
            "2027–2031",
            projections(self.root, as_of=future_as_of)["references/coverage.md"])
        with self.assertRaises(Exception):
            load_catalogue(self.root, as_of=date(2026, 10, 28))

    def test_write_failure_restores_old_bytes(self):
        before = self.snapshot()
        from update import atomic_write as original
        counter = 0
        def fail_second(path, content):
            nonlocal counter
            counter += 1
            if counter == 2:
                raise OSError("simulated disk error")
            return original(path, content)
        with patch("update.atomic_write", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "simulated disk"):
                apply(self.root, self.sample())
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.root / ".atlas-update.lock").exists())

    def test_stale_empty_lock_is_retired_before_update(self):
        (self.root / ".atlas-update.lock").mkdir()
        result = apply(self.root, self.sample())
        self.assertEqual(result["version"], "0.1.1")
        self.assertFalse((self.root / ".atlas-update.lock").exists())

    def test_shared_writer_lease_prevents_two_writers(self):
        from writer_lock import writer_lease
        with writer_lease(self.root), self.assertRaisesRegex(UpdateError, "写入者"):
            apply(self.root, self.sample())

    def test_proposal_rejects_unknown_fields_and_future_dates(self):
        path = self.root / "proposal.json"
        future = "2026-10-29"
        for change in ({"extra": True}, {"checked_at": future}, {"format": "other"}):
            data = {**self.sample(), **change}
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.subTest(change=change), self.assertRaises(UpdateError):
                read_proposal(path, as_of=date(2026, 10, 28))

    def test_direct_apply_rejects_future_checked_date(self):
        batch = self.sample()
        batch["checked_at"] = "2026-10-29"
        batch["sources"][0]["record"]["checked_at"] = "2026-10-29"
        with self.assertRaisesRegex(UpdateError, "UTC"):
            apply(self.root, batch, as_of=date(2026, 10, 28))

    def test_source_url_replacement_forbidden(self):
        prev = BASE_SOURCE
        new = {**prev, "url": "https://example.org/rewritten"}
        batch = self.batch(sources=[{"action": "replace", "record": new, "before_sha256": digest_record(prev)}])
        with self.assertRaisesRegex(UpdateError, "新来源 ID"):
            plan(self.root, batch)


class DiscoveryTests(unittest.TestCase):
    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Type": "application/json"}

    class Opener:
        def __init__(self, body):
            self.body = body
            self.calls = []

        def open(self, request, timeout):
            self.calls.append((request, timeout))
            return DiscoveryTests.Response(self.body)

    def test_popular_is_only_discovery_and_never_entry(self):
        called = []
        def fake_fetch(url):
            called.append(url)
            return {"code": 0, "data": {"list": [
                {"bvid": "BV1xx411c7mD", "title": "<b>测试标题</b>", "desc": "合成描述", "owner": {"name": "虚构用户"}, "pubdate": 1700000000, "stat": {"view": 300, "like": 30}},
                {"bvid": "BV1xx411c7mD", "title": "重复"},
                {"bvid": "not-a-bvid", "title": "不合法"}], "no_more": True}}
        result = discover(3, fetch=fake_fetch, pause=lambda _: None)
        self.assertEqual(len(called), 1)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["title"], "测试标题")
        self.assertEqual(result["items"][0]["video_views"], 300)
        self.assertEqual(result["items"][0]["list_rank"], 1)
        self.assertNotIn("evidence_status", result["items"][0])
        self.assertNotIn("entries", result)

    def test_api_rejection_does_not_retry(self):
        calls = []
        def reject(url):
            calls.append(url)
            return {"code": -352}
        with self.assertRaisesRegex(UpdateError, "停止"):
            discover(3, fetch=reject, pause=lambda _: None)
        self.assertEqual(len(calls), 1)

    def test_request_host_cannot_be_overridden(self):
        with self.assertRaisesRegex(UpdateError, "允许"):
            public_json("http://127.0.0.1/admin")
        with self.assertRaisesRegex(UpdateError, "允许"):
            public_json("https://api.bilibili.com.evil.org/x/web-interface/popular?pn=1&ps=20")

    def test_limit_prevents_unbounded_discovery(self):
        for n in (0, 4, 999, True):
            with self.subTest(pages=n), self.assertRaises(UpdateError):
                discover(n, fetch=lambda _: {})

    def test_network_json_is_strict_and_failure_receipt_is_safe(self):
        from update import main
        bodies = (
            b'{"code":-412,"code":0,"data":{"list":[]}}',
            b'{"code":0,"data":{"list":[{"stat":{"view":NaN}}]}}',
        )
        with tempfile.TemporaryDirectory() as temporary:
            for index, body in enumerate(bodies):
                opener = self.Opener(body)
                output = Path(temporary) / f"failure-{index}.json"
                with self.subTest(body=body), patch(
                        "update.build_opener", return_value=opener):
                    self.assertEqual(
                        main(["discover", "--output", str(output)]), 2)
                self.assertEqual(len(opener.calls), 1)
                receipt = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(receipt["status"], "unavailable")
                self.assertNotIn("items", receipt)
                self.assertNotIn("entries", receipt)


class UpdateRecoveryTests(unittest.TestCase):
    setUp = UpdateTests.setUp
    save = UpdateTests.save
    batch = UpdateTests.batch
    sample = UpdateTests.sample
    snapshot = UpdateTests.snapshot

    def write_journal(self, files):
        lock = self.root / ".atlas-update.lock"
        lock.mkdir()
        journal = {"format": "atlas-update-journal/v1", "files": files}
        (lock / "journal.json").write_text(
            json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        return lock

    def test_committed_update_with_cleanup_failure_is_finalizable(self):
        with patch("update._retire_update_lock",
                   side_effect=OSError("synthetic rename failure")):
            with self.assertRaisesRegex(OSError, "rename"):
                apply(self.root, self.sample())
        self.assertEqual(
            recovery_info(self.root)["state"],
            "committed_cleanup_pending")
        with self.assertRaisesRegex(UpdateError, "finalize"):
            recover(self.root, writers_stopped=True)
        result = recover(
            self.root, writers_stopped=True, finalize_committed=True)
        self.assertEqual(result["status"], "committed_cleanup_completed")
        self.assertFalse((self.root / ".atlas-update.lock").exists())
        self.assertEqual(load_catalogue(self.root)[2]["version"], "0.1.1")

    def test_recovery_info_is_read_only_and_rejects_replaced_journal(self):
        with patch("update._retire_update_lock", side_effect=OSError("synthetic cleanup failure")):
            with self.assertRaises(OSError):
                apply(self.root, self.sample())
        before = {str(path): (path.read_bytes() if path.is_file() else None,
                              path.stat().st_mtime_ns)
                  for path in self.root.rglob("*")}
        with patch("update.writer_lease", side_effect=AssertionError("read-only query took a writer lease")):
            self.assertEqual(recovery_info(self.root)["state"], "committed_cleanup_pending")
        self.assertEqual(before, {
            str(path): (path.read_bytes() if path.is_file() else None,
                        path.stat().st_mtime_ns)
            for path in self.root.rglob("*")})
        original = __import__("update")._validate_update_project
        journal = self.root / ".atlas-update.lock" / "journal.json"

        def validate_then_replace(*args, **kwargs):
            original(*args, **kwargs)
            replacement = self.root.parent / "replacement-journal.json"
            replacement.write_bytes(journal.read_bytes())
            os.replace(replacement, journal)

        with patch("update._validate_update_project", side_effect=validate_then_replace):
            with self.assertRaisesRegex(UpdateError, "变化|改变"):
                recovery_info(self.root)
        self.assertTrue(journal.is_file())

    def test_rolled_back_update_with_cleanup_failure_is_recoverable(self):
        original = __import__("update").atomic_write
        calls = 0

        def fail_second(path, content):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic write failure")
            return original(path, content)

        with patch("update.atomic_write", side_effect=fail_second), patch(
                "update._retire_update_lock",
                side_effect=OSError("synthetic cleanup failure")):
            with self.assertRaisesRegex(OSError, "cleanup"):
                apply(self.root, self.sample())
        self.assertEqual(
            recovery_info(self.root)["state"],
            "rollback_completed_cleanup_pending")
        result = recover(self.root, writers_stopped=True)
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(load_catalogue(self.root)[2]["version"], "0.1.0")

    def test_retired_update_cleanup_stays_outside_project(self):
        def retain_cleanup(root, lock, expected_token):
            retired = root.parent / f".{root.name}.synthetic-cleanup"
            os.replace(lock, retired)
            return retired

        with patch("update._retire_update_lock", side_effect=retain_cleanup):
            result = apply(self.root, self.sample())
        pending = Path(result["cleanup_pending"])
        self.assertTrue(pending.is_dir())
        self.assertFalse(pending.is_relative_to(self.root))
        self.assertFalse((self.root / ".atlas-update.lock").exists())
        load_catalogue(self.root)

    def test_journal_fsync_failure_changes_no_reference_bytes(self):
        before = self.snapshot()
        with patch("update.os.fsync",
                   side_effect=OSError("synthetic journal fsync failure")):
            with self.assertRaisesRegex(OSError, "fsync"):
                apply(self.root, self.sample())
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / ".atlas-update.lock").exists())

    def test_journal_paths_are_limited_to_planner_outputs(self):
        cases = (
            "references/schema.json",
            "references/usage.md",
            "references/2028-2032.json",
        )
        for index, name in enumerate(cases):
            with self.subTest(name=name):
                if index:
                    UpdateTests.setUp(self)
                _preview, changes = plan(self.root, self.sample())
                files = []
                for planned_name, planned_body in changes.items():
                    current_path = self.root / planned_name
                    current_body = (current_path.read_bytes()
                                    if current_path.exists() else None)
                    files.append({
                        "path": planned_name,
                        "before": (None if current_body is None else
                                   base64.b64encode(
                                       current_body).decode("ascii")),
                        "after_sha256": hashlib.sha256(
                            planned_body.encode("utf-8")).hexdigest(),
                    })
                path = self.root / name
                before = path.read_bytes() if path.exists() else None
                files.append({
                    "path": name,
                    "before": (None if before is None else
                               base64.b64encode(before).decode("ascii")),
                    "after_sha256": hashlib.sha256(
                        b"synthetic after").hexdigest(),
                })
                lock = self.write_journal(files)
                with self.assertRaisesRegex(UpdateError, "路径|必需|分片"):
                    recover(self.root, writers_stopped=True)
                self.assertEqual(
                    path.read_bytes() if path.exists() else None, before)
                self.assertTrue((lock / "journal.json").is_file())

    def test_failed_full_validation_restores_pre_recovery_bytes(self):
        _preview, changes = plan(self.root, self.sample())
        current = {name: (self.root / name).read_bytes()
                   for name in changes}
        invalid_metadata = json.loads(
            current["references/metadata.json"].decode("utf-8"))
        invalid_metadata["knowledge_cutoff"] = "2099-12-31"
        invalid_bytes = (json.dumps(
            invalid_metadata, ensure_ascii=False, indent=2) + "\n").encode()
        files = []
        for name, body in current.items():
            before = (invalid_bytes if name == "references/metadata.json"
                      else body)
            files.append({
                "path": name,
                "before": base64.b64encode(before).decode("ascii"),
                "after_sha256": hashlib.sha256(body).hexdigest(),
            })
        lock = self.write_journal(files)
        with self.assertRaisesRegex(UpdateError, "验证"):
            recover(self.root, writers_stopped=True)
        for name, body in current.items():
            self.assertEqual((self.root / name).read_bytes(), body)
        self.assertTrue((lock / "journal.json").is_file())

    def test_recovery_rejects_entry_later_than_knowledge_cutoff(self):
        future_path = self.root / "references" / "2027-2031.json"
        future_path.write_text("[]\n", encoding="utf-8")
        for name, text in projections(self.root).items():
            (self.root / name).write_bytes(text.encode("utf-8"))
        current_future = future_path.read_bytes()
        invalid_future = (json.dumps([
            entry("future-item", "未来样本", era="2027-2031")
        ], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        names = {
            "references/metadata.json",
            "references/catalog.md",
            "references/coverage.md",
            "references/history-index.md",
            "references/corpus-summary.json",
        }
        files = []
        for name in sorted(names):
            body = (self.root / name).read_bytes()
            files.append({
                "path": name,
                "before": base64.b64encode(body).decode("ascii"),
                "after_sha256": hashlib.sha256(body).hexdigest(),
            })
        files.append({
            "path": "references/2027-2031.json",
            "before": base64.b64encode(invalid_future).decode("ascii"),
            "after_sha256": hashlib.sha256(current_future).hexdigest(),
        })
        lock = self.write_journal(files)
        with self.assertRaisesRegex(UpdateError, "时期起年晚于"):
            recover(self.root, writers_stopped=True)
        self.assertEqual(future_path.read_bytes(), current_future)
        self.assertTrue((lock / "journal.json").is_file())

    @unittest.skipUnless(os.name == "posix", "POSIX 写入租约故障注入")
    def test_update_and_recovery_ignore_post_commit_lease_release_faults(self):
        import fcntl
        original_flock = fcntl.flock

        def fail_unlock(descriptor, operation):
            if operation == fcntl.LOCK_UN:
                raise OSError("synthetic unlock failure")
            return original_flock(descriptor, operation)

        with patch("fcntl.flock", side_effect=fail_unlock):
            result = apply(self.root, self.sample())
        self.assertEqual(result["version"], "0.1.1")
        self.assertFalse((self.root / ".atlas-update.lock").exists())

        root = self.root
        second_source = source("second-src")
        second_source["url"] = "https://example.org/synthetic-second"
        batch = self.batch(
            sources=[{"action": "add", "record": second_source}],
            entries=[{"action": "add", "record": entry(
                "second-item", "第二样本", source="second-src")}])
        with patch("update._retire_update_lock",
                   side_effect=OSError("synthetic cleanup failure")):
            with self.assertRaisesRegex(OSError, "cleanup"):
                apply(root, batch)
        retained_handles = []

        class CloseFault:
            closed = False

            def __init__(self, descriptor):
                self.descriptor = descriptor

            def fileno(self):
                return self.descriptor

            def close(self):
                raise OSError("synthetic close failure")

        def close_fault(descriptor):
            wrapped = CloseFault(descriptor)
            retained_handles.append(wrapped)
            return wrapped

        with patch(
                "writer_lock._open_lease_handle",
                side_effect=close_fault):
            finalized = recover(
                root, writers_stopped=True, finalize_committed=True)
        self.assertEqual(
            finalized["status"], "committed_cleanup_completed")
        self.assertFalse((root / ".atlas-update.lock").exists())
        self.assertEqual(apply(root, self.batch())["status"], "no_update")

    def test_update_retirement_preserves_replaced_or_extended_lock(self):
        for mode in ("replace", "extend"):
            with self.subTest(mode=mode):
                if mode == "extend":
                    UpdateTests.setUp(self)
                original_move = writer_lock._move_lock
                preserved = {}

                def mutate(source, destination):
                    source, destination = Path(source), Path(destination)
                    if mode == "replace":
                        displaced = source.with_name(source.name + ".displaced")
                        os.rename(source, displaced)
                        source.mkdir()
                        preserved["displaced"] = displaced
                    result = original_move(source, destination)
                    (destination / "unknown-owned-content").write_text(
                        "unknown", encoding="utf-8")
                    preserved["retired"] = destination
                    return result

                with patch("writer_lock._move_lock", side_effect=mutate):
                    with self.assertRaisesRegex(UpdateError, "保留|替换|改变"):
                        apply(self.root, self.sample())
                retired = preserved["retired"]
                self.assertEqual(
                    (retired / "unknown-owned-content").read_text(),
                    "unknown")
                if mode == "replace":
                    self.assertTrue(
                        (preserved["displaced"] / "journal.json").is_file())
                else:
                    self.assertTrue((retired / "journal.json").is_file())

    def test_update_recovery_retirement_preserves_replaced_lock(self):
        with patch("update._retire_update_lock",
                   side_effect=OSError("synthetic cleanup failure")):
            with self.assertRaisesRegex(OSError, "cleanup"):
                apply(self.root, self.sample())
        original_move = writer_lock._move_lock
        preserved = {}

        def swap(source, destination):
            source, destination = Path(source), Path(destination)
            displaced = source.with_name(source.name + ".displaced")
            os.rename(source, displaced)
            source.mkdir()
            (source / "unknown-owned-content").write_text(
                "unknown", encoding="utf-8")
            preserved.update(displaced=displaced, retired=destination)
            return original_move(source, destination)

        with patch("writer_lock._move_lock", side_effect=swap):
            with self.assertRaisesRegex(UpdateError, "保留|替换|改变"):
                recover(
                    self.root, writers_stopped=True,
                    finalize_committed=True)
        self.assertTrue(
            (preserved["displaced"] / "journal.json").is_file())
        self.assertEqual(
            (preserved["retired"] / "unknown-owned-content").read_text(),
            "unknown")



class NoUpdateAndIdentityTests(unittest.TestCase):
    setUp = UpdateTests.setUp
    save = UpdateTests.save
    batch = UpdateTests.batch
    sample = UpdateTests.sample
    snapshot = UpdateTests.snapshot
    def test_empty_proposal_is_successful_no_update(self):
        before = self.snapshot()
        path = self.root / 'empty.json'
        path.write_text(json.dumps(self.batch()))
        batch = read_proposal(path)
        result, changes = plan(self.root, batch)
        self.assertEqual(result['status'], 'no_update')
        self.assertEqual(changes, {})
        self.assertEqual(apply(self.root, batch)['status'], 'no_update')
        self.assertEqual(before, self.snapshot())

    def test_identical_replace_does_not_bump_version(self):
        record = load_catalogue(self.root)[0][0]
        result, changes = plan(self.root, self.batch(entries=[{
            'action': 'replace', 'record': record, 'before_sha256': digest_record(record)}]))
        self.assertEqual(result['status'], 'no_update')
        self.assertEqual(changes, {})

    def test_duplicate_operation_is_rejected(self):
        draft = self.sample()
        draft['entries'] *= 2
        with self.assertRaisesRegex(UpdateError, '重复操作'):
            plan(self.root, draft)

    def test_video_tracking_url_is_same_source(self):
        from update import canonical_source_url
        a = 'https://www.bilibili.com/video/BV1kt411a7oz/?vd_source=123&spm_id_from=333.1#reply'
        b = 'https://m.bilibili.com/video/BV1kt411a7oz'
        self.assertEqual(canonical_source_url(a), canonical_source_url(b))
        self.assertNotEqual(canonical_source_url(b+'?p=2'), canonical_source_url(b))
        self.assertNotEqual(canonical_source_url('https://example.org/?id=1'), canonical_source_url('https://example.org/?id=2'))

    def test_replacement_cannot_bypass_alias_deduplication(self):
        apply(self.root, self.sample())
        record = next(e for e in load_catalogue(self.root)[0] if e['id']=='new-item')
        replacement = deepcopy(record)
        replacement['aliases'] = ['起点样本']
        batch = self.batch(entries=[{'action':'replace','record':replacement,'before_sha256':digest_record(record)}])
        with self.assertRaisesRegex(UpdateError, '替换不能引入'):
            plan(self.root,batch)

    def test_set_field_reordering_is_semantically_no_update(self):
        batch = self.sample()
        batch['entries'][0]['record']['aliases'] = ['别名甲','别名乙']
        apply(self.root,batch)
        old = next(e for e in load_catalogue(self.root)[0] if e['id']=='new-item')
        row = deepcopy(old); row['aliases'].reverse()
        before = self.snapshot()
        result = apply(self.root,self.batch(entries=[{'action':'replace','record':row,'before_sha256':digest_record(old)}]))
        self.assertEqual(result['status'],'no_update')
        self.assertEqual(before,self.snapshot())


class DiscoveryReceiptTests(unittest.TestCase):
    def test_failure_receipt_is_preserved(self):
        from update import main
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/'failure.json'
            with patch('update.discover',side_effect=UpdateError('HTTP 412 refused')):
                result = main(['discover','--output',str(out)])
            self.assertEqual(result,2)
            self.assertEqual(json.loads(out.read_text())['status'],'unavailable')
            with patch('update.discover') as fetch:
                self.assertEqual(main(['discover','--output',str(out)]),2)
                fetch.assert_not_called()

    def test_discovery_output_cannot_be_written_inside_skill_root(self):
        from update import main
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'bilibili-meme-atlas';(root/'tests').mkdir(parents=True)
            out=root/'tests/discovery.json'
            with patch('update.ROOT',root), patch('update.discover') as fetch:
                self.assertEqual(main(['discover','--output',str(out)]),2)
                fetch.assert_not_called()
            self.assertFalse(out.exists())


class UpdateReleaseIntegrationTests(unittest.TestCase):
    def test_update_remains_verifiable_packageable_and_installable(self):
        from install import install as install_archive, uninstall as uninstall_archive
        from package import build_archive
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            base_root = base / "base-source"
            copy_project_fixture(base_root)
            root = base / "bilibili-meme-atlas"
            copy_project_fixture(root)
            before_version = load_catalogue(root)[2]["version"]
            major, minor, patch_version = map(int, before_version.split("."))
            expected_version = f"{major}.{minor}.{patch_version + 1}"
            checked = "2026-10-23"
            new_source = source("integration-source", checked=checked)
            new_source["url"] = "https://example.org/integration-source"
            new_entry = entry(
                "integration-entry", "合成更新词条",
                era="2021-2026", source=new_source["id"])
            batch = {
                "format": UPDATE_FORMAT,
                "base_manifest_sha256": fingerprint(root),
                "checked_at": checked,
                "sources": [{"action": "add", "record": new_source}],
                "entries": [{"action": "add", "record": new_entry}],
            }
            result = apply(root, batch)
            self.assertEqual(result["version"], expected_version)

            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            checks = (
                ([sys.executable, "-B", str(root / "scripts" / "build.py"), "--check"], 60),
                ([sys.executable, "-B", str(root / "scripts" / "validate.py")], 60),
            )
            for command, timeout in checks:
                verified = subprocess.run(
                    command, cwd=base, env=environment, text=True, encoding="utf-8",
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
                self.assertEqual(verified.returncode, 0, verified.stdout)

            base_archive = base / f"bilibili-meme-atlas-v{before_version}.zip"
            base_digest = build_archive(base_root, base_archive)["sha256"]
            archive = base / f"bilibili-meme-atlas-v{expected_version}.zip"
            archive_digest = build_archive(root, archive)["sha256"]
            parent = base / "installed"
            parent.mkdir()

            installed = install_archive(base_archive, parent, base_digest)
            upgraded = install_archive(
                archive, parent, archive_digest,
                expected_current=installed["identity"])
            target = Path(upgraded["path"])
            query = subprocess.run(
                [sys.executable, str(target / "scripts" / "atlas.py"),
                 "get", "合成更新词条"],
                cwd=base, text=True, encoding="utf-8",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            self.assertEqual(query.returncode, 0, query.stdout)
            self.assertIn("integration-entry", query.stdout)
            rolled_back = install_archive(
                base_archive, parent, base_digest,
                expected_current=upgraded["identity"])
            removed = uninstall_archive(parent, rolled_back["identity"])
            self.assertTrue(Path(removed["backup"]).is_dir())
            self.assertFalse(target.exists())

if __name__ == "__main__":
    unittest.main()
