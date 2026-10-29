"""Direct counterexamples for path, name and transaction boundaries."""
from copy import deepcopy
import errno
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from atlas import CatalogueError, load_catalogue
from validate import validate_project_files, validate_skill
from update import apply, recover, UpdateError
import test_update
from test_contracts import copy_project_fixture, create_symlink


class NameAndPathTests(unittest.TestCase):
    def test_symlink_probe_only_skips_capability_errors(self):
        for error, expected in ((errno.EPERM, unittest.SkipTest),
                                (errno.ENOSPC, OSError)):
            with self.subTest(error=error), patch.object(
                    Path, "symlink_to", side_effect=OSError(error, "synthetic link failure")):
                with self.assertRaises(expected):
                    create_symlink(self, Path("unused-link"), Path("unused-target"))

    def test_container_name_does_not_change_skill_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ("BilibiliMemeAtlas", "MixedCase", "space 中文 path", "nested/source"):
                root = Path(temp) / name
                root.mkdir(parents=True)
                (root / "SKILL.md").write_text('---\nname: bilibili-meme-atlas\ndescription: "合成测试"\n---\n', encoding="utf-8")
                self.assertEqual(validate_skill(root), [])
                (root / "SKILL.md").unlink()
                self.assertTrue(validate_skill(root))

    def test_bad_frontmatter_is_not_accepted_in_source_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("BilibiliMemeAtlas", "x--x", "../escape", "a"*65, "-bad", "bad_underscore"):
                (root / "SKILL.md").write_text(f'---\nname: {name}\ndescription: "合成测试"\n---\n')
                self.assertTrue(validate_skill(root))
            (root / "SKILL.md").write_text('---\nname: bilibili-meme-atlas\nname: bilibili-meme-atlas\ndescription: "测试"\n---\n')
            self.assertTrue(validate_skill(root))

    def test_yaml_scalars_require_quoted_descriptions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for value in ('["x"]','{a: b}','# comment','null','true','123','2026-10-24','!tag value','&anchor text','0xFF','0b101','1.','1.0e+3','2026-10-24T00:00:00Z','- value','A useful skill'):
                (root/'SKILL.md').write_text(f'---\nname: bilibili-meme-atlas\ndescription: {value}\n---\n')
                with self.subTest(value=value):self.assertTrue(validate_skill(root))
            for value in ('"A useful skill"',"'A useful skill'",'"123"','"true"'):
                (root/'SKILL.md').write_text(f'---\nname: bilibili-meme-atlas\ndescription: {value}\n---\n')
                with self.subTest(value=value):self.assertEqual(validate_skill(root),[])

    def test_skill_reference_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/"bilibili-meme-atlas"; root.mkdir()
            outside=Path(temp)/"private.md"; outside.write_text("synthetic")
            body='---\nname: bilibili-meme-atlas\ndescription: "测试"\n---\n'
            for target in ("../private.md", "%2e%2e/private.md", str(outside)):
                (root/"SKILL.md").write_bytes((body+f'[link]({target})\n').encode("utf-8"))
                self.assertTrue(validate_skill(root))
            (root/"inside.md").write_text("safe")
            (root/"SKILL.md").write_bytes((body+'[link](inside.md)\n').encode("utf-8"))
            self.assertEqual(validate_skill(root), [])

    def test_skill_reference_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "inside.md").write_bytes(b"safe")
            create_symlink(self, root / "link.md", root / "inside.md")
            (root / "SKILL.md").write_bytes((
                '---\nname: bilibili-meme-atlas\ndescription: "测试"\n---\n'
                '[link](link.md)\n').encode("utf-8"))
            self.assertTrue(validate_skill(root))

    def test_project_boundary_rejects_unexpected_nested_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"bilibili-meme-atlas"
            copy_project_fixture(root)
            self.assertEqual(validate_project_files(root), [])
            (root/"scripts/unknown.py").write_text("print('synthetic')")
            self.assertTrue(any("未登记文件" in item for item in validate_project_files(root)))

    def test_project_boundary_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"bilibili-meme-atlas"
            copy_project_fixture(root)
            link=root/"references/link.json"
            create_symlink(self, link, root/"references/metadata.json")
            self.assertTrue(any("符号链接" in item for item in validate_project_files(root)))


