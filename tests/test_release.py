"""Provider-neutral local release byte-contract tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from package import build_archive
import package as package_module
from release import (ReleaseError, check_local, describe, expected_checksums,
                     prepare_installable_request, prepare_request, read_json,
                     validate_manifest, verify_installable_observation,
                     verify_observation)
import release as release_module
from test_contracts import copy_project_fixture


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.base = Path(cls.fixture.name)
        cls.assets = cls.base / "assets"
        cls.assets.mkdir()
        cls.version = json.loads(
            (ROOT / "references" / "metadata.json").read_text(encoding="utf-8"))["version"]
        cls.notes = cls.base / "notes.md"
        cls.notes.write_bytes((
            f"# v{cls.version} — 本地发行字节合同\n\n"
            "本说明用于验证源码、归档和回读内容保持一致。\n"
        ).encode("utf-8"))
        cls.archive = cls.assets / f"bilibili-meme-atlas-v{cls.version}.zip"
        cls.archive_report = build_archive(ROOT, cls.archive)
        provisional = {
            "package": {
                "kind": "skill_zip",
                "name": cls.archive.name,
                "sha256": cls.archive_report["sha256"],
            }
        }
        (cls.assets / f"SHA256SUMS-v{cls.version}.txt").write_bytes(
            expected_checksums(provisional))
        cls.manifest = describe(ROOT, cls.notes, cls.assets)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def test_describe_and_validate_complete_local_release(self):
        manifest = validate_manifest(self.manifest)
        self.assertEqual(manifest["version"], self.version)
        self.assertEqual(manifest["package"]["kind"], "skill_zip")
        bodies = check_local(manifest, ROOT, self.notes, self.assets)
        self.assertEqual(
            set(bodies),
            {f"bilibili-meme-atlas-v{self.version}.zip",
             f"SHA256SUMS-v{self.version}.txt"})

    def test_release_description_rejects_source_change_during_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            copy_project_fixture(source)
            original_scan = package_module._scan_project
            calls = 0

            def change_before_second_scan(root):
                nonlocal calls
                calls += 1
                if calls == 2:
                    (source / "references" / "schema.json").write_text(
                        "not json\n", encoding="utf-8")
                return original_scan(root)

            with patch(
                    "package._scan_project",
                    side_effect=change_before_second_scan):
                with self.assertRaisesRegex(ValueError, "捕获期间"):
                    describe(source, self.notes)

    def test_prepare_request_and_verify_observation(self):
        request = prepare_request(self.manifest, ROOT, self.notes, self.assets)
        self.assertEqual(request["tag"], "v" + self.version)
        observation = {
            "tag": request["tag"],
            "name": request["name"],
            "body": request["body"],
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": row["name"],
                    "size": row["bytes"],
                    "digest": "sha256:" + row["sha256"],
                }
                for row in request["assets"]
            ],
        }
        result = verify_observation(
            self.manifest, ROOT, self.notes, observation, self.assets)
        self.assertEqual(result["result"], "MATCH")

        names_only = dict(observation)
        names_only["assets"] = [{"name": row["name"]} for row in request["assets"]]
        with self.assertRaises(ReleaseError):
            verify_observation(
                self.manifest, ROOT, self.notes, names_only, self.assets)

    def test_installable_release_binds_repository_revision_and_assets(self):
        repository = "example/bilibili-meme-atlas"
        revision = "a" * 40
        request = prepare_installable_request(
            self.manifest, ROOT, self.notes, repository, revision, self.assets)
        self.assertEqual(request["repository"], repository)
        self.assertEqual(request["source_revision"], revision)
        observation = {
            "tag": request["tag"],
            "name": request["name"],
            "body": request["body"],
            "draft": False,
            "prerelease": False,
            "repository": repository,
            "tag_target": revision,
            "assets": [
                {"name": row["name"], "size": row["bytes"],
                 "digest": "sha256:" + row["sha256"]}
                for row in request["assets"]
            ],
        }
        result = verify_installable_observation(
            self.manifest, ROOT, self.notes, observation,
            repository, revision, self.assets)
        self.assertTrue(result["installable"])
        for key, value in (("repository", "other/project"),
                           ("tag_target", "b" * 40)):
            changed = dict(observation)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ReleaseError):
                verify_installable_observation(
                    self.manifest, ROOT, self.notes, changed,
                    repository, revision, self.assets)

    def test_source_only_release_has_no_assets(self):
        manifest = describe(ROOT, self.notes)
        self.assertEqual(manifest["package"], {"kind": "source_only"})
        request = prepare_request(manifest, ROOT, self.notes)
        self.assertEqual(request["assets"], [])
        with self.assertRaises(ReleaseError):
            prepare_installable_request(
                manifest, ROOT, self.notes,
                "example/bilibili-meme-atlas", "a" * 40, self.assets)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "unexpected").write_text("x", encoding="utf-8")
            with self.assertRaises(ReleaseError):
                check_local(manifest, ROOT, self.notes, directory)

    def test_changed_notes_or_archive_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            notes = base / "notes.md"
            notes.write_bytes(self.notes.read_bytes() + b"changed\n")
            with self.assertRaises(ReleaseError):
                check_local(self.manifest, ROOT, notes, self.assets)
            assets = base / "assets"
            shutil.copytree(self.assets, assets)
            archive = assets / f"bilibili-meme-atlas-v{self.version}.zip"
            archive.write_bytes(archive.read_bytes() + b"changed")
            with self.assertRaises(ReleaseError):
                check_local(self.manifest, ROOT, self.notes, assets)

    def test_changed_source_and_unknown_manifest_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bilibili-meme-atlas"
            copy_project_fixture(source)
            skill = source / "SKILL.md"
            skill.write_bytes(skill.read_bytes() + b"\n")
            with self.assertRaises(ReleaseError):
                check_local(self.manifest, source, self.notes, self.assets)
        changed = json.loads(json.dumps(self.manifest))
        changed["repository"] = "example/project"
        with self.assertRaises(ReleaseError):
            validate_manifest(changed)

    def test_future_cutoff_cannot_enter_release_description(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bilibili-meme-atlas"
            copy_project_fixture(source)
            metadata = source / "references" / "metadata.json"
            value = json.loads(metadata.read_text(encoding="utf-8"))
            value["knowledge_cutoff"] = "2099-12-31"
            metadata.write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "UTC"):
                describe(source, self.notes)

    def test_duplicate_json_fields_and_bad_readback_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text('{"format":"a","format":"b"}', encoding="utf-8")
            with self.assertRaises(ReleaseError):
                read_json(path)
        request = prepare_request(self.manifest, ROOT, self.notes, self.assets)
        observation = {
            "tag": request["tag"],
            "name": request["name"],
            "body": request["body"],
            "draft": False,
            "prerelease": False,
            "assets": [],
        }
        with self.assertRaises(ReleaseError):
            verify_observation(
                self.manifest, ROOT, self.notes, observation, self.assets)

    def test_checksum_file_is_strict_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary) / "assets"
            shutil.copytree(self.assets, assets)
            checksum = assets / f"SHA256SUMS-v{self.version}.txt"
            checksum.write_bytes(checksum.read_bytes().replace(b"\n", b"\r\n"))
            with self.assertRaises(ReleaseError):
                describe(ROOT, self.notes, assets)

    def test_prepare_uses_the_exact_note_bytes_that_passed_checking(self):
        with tempfile.TemporaryDirectory() as temporary:
            notes = Path(temporary) / "notes.md"
            original_body = self.notes.read_bytes()
            notes.write_bytes(original_body)
            manifest = describe(ROOT, notes)
            original_check = release_module._checked_local

            def change_after_check(*args, **kwargs):
                result = original_check(*args, **kwargs)
                notes.write_bytes(original_body + b"changed\n")
                return result

            with patch("release._checked_local", side_effect=change_after_check):
                request = prepare_request(manifest, ROOT, notes)
            self.assertEqual(request["body"].encode("utf-8"), original_body)

    def test_readback_compares_the_exact_note_bytes_that_passed_checking(self):
        with tempfile.TemporaryDirectory() as temporary:
            notes = Path(temporary) / "notes.md"
            original_body = self.notes.read_bytes()
            notes.write_bytes(original_body)
            manifest = describe(ROOT, notes)
            request = prepare_request(manifest, ROOT, notes)
            observation = {
                "tag": request["tag"], "name": request["name"],
                "body": request["body"], "draft": False,
                "prerelease": False, "assets": [],
            }
            original_check = release_module._checked_local

            def change_after_check(*args, **kwargs):
                result = original_check(*args, **kwargs)
                notes.write_bytes(original_body + b"changed\n")
                return result

            with patch("release._checked_local", side_effect=change_after_check):
                result = verify_observation(
                    manifest, ROOT, notes, observation)
            self.assertEqual(result["result"], "MATCH")


if __name__ == "__main__":
    unittest.main()
