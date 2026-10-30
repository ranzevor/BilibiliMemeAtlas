"""Local archive install, upgrade, removal, rollback, and recovery tests."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import install as installer
from install import (LOCK_NAME, NAME, RECORD_NAME, archive_files, install,
                     recover, recovery_info, tree_identity, uninstall,
                     validate_installation)
from package import PackageError, build_archive, capture_files
from update import UpdateError
import update as updater
import writer_lock
from writer_lock import writer_lease
from test_contracts import create_symlink
from validate import validate_project_files


def write_member_archive(path, files):
    with ZipFile(path, "w") as archive:
        for name, body in sorted(files.items()):
            item = ZipInfo(NAME + "/" + name)
            item.external_attr = 0o100644 << 16
            archive.writestr(item, body)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.archive = Path(cls.fixture.name) / "source.zip"
        cls.report = build_archive(ROOT, cls.archive)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.parent = self.base / "项目 space" / "skills"
        self.parent.mkdir(parents=True)

    def test_install_query_upgrade_uninstall_and_manual_restore(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        self.assertEqual(validate_installation(target), initial["identity"])
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        commands = (
            ("atlas.py", ["get", "金坷垃"]),
            ("validate.py", []),
            ("update.py", ["fingerprint"]),
        )
        for script, arguments in commands:
            result = subprocess.run(
                [sys.executable, str(target / "scripts" / script), *arguments],
                cwd=self.base, capture_output=True, text=True,
                env=environment, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        upgraded = install(
            self.archive, self.parent, self.report["sha256"],
            expected_current=initial["identity"])
        self.assertEqual(
            tree_identity(Path(upgraded["backup"])), initial["identity"])
        removed = uninstall(self.parent, upgraded["identity"])
        self.assertFalse(target.exists())
        os.replace(removed["backup"], target)
        self.assertEqual(validate_installation(target), initial["identity"])

    def variant(self, label, transform):
        with ZipFile(self.archive) as archive:
            files = {item.filename[len(NAME) + 1:]: archive.read(item)
                     for item in archive.infolist()}
        transform(files)
        path = self.base / (label + ".zip")
        return path, write_member_archive(path, files), files

    def test_three_file_sets_upgrade_rollback_and_manual_restore(self):
        def previous(files):
            del files["AGENT_INSTALL.md"]
            del files[".github/dependabot.yml"]

        def following(files):
            files["docs/examples/query.md"] = b"# Offline query examples\n"
            files[".github/PULL_REQUEST_TEMPLATE/default.md"] = files.pop(
                ".github/pull_request_template.md")
            del files["tests/test_release.py"]

        old_archive, old_digest, old_files = self.variant("previous", previous)
        next_archive, next_digest, next_files = self.variant("following", following)
        initial = install(old_archive, self.parent, old_digest)
        target = self.parent / NAME
        self.assertEqual(capture_files(target), old_files)
        self.assertTrue(validate_project_files(target))
        upgraded = install(
            self.archive, self.parent, self.report["sha256"],
            expected_current=initial["identity"])
        old_backup = Path(upgraded["backup"])
        self.assertEqual(capture_files(old_backup), old_files)
        self.assertEqual(tree_identity(old_backup), initial["identity"])
        following_result = install(
            next_archive, self.parent, next_digest,
            expected_current=upgraded["identity"])
        self.assertEqual(capture_files(target), next_files)
        result = subprocess.run(
            [sys.executable, str(target / "scripts/atlas.py"), "stats"],
            cwd=self.base, capture_output=True, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["entries"], 303)
        self.assertEqual(tree_identity(target), following_result["identity"])
        rolled_back = install(
            old_archive, self.parent, old_digest,
            expected_current=following_result["identity"])
        self.assertEqual(rolled_back["identity"], initial["identity"])
        removed = uninstall(self.parent, rolled_back["identity"])
        self.assertEqual(capture_files(Path(removed["backup"])), old_files)
        self.assertTrue(old_backup.resolve().is_relative_to(self.base.resolve()))
        self.assertTrue(target.resolve().is_relative_to(self.base.resolve()))
        os.replace(old_backup, target)
        self.assertEqual(tree_identity(target), initial["identity"])

    def test_previous_file_set_is_restored_after_new_readback_failure(self):
        def previous(files):
            del files["AGENT_INSTALL.md"]
            del files[".github/dependabot.yml"]

        archive, digest, expected = self.variant("previous", previous)
        initial = install(archive, self.parent, digest)
        target = self.parent / NAME
        original = installer.validate_installation

        def fail_new_readback(path, **options):
            if Path(path) == target and (target / "AGENT_INSTALL.md").exists():
                raise PackageError("synthetic new-version readback failure")
            return original(path, **options)

        with patch("install.validate_installation", side_effect=fail_new_readback):
            with self.assertRaisesRegex(PackageError, "readback failure"):
                install(
                    self.archive, self.parent, self.report["sha256"],
                    expected_current=initial["identity"])
        self.assertEqual(capture_files(target), expected)
        self.assertEqual(tree_identity(target), initial["identity"])
        self.assertFalse((self.parent / LOCK_NAME).exists())
        failed = list(self.parent.glob(f".{NAME}.failed-*"))
        self.assertEqual(len(failed), 1)
        self.assertTrue((failed[0] / "AGENT_INSTALL.md").is_file())

    def test_identity_and_removal_do_not_reapply_source_content_rules(self):
        def historical(files):
            files["references/catalog.md"] = b"# Previous catalogue rendering\n"
            files["agents/openai.yaml"] = files["agents/openai.yaml"].replace(
                "B站热梗知识库".encode("utf-8"), b"Previous display name")
            files["references/metadata.json"] = b"previous metadata representation\n"

        archive, digest, expected = self.variant("historical-content", historical)
        result = install(archive, self.parent, digest)
        target = self.parent / NAME
        self.assertEqual(capture_files(target), expected)
        self.assertEqual(tree_identity(target), result["identity"])
        removed = uninstall(self.parent, result["identity"])
        self.assertEqual(tree_identity(Path(removed["backup"])), result["identity"])

    def test_archive_member_omission_cannot_reuse_original_digest(self):
        archive, _digest, _files = self.variant(
            "omitted", lambda files: files.pop("scripts/atlas.py"))
        with self.assertRaisesRegex(PackageError, "校验和"):
            install(archive, self.parent, self.report["sha256"])
        self.assertFalse((self.parent / NAME).exists())

    def test_staging_readback_checks_exact_archive_bytes(self):
        original = installer.validate_installation

        def corrupt_stage(path, **options):
            (Path(path) / "SKILL.md").write_bytes(b"changed after unpacking\n")
            return original(path, **options)

        with patch("install.validate_installation", side_effect=corrupt_stage):
            with self.assertRaisesRegex(PackageError, "字节与归档不一致"):
                install(self.archive, self.parent, self.report["sha256"])
        self.assertFalse((self.parent / NAME).exists())
        self.assertFalse((self.parent / LOCK_NAME).exists())

    def test_extra_file_or_empty_directory_blocks_identity_bound_replacement(self):
        result = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        extra = target / "user-content.txt"
        extra.write_bytes(b"retained user content\n")
        self.assertNotEqual(tree_identity(target), result["identity"])
        with self.assertRaises(PackageError):
            uninstall(self.parent, result["identity"])
        self.assertTrue(extra.is_file())
        extra.unlink()
        empty = target / "unrecorded-empty"
        empty.mkdir()
        with self.assertRaisesRegex(PackageError, "空目录"):
            tree_identity(target)
        with self.assertRaises(PackageError):
            install(self.archive, self.parent, self.report["sha256"],
                    expected_current=result["identity"])
        self.assertTrue(empty.is_dir())

    def test_archive_rejects_portable_namespace_conflicts_and_control_members(self):
        cases = (
            {"folder/a.txt": b"a", "FOLDER/b.txt": b"b"},
            {"file": b"a", "file/nested.txt": b"b"},
            {".GIT/config": b"data"},
            {"scripts/__pycache__/cached.pyc": b"data"},
            {".atlas-update.lock/operation.json": b"data"},
        )
        for index, members in enumerate(cases):
            path = self.base / f"namespace-{index}.zip"
            digest = write_member_archive(path, {"SKILL.md": b"entry", **members})
            with self.subTest(members=members), self.assertRaises(PackageError):
                archive_files(path, digest)

    def test_archive_count_file_and_expanded_size_limits(self):
        path = self.base / "limits.zip"
        files = {"SKILL.md": b"entry", "data.bin": b"x" * 2049}
        digest = write_member_archive(path, files)
        with patch("install.MAX_FILES", 1), self.assertRaises(PackageError):
            archive_files(path, digest)
        with patch("install.MAX_PROJECT_FILE_BYTES", 4), self.assertRaises(PackageError):
            archive_files(path, digest)
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            for name, body in files.items():
                archive.writestr(NAME + "/" + name, body)
        self.assertLess(path.stat().st_size, 2048)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch("install.MAX_ARCHIVE_BYTES", 2048), self.assertRaises(PackageError):
            archive_files(path, digest)

    def test_bad_checksum_never_creates_target(self):
        with self.assertRaisesRegex(PackageError, "校验和"):
            install(self.archive, self.parent, "0" * 64)
        self.assertFalse((self.parent / NAME).exists())

    def test_upgrade_and_uninstall_refuse_changed_current_tree(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        (target / "SKILL.md").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(PackageError):
            install(
                self.archive, self.parent, self.report["sha256"],
                expected_current=initial["identity"])
        self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), "changed\n")
        with self.assertRaises(PackageError):
            uninstall(self.parent, initial["identity"])

    def test_upgrade_replace_failure_restores_previous_directory(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        original = os.replace
        calls = 0

        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic replacement failure")
            return original(source, destination)

        with patch("install.os.replace", side_effect=fail_second):
            with self.assertRaises(OSError):
                install(
                    self.archive, self.parent, self.report["sha256"],
                    expected_current=initial["identity"])
        self.assertEqual(tree_identity(target), initial["identity"])
        self.assertFalse((self.parent / LOCK_NAME).exists())

    def test_shared_writer_lease_covers_upgrade_move_and_readback(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        phases = []
        original_record = installer._write_record
        original_replace = os.replace
        original_finish = installer._finish_lock

        def blocked(phase):
            with self.assertRaisesRegex(UpdateError, "写入者"):
                updater.apply(target, {})
            phases.append(phase)

        def record_hook(*args, **kwargs):
            result = original_record(*args, **kwargs)
            blocked("pre-move")
            return result

        def replace_hook(source, destination):
            result = original_replace(source, destination)
            source = Path(source)
            if (Path(destination) == target and source.name == NAME
                    and source.parent.name.startswith(f".{NAME}.stage-")):
                blocked("post-move")
            return result

        def finish_hook(lock, handle, expected_token):
            blocked("pre-return")
            return original_finish(lock, handle, expected_token)

        with patch("install._write_record", side_effect=record_hook), patch(
                "install.os.replace", side_effect=replace_hook), patch(
                "install._finish_lock", side_effect=finish_hook):
            result = install(
                self.archive, self.parent, self.report["sha256"],
                expected_current=initial["identity"])
        self.assertEqual(
            phases, ["pre-move", "post-move", "pre-return"])
        self.assertEqual(tree_identity(target), result["identity"])

    def test_update_writer_blocks_upgrade_and_uninstall(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        attempts = []

        def inside_update(root, batch, **options):
            with self.assertRaisesRegex(PackageError, "写入者"):
                install(
                    self.archive, self.parent, self.report["sha256"],
                    expected_current=initial["identity"])
            attempts.append("upgrade")
            with self.assertRaisesRegex(PackageError, "写入者"):
                uninstall(self.parent, initial["identity"])
            attempts.append("uninstall")
            return {"status": "synthetic"}

        with patch("update._apply_locked", side_effect=inside_update):
            self.assertEqual(
                updater.apply(target, {})["status"], "synthetic")
        self.assertEqual(attempts, ["upgrade", "uninstall"])
        self.assertEqual(tree_identity(target), initial["identity"])

    @unittest.skipUnless(os.name == "posix", "POSIX 写入租约故障注入")
    def test_writer_lease_release_faults_preserve_lifecycle_results(self):
        import fcntl
        original_flock = fcntl.flock

        def fail_unlock(descriptor, operation):
            if operation == fcntl.LOCK_UN:
                raise OSError("synthetic unlock failure")
            return original_flock(descriptor, operation)

        with patch("fcntl.flock", side_effect=fail_unlock):
            installed = install(
                self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        self.assertEqual(tree_identity(target), installed["identity"])

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
            upgraded = install(
                self.archive, self.parent, self.report["sha256"],
                expected_current=installed["identity"])
        self.assertEqual(tree_identity(target), upgraded["identity"])

        with patch("fcntl.flock", side_effect=fail_unlock):
            removed = uninstall(self.parent, upgraded["identity"])
        self.assertFalse(target.exists())
        self.assertEqual(
            tree_identity(Path(removed["backup"])), upgraded["identity"])

    def test_install_retirement_preserves_replaced_or_extended_lock(self):
        for mode in ("replace", "extend"):
            with self.subTest(mode=mode):
                parent = self.base / f"{mode}-skills"
                parent.mkdir()
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
                    with self.assertRaisesRegex(PackageError, "保留|替换|改变"):
                        install(self.archive, parent, self.report["sha256"])
                self.assertTrue((parent / NAME).is_dir())
                retired = preserved["retired"]
                self.assertEqual(
                    (retired / "unknown-owned-content").read_text(), "unknown")
                if mode == "replace":
                    self.assertTrue(
                        (preserved["displaced"] / RECORD_NAME).is_file())
                    self.assertTrue(
                        (preserved["displaced"] / installer.LEASE_NAME).is_file())
                else:
                    self.assertTrue((retired / RECORD_NAME).is_file())
                    self.assertTrue(
                        (retired / installer.LEASE_NAME).is_file())

    def test_record_fsync_failure_leaves_no_target_or_live_lock(self):
        with patch("install.os.fsync",
                   side_effect=OSError("synthetic record fsync failure")):
            with self.assertRaisesRegex(OSError, "fsync"):
                install(self.archive, self.parent, self.report["sha256"])
        self.assertFalse((self.parent / NAME).exists())
        self.assertFalse((self.parent / LOCK_NAME).exists())

    def test_failed_new_tree_is_retained_outside_temporary_stage(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        original = installer.validate_installation

        def fail_target_readback(path, **options):
            identity = original(path, **options)
            if Path(path) == target:
                raise PackageError("synthetic target readback failure")
            return identity

        with patch("install.validate_installation", side_effect=fail_target_readback):
            with self.assertRaisesRegex(PackageError, "synthetic"):
                install(
                    self.archive, self.parent, self.report["sha256"],
                    expected_current=initial["identity"])
        self.assertEqual(tree_identity(target), initial["identity"])
        failed = list(self.parent.glob(f".{NAME}.failed-*"))
        self.assertEqual(len(failed), 1)
        self.assertEqual(validate_installation(failed[0]), initial["identity"])
        self.assertFalse((self.parent / LOCK_NAME).exists())

    def test_upgrade_detects_change_during_target_move_and_preserves_it(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        changed = (target / "CONTENT_RIGHTS.md").read_bytes() + b"\n"
        original = os.replace
        changed_once = False

        def mutate_then_move(source, destination):
            nonlocal changed_once
            if Path(source) == target and not changed_once:
                changed_once = True
                (target / "CONTENT_RIGHTS.md").write_bytes(changed)
            return original(source, destination)

        with patch("install.os.replace", side_effect=mutate_then_move):
            with self.assertRaisesRegex(PackageError, "移动期间"):
                install(
                    self.archive, self.parent, self.report["sha256"],
                    expected_current=initial["identity"])
        self.assertEqual((target / "CONTENT_RIGHTS.md").read_bytes(), changed)
        self.assertFalse((self.parent / LOCK_NAME).exists())

    def test_upgrade_keeps_unknown_target_and_recovery_state(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        original = os.replace
        appeared = False

        def move_then_occupy(source, destination):
            nonlocal appeared
            original(source, destination)
            if (Path(source) == target
                    and Path(destination).name.startswith(f".{NAME}.backup-")
                    and not appeared):
                appeared = True
                target.mkdir()
                (target / "owned.txt").write_bytes(b"unknown content")

        with patch("install.os.replace", side_effect=move_then_occupy):
            with self.assertRaisesRegex(PackageError, "保留待恢复状态"):
                install(self.archive, self.parent, self.report["sha256"],
                        expected_current=initial["identity"])
        self.assertEqual((target / "owned.txt").read_bytes(), b"unknown content")
        lock = self.parent / LOCK_NAME
        record = json.loads((lock / "operation.json").read_bytes())
        self.assertEqual(tree_identity(self.parent / record["backup"]), initial["identity"])
        self.assertTrue((self.parent / record["stage"]).is_dir())
        self.assertEqual(list(self.parent.glob(f".{NAME}.failed-*")), [])

    def test_upgrade_preserves_target_when_object_identity_is_unreadable(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        original_validate = installer.validate_installation
        original_object = installer._directory_object
        observed = []

        def fail_readback(path, **options):
            if Path(path) == target:
                value = target.stat()
                observed.append((value.st_dev, value.st_ino))
                raise PackageError("synthetic target readback failure")
            return original_validate(path, **options)

        def unreadable_target(path):
            if Path(path) == target:
                raise PermissionError("synthetic unreadable target identity")
            return original_object(path)

        with patch("install.validate_installation", side_effect=fail_readback), patch(
                "install._directory_object", side_effect=unreadable_target):
            with self.assertRaisesRegex(PackageError, "保留待恢复状态"):
                install(self.archive, self.parent, self.report["sha256"],
                        expected_current=initial["identity"])
        value = target.stat()
        self.assertEqual(observed, [(value.st_dev, value.st_ino)])
        self.assertEqual(tree_identity(target), initial["identity"])
        record = json.loads((self.parent / LOCK_NAME / "operation.json").read_bytes())
        self.assertEqual(tree_identity(self.parent / record["backup"]), initial["identity"])
        self.assertTrue((self.parent / record["stage"]).parent.is_dir())
        self.assertEqual(list(self.parent.glob(f".{NAME}.failed-*")), [])

    def test_upgrade_preserves_unknown_replaced_during_rollback_move(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        displaced = self.base / "displaced-candidate"
        original_replace = os.replace
        original_validate = installer.validate_installation
        replaced = False

        def fail_readback(path, **options):
            if Path(path) == target:
                raise PackageError("synthetic target readback failure")
            return original_validate(path, **options)

        def replace_during_move(source, destination):
            nonlocal replaced
            if (Path(source) == target
                    and Path(destination).name.startswith(f".{NAME}.failed-")
                    and not replaced):
                replaced = True
                original_replace(target, displaced)
                target.mkdir()
                (target / "owned.txt").write_bytes(b"changed during move")
            return original_replace(source, destination)

        with patch("install.validate_installation", side_effect=fail_readback), patch(
                "install.os.replace", side_effect=replace_during_move):
            with self.assertRaisesRegex(PackageError, "保留待恢复状态"):
                install(self.archive, self.parent, self.report["sha256"],
                        expected_current=initial["identity"])
        self.assertTrue(replaced)
        self.assertEqual((target / "owned.txt").read_bytes(), b"changed during move")
        self.assertTrue((self.parent / LOCK_NAME / "operation.json").is_file())
        backups = list(self.parent.glob(f".{NAME}.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(tree_identity(backups[0]), initial["identity"])
        self.assertTrue(displaced.is_dir())

    def test_new_install_keeps_unknown_replacement_and_recovery_state(self):
        target = self.parent / NAME
        displaced = self.base / "displaced-candidate"
        original = installer.validate_installation
        replaced = False

        def replace_before_readback(path, **options):
            nonlocal replaced
            if Path(path) == target and not replaced:
                replaced = True
                os.replace(target, displaced)
                target.mkdir()
                (target / "owned.txt").write_bytes(b"unknown replacement")
                raise PackageError("synthetic target replacement")
            return original(path, **options)

        with patch("install.validate_installation", side_effect=replace_before_readback):
            with self.assertRaisesRegex(PackageError, "保留待恢复状态"):
                install(self.archive, self.parent, self.report["sha256"])
        self.assertEqual((target / "owned.txt").read_bytes(), b"unknown replacement")
        self.assertTrue(displaced.is_dir())
        self.assertTrue((self.parent / LOCK_NAME / "operation.json").is_file())
        self.assertTrue(list(self.parent.glob(f".{NAME}.stage-*")))

    def test_uninstall_detects_change_during_target_move_and_preserves_it(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        changed = (target / "CONTENT_RIGHTS.md").read_bytes() + b"\n"
        original = os.replace
        changed_once = False

        def mutate_then_move(source, destination):
            nonlocal changed_once
            if Path(source) == target and not changed_once:
                changed_once = True
                (target / "CONTENT_RIGHTS.md").write_bytes(changed)
            return original(source, destination)

        with patch("install.os.replace", side_effect=mutate_then_move):
            with self.assertRaisesRegex(PackageError, "移动期间"):
                uninstall(self.parent, initial["identity"])
        self.assertEqual((target / "CONTENT_RIGHTS.md").read_bytes(), changed)
        self.assertFalse((self.parent / LOCK_NAME).exists())

    def test_uninstall_keeps_lock_if_unknown_target_appears_after_move(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        original = installer.tree_identity
        appeared = False

        def create_unknown_target(path):
            nonlocal appeared
            identity = original(path)
            if Path(path).name.startswith(f".{NAME}.removed-") and not appeared:
                appeared = True
                target.mkdir()
                (target / "owned.txt").write_text("unknown", encoding="utf-8")
            return identity

        with patch("install.tree_identity", side_effect=create_unknown_target):
            with self.assertRaisesRegex(PackageError, "保留待恢复状态"):
                uninstall(self.parent, initial["identity"])
        self.assertEqual((target / "owned.txt").read_text(), "unknown")
        self.assertTrue((self.parent / LOCK_NAME).exists())
        self.assertEqual(
            len(list(self.parent.glob(f".{NAME}.removed-*"))), 1)

    def test_keyboard_interrupt_after_old_tree_move_rolls_back(self):
        initial = install(self.archive, self.parent, self.report["sha256"])
        target = self.parent / NAME
        original = os.replace
        interrupted = False

        def interrupt_after_first(source, destination):
            nonlocal interrupted
            result = original(source, destination)
            if (Path(source) == target
                    and Path(destination).name.startswith(f".{NAME}.backup-")
                    and not interrupted):
                interrupted = True
                raise KeyboardInterrupt("synthetic interruption")
            return result

        with patch("install.os.replace", side_effect=interrupt_after_first):
            with self.assertRaises(KeyboardInterrupt):
                install(
                    self.archive, self.parent, self.report["sha256"],
                    expected_current=initial["identity"])
        self.assertEqual(tree_identity(target), initial["identity"])
        self.assertFalse((self.parent / LOCK_NAME).exists())

    def test_parent_traversal_is_refused(self):
        with self.assertRaises(PackageError):
            install(self.archive, self.parent / ".." / "skills", self.report["sha256"])

    def test_parent_symlink_is_refused(self):
        linked = self.base / "linked"
        create_symlink(self, linked, self.parent, target_is_directory=True)
        with self.assertRaises(PackageError):
            install(self.archive, linked, self.report["sha256"])

    def test_invalid_archive_members_enter_the_parser(self):
        cases = (
            (NAME + "/../escape", 0),
            (NAME + "/.git/config", 0),
            ("Other/SKILL.md", 0),
            (NAME + "/SKILL.md", 0o120777),
            (NAME + "/CON", 0),
            (NAME + "/scripts/C:evil", 0),
        )
        for index, (name, mode) in enumerate(cases):
            path = self.base / f"negative-{index}.zip"
            with ZipFile(path, "w") as archive:
                item = ZipInfo(name)
                item.external_attr = mode << 16
                archive.writestr(item, b"synthetic")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.subTest(name=name), self.assertRaises(PackageError):
                archive_files(path, digest)

    def test_missing_skill_entry_rejected_before_target_creation(self):
        bad = self.base / "missing.zip"
        with ZipFile(self.archive) as source, ZipFile(bad, "w") as destination:
            for item in source.infolist():
                if item.filename != NAME + "/SKILL.md":
                    destination.writestr(item, source.read(item.filename))
        digest = hashlib.sha256(bad.read_bytes()).hexdigest()
        with self.assertRaises(PackageError):
            install(bad, self.parent, digest)
        self.assertFalse((self.parent / NAME).exists())
        self.assertFalse((self.parent / LOCK_NAME).exists())

    def test_encrypted_member_is_a_clean_cli_error(self):
        path = self.base / "encrypted.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr(NAME + "/SKILL.md", b"synthetic")
        raw = bytearray(path.read_bytes())
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            start = 0
            while True:
                position = raw.find(signature, start)
                if position < 0:
                    break
                flag_at = position + offset
                flags = int.from_bytes(raw[flag_at:flag_at + 2], "little") | 1
                raw[flag_at:flag_at + 2] = flags.to_bytes(2, "little")
                start = position + 4
        path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "install.py"), "install",
             str(path), "--sha256", digest, "--parent", str(self.parent)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.parent / NAME).exists())


@unittest.skipUnless(os.name == "posix", "恢复入口使用 POSIX 文件租约")
class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.archive = Path(cls.fixture.name) / "source.zip"
        cls.report = build_archive(ROOT, cls.archive)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.parent = self.base / "skills"
        self.parent.mkdir()
        result = install(self.archive, self.parent, self.report["sha256"])
        self.identity = result["identity"]
        self.target = self.parent / NAME

    def create_scene(self):
        self.backup = self.parent / f".{NAME}.backup-synthetic"
        os.rename(self.target, self.backup)
        stage_parent = self.parent / f".{NAME}.stage-synthetic"
        self.stage = stage_parent / NAME
        self.stage.mkdir(parents=True)
        (self.stage / "retained.txt").write_text("retained", encoding="utf-8")
        self.lock = self.parent / LOCK_NAME
        self.lock.mkdir(mode=0o700)
        lease = self.lock / installer.LEASE_NAME
        lease.write_bytes(b"")
        value = installer._record(
            "install", backup=self.backup, stage=self.stage,
            new_identity=self.identity, previous_identity=self.identity)
        raw = installer._write_record(self.lock, value)
        self.record_digest = hashlib.sha256(raw).hexdigest()
        return lease

    def test_recovery_of_previous_file_set_keeps_identity_and_stage(self):
        for name in ("AGENT_INSTALL.md", ".github/dependabot.yml"):
            (self.target / name).unlink()
        (self.target / "references/catalog.md").write_bytes(
            b"# Previous catalogue rendering\n")
        self.identity = tree_identity(self.target)
        self.create_scene()
        info = recovery_info(self.parent)
        self.assertEqual(info["state"], "recovery_pending")
        result = recover(
            self.parent, self.record_digest, backup=self.backup,
            expected_backup=self.identity, acknowledge_stopped_writers=True)
        self.assertEqual(result["identity"], self.identity)
        self.assertEqual(tree_identity(self.target), self.identity)
        self.assertFalse((self.target / "AGENT_INSTALL.md").exists())
        self.assertEqual((self.stage / "retained.txt").read_bytes(), b"retained")
        self.assertFalse(self.lock.exists())

    def test_recovery_restores_backup_and_keeps_staged_content(self):
        self.create_scene()
        info = recovery_info(self.parent)
        self.assertEqual(info["recovery_sha256"], self.record_digest)
        self.assertEqual(
            set(info["operation"]),
            {"format", "operation", "target", "backup", "stage",
             "new_identity", "previous_identity"})
        result = recover(
            self.parent, self.record_digest, backup=self.backup,
            expected_backup=self.identity,
            acknowledge_stopped_writers=True)
        self.assertEqual(result["identity"], self.identity)
        self.assertEqual(validate_installation(self.target), self.identity)
        self.assertEqual((self.stage / "retained.txt").read_text(), "retained")
        self.assertFalse(self.lock.exists())

    def test_recovery_can_finish_after_backup_was_already_restored(self):
        self.create_scene()
        os.rename(self.backup, self.target)
        result = recover(
            self.parent, self.record_digest, already_restored=True,
            expected_current=self.identity,
            acknowledge_stopped_writers=True)
        self.assertEqual(result["status"], "recovered")
        self.assertFalse(self.lock.exists())

    def test_recovery_refuses_wrong_record_or_missing_writer_acknowledgement(self):
        self.create_scene()
        with self.assertRaises(PackageError):
            recover(
                self.parent, "0" * 64, backup=self.backup,
                expected_backup=self.identity,
                acknowledge_stopped_writers=True)
        with self.assertRaises(PackageError):
            recover(
                self.parent, self.record_digest, backup=self.backup,
                expected_backup=self.identity)
        self.assertTrue(self.lock.exists())
        self.assertTrue(self.backup.exists())

    def test_active_lease_blocks_recovery(self):
        lease = self.create_scene()
        import fcntl
        with lease.open("rb") as holder:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(PackageError):
                recover(
                    self.parent, self.record_digest, backup=self.backup,
                    expected_backup=self.identity,
                    acknowledge_stopped_writers=True)
        self.assertTrue(self.lock.exists())

    def test_shared_writer_lease_blocks_recovery(self):
        self.create_scene()
        with writer_lease(self.target), self.assertRaisesRegex(
                PackageError, "写入者"):
            recover(
                self.parent, self.record_digest, backup=self.backup,
                expected_backup=self.identity,
                acknowledge_stopped_writers=True)
        self.assertTrue(self.lock.exists())

    def test_recovery_writer_lease_close_failure_does_not_replace_success(self):
        self.create_scene()
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
            result = recover(
                self.parent, self.record_digest, backup=self.backup,
                expected_backup=self.identity,
                acknowledge_stopped_writers=True)
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(validate_installation(self.target), self.identity)
        self.assertFalse(self.lock.exists())
        upgraded = install(
            self.archive, self.parent, self.report["sha256"],
            expected_current=self.identity)
        self.assertEqual(upgraded["identity"], self.identity)

    def test_recovery_retirement_preserves_replaced_lock(self):
        self.create_scene()
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
            with self.assertRaisesRegex(PackageError, "保留|替换|改变"):
                recover(
                    self.parent, self.record_digest, backup=self.backup,
                    expected_backup=self.identity,
                    acknowledge_stopped_writers=True)
        self.assertEqual(validate_installation(self.target), self.identity)
        self.assertTrue((preserved["displaced"] / RECORD_NAME).is_file())
        self.assertEqual(
            (preserved["retired"] / "unknown-owned-content").read_text(),
            "unknown")

    def test_committed_new_install_cleanup_failure_is_finalizable(self):
        other = self.base / "other-skills"
        other.mkdir()

        def fail_after_close(lock, handle, expected_token):
            handle.close()
            raise OSError("synthetic lock rename failure")

        with patch("install._finish_lock", side_effect=fail_after_close):
            with self.assertRaisesRegex(OSError, "rename"):
                install(self.archive, other, self.report["sha256"])
        info = recovery_info(other)
        self.assertEqual(info["state"], "committed_cleanup_pending")
        result = recover(
            other, info["recovery_sha256"],
            acknowledge_stopped_writers=True, finalize_committed=True)
        self.assertEqual(result["status"], "committed_cleanup_completed")
        self.assertTrue((other / NAME).is_dir())

    def test_committed_upgrade_cleanup_failure_is_finalizable(self):
        def fail_after_close(lock, handle, expected_token):
            handle.close()
            raise OSError("synthetic lock rename failure")

        with patch("install._finish_lock", side_effect=fail_after_close):
            with self.assertRaisesRegex(OSError, "rename"):
                install(
                    self.archive, self.parent, self.report["sha256"],
                    expected_current=self.identity)
        info = recovery_info(self.parent)
        self.assertEqual(info["state"], "committed_cleanup_pending")
        result = recover(
            self.parent, info["recovery_sha256"],
            acknowledge_stopped_writers=True, finalize_committed=True)
        self.assertEqual(result["status"], "committed_cleanup_completed")
        self.assertEqual(validate_installation(self.target), self.identity)

    def test_committed_uninstall_cleanup_failure_is_finalizable(self):
        def fail_after_close(lock, handle, expected_token):
            handle.close()
            raise OSError("synthetic lock rename failure")

        with patch("install._finish_lock", side_effect=fail_after_close):
            with self.assertRaisesRegex(OSError, "rename"):
                uninstall(self.parent, self.identity)
        info = recovery_info(self.parent)
        self.assertEqual(info["state"], "committed_cleanup_pending")
        result = recover(
            self.parent, info["recovery_sha256"],
            acknowledge_stopped_writers=True, finalize_committed=True)
        self.assertEqual(result["status"], "committed_cleanup_completed")
        self.assertFalse(self.target.exists())

    def test_retired_lock_cleanup_is_reported_and_does_not_block_retry(self):
        other = self.base / "pending-cleanup-skills"
        other.mkdir()

        def retain_after_close(lock, handle, expected_token):
            handle.close()
            retired = lock.parent / f"{lock.name}.synthetic-cleanup"
            os.replace(lock, retired)
            return retired

        with patch("install._finish_lock", side_effect=retain_after_close):
            result = install(self.archive, other, self.report["sha256"])
        pending = Path(result["cleanup_pending"])
        self.assertTrue(pending.is_dir())
        self.assertFalse((other / LOCK_NAME).exists())
        upgraded = install(
            self.archive, other, self.report["sha256"],
            expected_current=result["identity"])
        self.assertEqual(
            tree_identity(other / NAME), upgraded["identity"])

    def test_recovery_rejects_a_valid_but_unrecorded_backup_identity(self):
        self.create_scene()
        rights = self.backup / "CONTENT_RIGHTS.md"
        rights.write_bytes(rights.read_bytes() + b"\n")
        changed_identity = tree_identity(self.backup)
        self.assertNotEqual(changed_identity, self.identity)
        with self.assertRaisesRegex(PackageError, "操作记录"):
            recover(
                self.parent, self.record_digest, backup=self.backup,
                expected_backup=changed_identity,
                acknowledge_stopped_writers=True)
        self.assertTrue(self.backup.exists())
        self.assertFalse(self.target.exists())
        self.assertTrue(self.lock.exists())


if __name__ == "__main__":
    unittest.main()
