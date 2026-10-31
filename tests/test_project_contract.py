"""Self-contained Skill, corpus, interface, and content-boundary checks."""
from __future__ import annotations

from pathlib import Path
import json
import re
import shutil
import stat
import struct
import sys
import tempfile
import unittest
from zipfile import ZipFile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from atlas import load_catalogue
from build import projections
from package import build_archive, source_files
from validate import (PROJECT_FILES, allowed_project_path, expected_rights_registry,
                      validate_interface, validate_project_files,
                      validate_rights_registry, validate_schema_contracts,
                      validate_skill)
from test_contracts import copy_project_fixture

AUTHORED_INTERFACE = (
    'interface:\n'
    '  display_name: "B站热梗知识库"\n'
    '  short_description: "检索B站历史热梗与弹幕表达，核对出处、传播和适用语境"\n'
    '  default_prompt: "使用 $bilibili-meme-atlas 解释这些B站热梗的含义、出处和合适用法，并给出可核验来源。"\n'
    '  icon_small: "./assets/icon.png"\n'
    '  icon_large: "./assets/icon.png"\n'
    'policy:\n'
    '  allow_implicit_invocation: true\n')


def png_rgba_pixels(path):
    body = Path(path).read_bytes()
    if not body.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not PNG")
    offset = 8
    chunks = []
    image_data = []
    while offset < len(body):
        length = struct.unpack(">I", body[offset:offset + 4])[0]
        name = body[offset + 4:offset + 8]
        data = body[offset + 8:offset + 8 + length]
        chunks.append((name, data))
        if name == b"IDAT":
            image_data.append(data)
        offset += length + 12
    header = next(data for name, data in chunks if name == b"IHDR")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", header)
    if (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0):
        raise ValueError("not non-interlaced RGBA8")
    raw = zlib.decompress(b"".join(image_data))
    stride = width * 4
    previous = bytearray(stride)
    pixels = []

    def paeth(a, b, c):
        estimate = a + b - c
        distances = (abs(estimate - a), abs(estimate - b),
                     abs(estimate - c))
        return (a, b, c)[distances.index(min(distances))]

    cursor = 0
    for _row in range(height):
        method = raw[cursor]
        encoded = raw[cursor + 1:cursor + 1 + stride]
        cursor += stride + 1
        decoded = bytearray(stride)
        for index, value in enumerate(encoded):
            left = decoded[index - 4] if index >= 4 else 0
            above = previous[index]
            upper_left = previous[index - 4] if index >= 4 else 0
            if method == 0:
                predictor = 0
            elif method == 1:
                predictor = left
            elif method == 2:
                predictor = above
            elif method == 3:
                predictor = (left + above) // 2
            elif method == 4:
                predictor = paeth(left, above, upper_left)
            else:
                raise ValueError("unknown PNG filter")
            decoded[index] = (value + predictor) & 0xff
        pixels.extend(tuple(decoded[i:i + 4])
                      for i in range(0, stride, 4))
        previous = decoded
    return width, height, pixels