class TransactionHardeningTests(unittest.TestCase):
    setUp = test_update.UpdateTests.setUp
    save = test_update.UpdateTests.save
    batch = test_update.UpdateTests.batch
    sample = test_update.UpdateTests.sample
    snapshot = test_update.UpdateTests.snapshot

    def test_partial_write_failure_restores_exact_bytes_and_retry_works(self):
        before=self.snapshot()
        def failed(path,text):
            path.write_bytes(b'partial failed write')
            raise OSError("synthetic write error")
        with patch("update.atomic_write",side_effect=failed), self.assertRaises(OSError): apply(self.root,self.sample())
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(apply(self.root,self.sample())["added_entries"],["new-item"])

    def test_persistent_rollback_failure_keeps_journal_and_recovery(self):
        from update import atomic_write
        before=self.snapshot();counter=0
        def fail_second(path,text):
            nonlocal counter
            counter+=1
            if counter==2: raise OSError("disk write unavailable")
            return atomic_write(path,text)
        with patch("update.atomic_write",side_effect=fail_second), patch("update.restore_bytes",side_effect=OSError("disk still unavailable")):
            with self.assertRaisesRegex(UpdateError,"回滚未完成"):apply(self.root,self.sample())
        self.assertTrue((self.root/".atlas-update.lock/journal.json").is_file())
        with self.assertRaises(UpdateError):recover(self.root)
        self.assertEqual(recover(self.root,writers_stopped=True)["status"],"recovered")
        self.assertEqual(self.snapshot(),before)

    def test_malformed_source_id_and_aliases_rejected(self):
        for field,value in (("source_id",[]),("source_id",{}),("aliases",None)):
            batch=self.sample()
            if field=="aliases":batch["entries"][0]["record"][field]=value
            else:batch["entries"][0]["record"]["evidence"][0][field]=value
            before=self.snapshot()
            with self.subTest(field=field,value=value),self.assertRaises(UpdateError):apply(self.root,batch)
            self.assertEqual(self.snapshot(),before)

    def test_cross_shard_relation_is_preserved(self):
        batch=self.sample();batch["entries"][0]["record"].update(era="2009-2015",relations=["original"])
        apply(self.root,batch)
        self.assertEqual(next(e for e in load_catalogue(self.root)[0] if e["id"]=="new-item")["relations"],["original"])

    def test_conflicting_proposal_and_identical_retry_do_not_overwrite(self):
        first=self.sample();second=deepcopy(first);second["entries"][0]["record"]["meaning"]="不同提案"
        apply(self.root,first);before=self.snapshot()
        for batch in (first,second):
            with self.assertRaisesRegex(UpdateError,"基线"):apply(self.root,batch)
            self.assertEqual(self.snapshot(),before)

    def test_reference_symlink_rejected_without_external_writes(self):
        path = self.root / "references/2021-2026.json"
        outside = self.root.parent / "outside.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        create_symlink(self, path, outside)
        before=outside.read_bytes()
        with self.assertRaises((UpdateError,CatalogueError)):apply(self.root,self.batch())
        self.assertEqual(outside.read_bytes(),before)

    def test_no_update_has_no_file_or_directory_metadata_changes(self):
        before={str(p): (p.read_bytes() if p.is_file() else None,p.stat().st_mtime_ns) for p in self.root.rglob('*')}
        stamp=self.root.stat().st_mtime_ns
        self.assertEqual(apply(self.root,self.batch())["status"],"no_update")
        self.assertEqual(stamp,self.root.stat().st_mtime_ns)
        self.assertEqual(before,{str(p): (p.read_bytes() if p.is_file() else None,p.stat().st_mtime_ns) for p in self.root.rglob('*')})


if __name__ == "__main__": unittest.main()
