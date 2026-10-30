"""Deterministic local archive and project-boundary tests."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_STORED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from package import (PackageError, archive_name, build_archive, capture_files, source_files,
                     verify_archive)
import package as package_module
from test_contracts import copy_project_fixture, create_symlink


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def test_archive_is_deterministic_and_matches_project_bytes(self):
        first = self.base / "first.zip"
        second = self.base / "second.zip"
        report_a = build_archive(ROOT, first)
        report_b = build_archive(ROOT, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(report_a["sha256"], report_b["sha256"])
        expected = source_files(ROOT)
        self.assertEqual(report_a["files"], len(expected))
        self.assertEqual(verify_archive(first, expected)["files"], len(expected))
        with ZipFile(first) as archive:
            self.assertEqual(len(archive.infolist()), len(expected))
            for item in archive.infolist():
                self.assertEqual(item.compress_type, ZIP_STORED)
                self.assertEqual(item.create_system, 3)
                self.assertEqual(item.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(stat.S_IMODE(item.external_attr >> 16), 0o644)

    def test_archive_includes_rights_and_runtime_lifecycle(self):
        archive_path = self.base / "complete.zip"
        build_archive(ROOT, archive_path)
        with ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        for relative in (
            "LICENSE", "CONTENT_RIGHTS.md", "references/rights.json",
            "scripts/package.py",
            "scripts/install.py", "scripts/release.py",
        ):
            self.assertIn(archive_name(relative), names)

    def test_verify_rejects_omission_and_changed_bytes(self):
        archive_path = self.base / "complete.zip"
        build_archive(ROOT, archive_path)
        with ZipFile(archive_path) as archive:
            rows = [(item, archive.read(item.filename))
                    for item in archive.infolist()]
        omitted = self.base / "omitted.zip"
        with ZipFile(omitted, "w") as archive:
            for item, body in rows[1:]:
                archive.writestr(item, body)
        with self.assertRaises(PackageError):
            verify_archive(omitted, source_files(ROOT))
        changed = self.base / "changed.zip"
        with ZipFile(changed, "w") as archive:
            for index, (item, body) in enumerate(rows):
                archive.writestr(item, body + (b"changed" if index == 0 else b""))
        with self.assertRaises(PackageError):
            verify_archive(changed, source_files(ROOT))

    def test_verify_report_describes_the_same_bytes_that_were_checked(self):
        archive_path = self.base / "snapshot.zip"
        build_archive(ROOT, archive_path)
        original = archive_path.read_bytes()
        original_testzip = ZipFile.testzip

        def replace_path_after_crc(archive):
            result = original_testzip(archive)
            archive_path.write_bytes(b"not a zip archive")
            return result

        with patch.object(ZipFile, "testzip", replace_path_after_crc):
            report = verify_archive(archive_path, source_files(ROOT))
        self.assertEqual(report["bytes"], len(original))
        self.assertEqual(report["sha256"], hashlib.sha256(original).hexdigest())
        self.assertNotEqual(
            report["sha256"], hashlib.sha256(archive_path.read_bytes()).hexdigest())

    def test_capture_is_independent_of_current_source_file_contract(self):
        copy = self.base / "previous-files"
        copy_project_fixture(copy)
        (copy / "AGENT_INSTALL.md").unlink()
        (copy / ".github/dependabot.yml").unlink()
        renamed = copy / "historical-maintenance"
        renamed.mkdir()
        (copy / ".github/pull_request_template.md").rename(renamed / "review.md")
        captured = capture_files(copy)
        self.assertIn("historical-maintenance/review.md", captured)
        self.assertNotIn("AGENT_INSTALL.md", captured)
        with self.assertRaises(PackageError):
            source_files(copy)

    def test_capture_rejects_empty_directories_and_pending_update_members(self):
        copy = self.base / "captured"
        copy_project_fixture(copy)
        empty = copy / "extra-empty"
        empty.mkdir()
        with self.assertRaisesRegex(PackageError, "空目录"):
            capture_files(copy)
        empty.rmdir()
        lock = copy / ".atlas-update.lock"
        lock.mkdir()
        (lock / "operation.json").write_bytes(b"{}\n")
        with self.assertRaisesRegex(PackageError, "更新"):
            capture_files(copy)

    def test_unknown_file_and_cache_are_rejected(self):
        copy = self.base / "bilibili-meme-atlas"
        copy_project_fixture(copy)
        (copy / ".env").write_text("TOKEN=example", encoding="utf-8")
        with self.assertRaises(PackageError):
            source_files(copy)
        (copy / ".env").unlink()
        cache = copy / "scripts" / "__pycache__"
        cache.mkdir()
        (cache / "atlas.pyc").write_bytes(b"synthetic")
        with self.assertRaises(PackageError):
            source_files(copy)
        shutil.rmtree(cache)

    def test_source_symlink_is_rejected(self):
        copy = self.base / "bilibili-meme-atlas"
        copy_project_fixture(copy)
        link = copy / "references" / "linked.json"
        create_symlink(self, link, copy / "SKILL.md")
        with self.assertRaises(PackageError):
            source_files(copy)

    def test_invalid_metadata_or_license_cannot_be_packaged(self):
        copy = self.base / "metadata-copy"
        copy_project_fixture(copy)
        metadata = copy / "references" / "metadata.json"
        value = json.loads(metadata.read_text(encoding="utf-8"))
        value["license_status"] = "Proprietary"
        metadata.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(PackageError):
            source_files(copy)

    def test_future_cutoff_cannot_be_packaged(self):
        copy = self.base / "future-cutoff"
        copy_project_fixture(copy)
        metadata = copy / "references" / "metadata.json"
        value = json.loads(metadata.read_text(encoding="utf-8"))
        value["knowledge_cutoff"] = "2099-12-31"
        metadata.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        with self.assertRaisesRegex(PackageError, "UTC"):
            source_files(copy)
        shutil.rmtree(copy)
        copy_project_fixture(copy)
        (copy / "LICENSE").write_text("not a license\n", encoding="utf-8")
        with self.assertRaises(PackageError):
            source_files(copy)

    def test_entry_in_a_post_cutoff_shard_cannot_be_archived(self):
        copy = self.base / "future-entry"
        copy_project_fixture(copy)
        current_path = copy / "references" / "2021-2026.json"
        current = json.loads(current_path.read_text(encoding="utf-8"))
        future_entry = current.pop()
        future_entry["era"] = "2027-2031"
        current_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        (copy / "references" / "2027-2031.json").write_text(
            json.dumps([future_entry], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        archive = self.base / "future-entry.zip"
        with self.assertRaisesRegex(PackageError, "时期起年晚于"):
            build_archive(copy, archive)
        self.assertFalse(archive.exists())

    def test_invalid_corpus_schema_or_stale_projection_cannot_be_packaged(self):
        for label, relative, body in (
            ("corpus", "references/2009-2015.json", "not json\n"),
            ("schema", "references/schema.json", "not json\n"),
            ("projection", "references/catalog.md", "stale\n"),
        ):
            with self.subTest(label=label):
                copy = self.base / ("invalid-" + label)
                copy_project_fixture(copy)
                (copy / relative).write_text(body, encoding="utf-8")
                with self.assertRaises(PackageError):
                    source_files(copy)

    def test_capture_rejects_tree_changes_before_snapshot_validation(self):
        mutations = (
            ("metadata", "references/metadata.json", lambda path: path.write_text(
                json.dumps({
                    **json.loads(path.read_text(encoding="utf-8")),
                    "license_status": "Proprietary",
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")),
            ("corpus", "references/2009-2015.json",
             lambda path: path.write_text("not json\n", encoding="utf-8")),
            ("schema", "references/schema.json",
             lambda path: path.write_text("not json\n", encoding="utf-8")),
        )
        for label, relative, mutate in mutations:
            with self.subTest(label=label):
                copy = self.base / ("capture-change-" + label)
                copy_project_fixture(copy)
                original_scan = package_module._scan_project
                calls = 0

                def mutate_before_second_scan(root):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        mutate(copy / relative)
                    return original_scan(root)

                archive = self.base / (label + "-changed.zip")
                with patch(
                        "package._scan_project",
                        side_effect=mutate_before_second_scan):
                    with self.assertRaisesRegex(PackageError, "捕获期间"):
                        build_archive(copy, archive)
                self.assertFalse(archive.exists())

    def test_archive_path_and_output_boundaries(self):
        for value in ("../escape", "/absolute", "scripts/C:bad", "CON"):
            with self.subTest(value=value), self.assertRaises(PackageError):
                archive_name(value)
        before = (ROOT / "SKILL.md").read_bytes()
        with self.assertRaises(PackageError):
            build_archive(ROOT, ROOT / "SKILL.md")
        self.assertEqual((ROOT / "SKILL.md").read_bytes(), before)

    def test_archive_output_parent_cannot_be_a_symlink(self):
        real = self.base / "real"
        real.mkdir()
        linked = self.base / "linked"
        create_symlink(self, linked, real, target_is_directory=True)
        with self.assertRaises(PackageError):
            build_archive(ROOT, linked / "archive.zip")


if __name__ == "__main__":
    unittest.main()