class ProjectContractTests(unittest.TestCase):
    def test_source_requirements_are_not_relaxed_for_older_software_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bilibili-meme-atlas"
            copy_project_fixture(root)
            names = ("AGENT_INSTALL.md", ".github/dependabot.yml")
            for name in names:
                (root / name).unlink()
            path = root / "references/metadata.json"
            metadata = json.loads(path.read_bytes())
            for version in ("0.1.0", "0.1.1", "0.2.0"):
                metadata["version"] = version
                path.write_bytes((json.dumps(
                    metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
                errors = validate_project_files(root)
                self.assertTrue(any("AGENT_INSTALL.md" in error for error in errors))
                self.assertTrue(any("dependabot.yml" in error for error in errors))
            for name in names:
                (root / name).write_bytes((ROOT / name).read_bytes())
            (root / "LICENSE").unlink()
            self.assertTrue(any("LICENSE" in error for error in validate_project_files(root)))
            (root / "scripts/unregistered.py").write_bytes(b"pass\n")
            self.assertTrue(any("未登记文件" in error for error in validate_project_files(root)))

    def test_complete_skill_is_exactly_whitelisted(self):
        actual = set(source_files(ROOT))
        self.assertEqual(validate_project_files(ROOT), [])
        self.assertTrue(PROJECT_FILES <= actual)
        self.assertTrue(all(allowed_project_path(path) for path in actual))
        self.assertTrue(all(re.fullmatch(r"references/[0-9]{4}-[0-9]{4}\.json", path)
                            for path in actual - PROJECT_FILES))
        self.assertTrue(all(stat.S_ISREG((ROOT / path).stat().st_mode) for path in actual))

    def test_root_git_management_entry_is_excluded_from_product_bytes(self):
        for shape in ("clone", "worktree"):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "bilibili-meme-atlas"
                copy_project_fixture(root)
                if shape == "clone":
                    git = root / ".git"
                    git.mkdir()
                    (git / "HEAD").write_text(
                        "ref: refs/heads/main\n", encoding="utf-8")
                else:
                    (root / ".git").write_text(
                        "gitdir: ../.git/worktrees/bilibili-meme-atlas\n",
                        encoding="utf-8")
                self.assertEqual(validate_project_files(root), [])
                files = source_files(root)
                self.assertFalse(any(
                    name == ".git" or name.startswith(".git/")
                    for name in files))
                archive = Path(temp) / (shape + ".zip")
                build_archive(root, archive)
                with ZipFile(archive) as zipped:
                    self.assertFalse(any(
                        name.removeprefix("bilibili-meme-atlas/") == ".git"
                        or name.removeprefix(
                            "bilibili-meme-atlas/").startswith(".git/")
                        for name in zipped.namelist()))

    def test_nested_or_malformed_git_entry_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            nested = Path(temp) / "nested"
            copy_project_fixture(nested)
            (nested / "docs" / ".git").mkdir()
            (nested / "docs" / ".git" / "HEAD").write_text(
                "synthetic\n", encoding="utf-8")
            self.assertTrue(validate_project_files(nested))
            with self.assertRaises(ValueError):
                source_files(nested)

            malformed = Path(temp) / "malformed"
            copy_project_fixture(malformed)
            (malformed / ".git").write_text(
                "not a worktree management file\n", encoding="utf-8")
            self.assertTrue(validate_project_files(malformed))
            with self.assertRaises(ValueError):
                source_files(malformed)

    def test_skill_and_interface_validate_together(self):
        self.assertEqual(validate_skill(ROOT), [])
        self.assertEqual(validate_interface(ROOT), [])
        self.assertEqual(validate_schema_contracts(ROOT), [])

    def test_published_schemas_cannot_be_malformed_or_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bilibili-meme-atlas"
            copy_project_fixture(root)
            entry_schema = root / "references" / "schema.json"
            entry_schema.write_text("not json\n", encoding="utf-8")
            self.assertTrue(validate_schema_contracts(root))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bilibili-meme-atlas"
            copy_project_fixture(root)
            source_schema = root / "references" / "sources.schema.json"
            value = json.loads(source_schema.read_text(encoding="utf-8"))
            value["items"]["properties"]["access_level"]["enum"] = ["full"]
            source_schema.write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            self.assertTrue(validate_schema_contracts(root))

    def test_authored_interface_is_canonical(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bilibili-meme-atlas"
            shutil.copytree(ROOT / "agents", root / "agents")
            shutil.copytree(ROOT / "assets", root / "assets")
            path = root / "agents" / "openai.yaml"
            self.assertEqual(path.read_text(encoding="utf-8"), AUTHORED_INTERFACE)
            self.assertEqual(validate_interface(root), [])

    def test_interface_rejects_schema_path_and_semantic_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bilibili-meme-atlas"
            shutil.copytree(ROOT / "agents", root / "agents")
            shutil.copytree(ROOT / "assets", root / "assets")
            path = root / "agents" / "openai.yaml"
            original = AUTHORED_INTERFACE
            mutations = {
                "missing interface field": original.replace('  icon_large: "./assets/icon.png"\n', ''),
                "extra interface field": original.replace('policy:\n', '  theme: "dark"\npolicy:\n'),
                "external icon": original.replace('  icon_small: "./assets/icon.png"', '  icon_small: "https://example.invalid/icon.png"'),
                "path escape": original.replace('  icon_small: "./assets/icon.png"', '  icon_small: "../assets/icon.png"'),
                "different in-tree icon": original.replace('  icon_small: "./assets/icon.png"', '  icon_small: "./assets/cover.webp"'),
                "plain string": original.replace('  display_name: "B站热梗知识库"', '  display_name: B站热梗知识库'),
                "noncanonical icon path": original.replace('  icon_small: "./assets/icon.png"', '  icon_small: "assets/icon.png"'),
                "extra policy field": original + '  undefined_option: false\n',
                "implicit invocation disabled": original.replace('allow_implicit_invocation: true', 'allow_implicit_invocation: false'),
                "display name drift": original.replace('B站热梗知识库', 'B站热梗百科'),
                "description drift": original.replace('适用语境', '当前热度'),
                "prompt drift": original.replace('可核验来源', '相关线索'),
            }
            for label, text in mutations.items():
                path.write_text(text, encoding="utf-8")
                with self.subTest(label=label):
                    self.assertTrue(validate_interface(root))

    def test_corpus_summary_and_every_source_has_a_consumer(self):
        entries, sources, metadata = load_catalogue(ROOT)
        summary = json.loads((ROOT / "references" / "corpus-summary.json").read_text(encoding="utf-8"))
        statistics = summary["statistics"]
        self.assertEqual(statistics["entries"], len(entries))
        self.assertEqual(statistics["sources"], len(sources))
        self.assertEqual(statistics["version"], metadata["version"])
        self.assertEqual(statistics["knowledge_cutoff"], metadata["knowledge_cutoff"])
        self.assertEqual(set(summary["source_files"]), {
            path.name for path in (ROOT / "references").glob("[0-9]*-[0-9]*.json")
        } | {"sources.json", "metadata.json"})
        statuses = {row["evidence_status"] for row in entries}
        self.assertTrue({"verified", "partial"}.issubset(statuses))
        self.assertLessEqual(statuses, {"verified", "partial", "candidate"})
        used = {ref["source_id"] for row in entries for ref in row["evidence"]}
        self.assertEqual(used, set(sources))

    def test_recent_ai_subject_entries_and_platform_sources_are_available(self):
        entries, sources, _ = load_catalogue(ROOT)
        by_id = {row["id"]: row for row in entries}
        self.assertIn("AI时代", by_id["recent-human-vibe"]["origin"]["summary"])
        self.assertIn("机器生成", by_id["recent-real-person"]["meaning"])
        self.assertTrue(any("造梗机器" in source["title"] for source in sources.values()))
        self.assertTrue(any("bilibili.com" in source["url"] for source in sources.values()))

    def test_user_facing_docs_do_not_embed_release_sha256_values(self):
        concrete = re.compile(r"(?<![A-Za-z0-9])[a-f0-9]{64}(?![A-Za-z0-9])")
        paths = [ROOT / name for name in (
            "README.md", "SKILL.md", "AGENT_INSTALL.md", "CONTENT_RIGHTS.md", "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md", "SECURITY.md",
        )]
        paths.extend(sorted((ROOT / "docs").glob("*.md")))
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertIsNone(concrete.search(path.read_text(encoding="utf-8")))

    def test_rights_registry_matches_assets_and_sources(self):
        self.assertEqual(validate_rights_registry(ROOT), [])
        observed = json.loads(
            (ROOT / "references" / "rights.json").read_text(encoding="utf-8"))
        self.assertEqual(observed, expected_rights_registry(ROOT))
        self.assertEqual(len(observed["sources"]),
                         len(json.loads((ROOT / "references" / "sources.json").read_text(encoding="utf-8"))))
        self.assertEqual({row["path"] for row in observed["assets"]}, {
            "assets/cover.webp", "assets/icon.png",
        })

    def test_skill_frontmatter_and_runtime_contract(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        frontmatter, body = text.split("---\n", 2)[1:]
        fields = dict(line.split(": ", 1) for line in frontmatter.splitlines())
        self.assertEqual(set(fields), {"name", "description"})
        self.assertEqual(fields["name"], "bilibili-meme-atlas")
        self.assertTrue(1 <= len(json.loads(fields["description"])) <= 1024)
        self.assertIn("Python 3.10+", body)

    def test_readme_brand_assets_and_local_links_resolve(self):
        required_assets = {"cover.webp", "icon.png"}
        badges = {
            "https://img.shields.io/badge/Python-3.10%2B-blue.svg",
            "https://img.shields.io/badge/License-MIT-yellow.svg",
        }
        for relative in ("README.md", "docs/README.en.md"):
            path = ROOT / relative
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertTrue(all(name in text for name in required_assets))
                self.assertTrue(all(url in text for url in badges))
            candidates = re.findall(
                r"(?<!!)\[[^]]*\]\(([^)]+)\)|<img[^>]+src=\"([^\"]+)\"",
                text)
            for link, image in candidates:
                target = (link or image).split("#", 1)[0]
                if not target or target.startswith(("https://", "http://", "mailto:")):
                    continue
                with self.subTest(path=relative, target=target):
                    self.assertTrue((path.parent / target).resolve().is_file())
        english = (ROOT / "docs" / "README.en.md").read_text(encoding="utf-8")
        for label in ("Catalogue (Chinese)", "Maintenance (Chinese)",
                      "Verification (Chinese)", "Dependencies (Chinese)"):
            self.assertIn(label, english)
        width, height, pixels = png_rgba_pixels(ROOT / "assets" / "icon.png")
        self.assertEqual((width, height), (512, 512))
        self.assertIn((223, 223, 223, 255), pixels)
        self.assertIn((155, 155, 155, 255), pixels)
        self.assertIn((46, 201, 176, 255), pixels)
        self.assertTrue(any(pixel[3] == 0 for pixel in pixels))
        self.assertTrue(any(pixel[3] == 255 for pixel in pixels))
        rights = (ROOT / "CONTENT_RIGHTS.md").read_text(encoding="utf-8")
        for name in required_assets:
            self.assertIn(f"assets/{name}", rights)

    def test_pr_and_manual_ci_are_read_only_hosted_verification(self):
        workflows = ROOT / ".github" / "workflows"
        self.assertEqual(
            {path.name for path in workflows.iterdir()},
            {"verify.yml", "local-lifecycle-verify.yml", "release-byte-verify.yml"})
        forbidden = (
            "self-hosted", "contents: write", "secrets.", "actions/upload",
            "git push", "gh release", "curl ", "wget ", "pull_request_target:",
        )
        for path in workflows.iterdir():
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIn("workflow_dispatch:", text)
                self.assertIn('  pull_request:\n    branches: ["main"]\n', text)
                self.assertIn("  contents: read", text)
                if path.name == "verify.yml":
                    self.assertIn("runs-on: ${{ matrix.os }}", text)
                    self.assertIn('os: ["ubuntu-24.04", "windows-2022"]', text)
                    self.assertIn('PYTHONUTF8: "1"', text)
                else:
                    self.assertIn("runs-on: ubuntu-24.04", text)
                self.assertIn('python: ["3.10", "3.13"]', text)
                actions = re.findall(
                    r"^[ \t]*(?:-[ \t]+)?uses:[ \t]*(\S+)(?:[ \t]+#.*)?[ \t]*$", text, re.MULTILINE)
                self.assertEqual(len(actions), 2)
                self.assertEqual(
                    {action.split("@", 1)[0] for action in actions},
                    {"actions/checkout", "actions/setup-python"})
                for action in actions:
                    self.assertRegex(
                        action, r"^actions/(?:checkout|setup-python)@[0-9a-f]{40}\Z")
                self.assertIn("python-version: ${{ matrix.python }}", text)
                self.assertIn("run: python -VV", text)
                self.assertIn("persist-credentials: false", text)
                self.assertFalse(any(fragment in text for fragment in forbidden))

    def test_cache_directories_are_rejected_not_packaged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bilibili-meme-atlas"
            copy_project_fixture(root)
            cache = root / "scripts" / "__pycache__"
            cache.mkdir()
            (cache / "atlas.pyc").write_bytes(b"synthetic")
            errors = validate_project_files(root)
            self.assertTrue(any("缓存目录" in error for error in errors))

    def test_future_shard_is_part_of_the_valid_project_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bilibili-meme-atlas"
            copy_project_fixture(root)
            shard = root / "references" / "2027-2031.json"
            shard.write_text("[]\n", encoding="utf-8")
            self.assertEqual(validate_project_files(root), [])
            for name, body in projections(root).items():
                (root / name).write_bytes(body.encode("utf-8"))
            self.assertIn("references/2027-2031.json", source_files(root))


if __name__ == "__main__":
    unittest.main()
