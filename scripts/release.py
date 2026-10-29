#!/usr/bin/env python3
"""Describe and verify provider-neutral local release bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

from package import PackageError, source_files, verify_archive, version_of

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "bilibili-meme-atlas-release/v1"
SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
REVISION = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")


class ReleaseError(ValueError):
    pass


def _reject_duplicate_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseError(f"JSON 含重复字段: {key}")
        value[key] = item
    return value


def read_json(path):
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_fields)
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError(f"无法读取 JSON: {exc}") from exc


def _digest(value):
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def _positive(value):
    return type(value) is int and value > 0


def snapshot_identity(files: dict[str, bytes]) -> dict:
    inventory = [
        {"path": name, "bytes": len(body),
         "sha256": hashlib.sha256(body).hexdigest()}
        for name, body in sorted(files.items())
    ]
    encoded = json.dumps(
        inventory, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    return {
        "files": len(inventory),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def archive_filename(version: str) -> str:
    return f"bilibili-meme-atlas-v{version}.zip"


def checksums_filename(version: str) -> str:
    return f"SHA256SUMS-v{version}.txt"


def expected_checksums(manifest: dict) -> bytes:
    package = manifest["package"]
    if package["kind"] != "skill_zip":
        raise ReleaseError("纯源码发行没有归档校验和文件")
    return f"{package['sha256']}  {package['name']}\n".encode("ascii")


def _read_notes(path: Path, version: str) -> tuple[bytes, str]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("发行说明必须是普通文件")
    body = path.read_bytes()
    try:
        text = body.decode("utf-8")
    except UnicodeError as exc:
        raise ReleaseError("发行说明必须使用 UTF-8") from exc
    first = text.splitlines(keepends=True)[:1]
    prefix = f"# v{version} — "
    if not first or not first[0].endswith("\n") or not first[0].startswith(prefix):
        raise ReleaseError("发行说明首行须为对应版本标题并以 LF 结束")
    title = first[0][2:-1]
    if len(title) > 120 or any(ord(char) < 32 for char in title):
        raise ReleaseError("发行标题无效")
    return body, title


def _asset_paths(version: str, directory) -> tuple[Path, Path]:
    directory = Path(directory)
    expected = {archive_filename(version), checksums_filename(version)}
    if (directory.is_symlink() or not directory.is_dir()
            or {path.name for path in directory.iterdir()} != expected):
        raise ReleaseError("资产目录须恰好包含版本归档与校验和文件")
    archive = directory / archive_filename(version)
    checksums = directory / checksums_filename(version)
    if (archive.is_symlink() or checksums.is_symlink()
            or not archive.is_file() or not checksums.is_file()):
        raise ReleaseError("发行资产必须是普通文件")
    return archive, checksums


def describe(source_root, notes, assets_dir=None) -> dict:
    """Build a manifest from validated local source, notes and optional ZIP bytes."""
    files = source_files(Path(source_root))
    version = version_of(files)
    note_bytes, title = _read_notes(Path(notes), version)
    manifest = {
        "format": FORMAT,
        "version": version,
        "title": title,
        "source": snapshot_identity(files),
        "notes": {
            "bytes": len(note_bytes),
            "sha256": hashlib.sha256(note_bytes).hexdigest(),
        },
        "package": {"kind": "source_only"},
    }
    if assets_dir is not None:
        archive, checksums = _asset_paths(version, assets_dir)
        report = verify_archive(archive, files)
        manifest["package"] = {
            "kind": "skill_zip",
            "name": archive.name,
            "files": report["files"],
            "bytes": report["bytes"],
            "sha256": report["sha256"],
        }
        if checksums.read_bytes() != expected_checksums(manifest):
            raise ReleaseError("校验和文件与归档不一致")
    return manifest


def validate_manifest(value) -> dict:
    if (not isinstance(value, dict)
            or set(value) != {"format", "version", "title", "source", "notes", "package"}
            or value["format"] != FORMAT):
        raise ReleaseError("发行清单字段或格式无效")
    version = value["version"]
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise ReleaseError("发行版本必须为三段数字版本")
    title = value["title"]
    if (not isinstance(title, str) or not title.startswith(f"v{version} — ")
            or len(title) > 120 or any(ord(char) < 32 for char in title)):
        raise ReleaseError("发行标题无效")
    source = value["source"]
    if (not isinstance(source, dict) or set(source) != {"files", "sha256"}
            or not _positive(source["files"]) or not _digest(source["sha256"])):
        raise ReleaseError("源码身份无效")
    notes = value["notes"]
    if (not isinstance(notes, dict) or set(notes) != {"bytes", "sha256"}
            or not _positive(notes["bytes"]) or not _digest(notes["sha256"])):
        raise ReleaseError("发行说明身份无效")
    package = value["package"]
    if not isinstance(package, dict):
        raise ReleaseError("归档合同无效")
    if package.get("kind") == "source_only":
        if set(package) != {"kind"}:
            raise ReleaseError("纯源码发行含未定义字段")
    elif package.get("kind") == "skill_zip":
        if (set(package) != {"kind", "name", "files", "bytes", "sha256"}
                or package["name"] != archive_filename(version)
                or not _positive(package["files"])
                or not _positive(package["bytes"])
                or not _digest(package["sha256"])):
            raise ReleaseError("归档身份无效")
    else:
        raise ReleaseError("不支持的发行资产类型")
    return value


def _checked_local(manifest, source_root, notes, assets_dir=None):
    """Return the exact note and asset bytes that passed the fixed manifest."""
    manifest = validate_manifest(manifest)
    files = source_files(Path(source_root))
    if version_of(files) != manifest["version"]:
        raise ReleaseError("源码版本与发行清单不一致")
    if snapshot_identity(files) != manifest["source"]:
        raise ReleaseError("源码快照身份不一致")
    note_bytes, title = _read_notes(Path(notes), manifest["version"])
    if (title != manifest["title"]
            or len(note_bytes) != manifest["notes"]["bytes"]
            or hashlib.sha256(note_bytes).hexdigest() != manifest["notes"]["sha256"]):
        raise ReleaseError("发行说明字节不一致")
    package = manifest["package"]
    if package["kind"] == "source_only":
        if assets_dir is not None and any(Path(assets_dir).iterdir()):
            raise ReleaseError("纯源码发行不得包含自定义资产")
        return note_bytes, {}
    if assets_dir is None:
        raise ReleaseError("归档发行须提供资产目录")
    archive, checksums = _asset_paths(manifest["version"], assets_dir)
    archive_bytes = archive.read_bytes()
    checksum_bytes = checksums.read_bytes()
    if (len(archive_bytes) != package["bytes"]
            or hashlib.sha256(archive_bytes).hexdigest() != package["sha256"]
            or checksum_bytes != expected_checksums(manifest)):
        raise ReleaseError("发行资产字节不一致")
    report = verify_archive(archive, files)
    if any(report[key] != package[key] for key in ("files", "bytes", "sha256")):
        raise ReleaseError("归档内容与发行清单不一致")
    return note_bytes, {archive.name: archive_bytes, checksums.name: checksum_bytes}


def check_local(manifest, source_root, notes, assets_dir=None) -> dict[str, bytes]:
    """Verify the complete local release object against a fixed manifest."""
    return _checked_local(manifest, source_root, notes, assets_dir)[1]


def prepare_request(manifest, source_root, notes, assets_dir=None) -> dict:
    """Return a provider-neutral release object after all local bytes match."""
    note_bytes, bodies = _checked_local(manifest, source_root, notes, assets_dir)
    manifest = validate_manifest(manifest)
    return {
        "format": "bilibili-meme-atlas-release-request/v1",
        "tag": "v" + manifest["version"],
        "name": manifest["title"],
        "body": note_bytes.decode("utf-8"),
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": name, "bytes": len(body),
             "sha256": hashlib.sha256(body).hexdigest()}
            for name, body in sorted(bodies.items())
        ],
    }


def _release_target(repository, source_revision) -> tuple[str, str]:
    if (not isinstance(repository, str) or not REPOSITORY.fullmatch(repository)
            or any(part in {".", ".."} for part in repository.split("/"))):
        raise ReleaseError("发行仓库须为 owner/name")
    if (not isinstance(source_revision, str)
            or not REVISION.fullmatch(source_revision)):
        raise ReleaseError("源码修订须为完整提交标识")
    return repository, source_revision


def prepare_installable_request(manifest, source_root, notes, repository,
                                source_revision, assets_dir) -> dict:
    """Bind an installable release request to one repository and source revision."""
    manifest = validate_manifest(manifest)
    if manifest["package"]["kind"] != "skill_zip":
        raise ReleaseError("可安装发行必须包含版本归档与校验和文件")
    repository, source_revision = _release_target(repository, source_revision)
    request = prepare_request(manifest, source_root, notes, assets_dir)
    return {
        **request,
        "format": "bilibili-meme-atlas-installable-release-request/v1",
        "repository": repository,
        "source_revision": source_revision,
    }


def verify_observation(manifest, source_root, notes, observation,
                       assets_dir=None) -> dict:
    """Compare already-obtained metadata and bytes; perform no remote action."""
    note_bytes, bodies = _checked_local(manifest, source_root, notes, assets_dir)
    manifest = validate_manifest(manifest)
    if (not isinstance(observation, dict)
            or set(observation) != {"tag", "name", "body", "draft", "prerelease", "assets"}
            or observation["tag"] != "v" + manifest["version"]
            or observation["name"] != manifest["title"]
            or not isinstance(observation["body"], str)
            or observation["body"].encode("utf-8") != note_bytes
            or observation["draft"] is not False
            or observation["prerelease"] is not False
            or not isinstance(observation["assets"], list)):
        raise ReleaseError("发行元数据与本地合同不一致")
    assets = {}
    for item in observation["assets"]:
        if (not isinstance(item, dict)
                or set(item) != {"name", "size", "digest"}
                or not isinstance(item["name"], str)
                or type(item["size"]) is not int or item["size"] < 0
                or not isinstance(item["digest"], str)
                or item["name"] in assets):
            raise ReleaseError("发行资产须提供唯一名称、字节数和可信摘要")
        assets[item["name"]] = item
    if set(assets) != set(bodies):
        raise ReleaseError("发行资产列表不一致")
    for name, body in bodies.items():
        item = assets[name]
        if item["size"] != len(body):
            raise ReleaseError("发行资产长度不一致")
        if item["digest"] != "sha256:" + hashlib.sha256(body).hexdigest():
            raise ReleaseError("发行资产摘要不一致")
    return {
        "result": "MATCH",
        "tag": observation["tag"],
        "assets": [assets[name] for name in sorted(assets)],
    }


def verify_installable_observation(manifest, source_root, notes, observation,
                                   repository, source_revision,
                                   assets_dir) -> dict:
    """Verify the release target, resolved tag commit, and installable assets."""
    manifest = validate_manifest(manifest)
    if manifest["package"]["kind"] != "skill_zip":
        raise ReleaseError("纯源码发行不满足安装前置条件")
    repository, source_revision = _release_target(repository, source_revision)
    required = {"tag", "name", "body", "draft", "prerelease", "assets",
                "repository", "tag_target"}
    if (not isinstance(observation, dict) or set(observation) != required
            or observation["repository"] != repository
            or observation["tag_target"] != source_revision):
        raise ReleaseError("发行仓库或标签解析的源码修订不一致")
    base = {key: observation[key] for key in required - {"repository", "tag_target"}}
    result = verify_observation(
        manifest, source_root, notes, base, assets_dir)
    return {
        **result,
        "repository": repository,
        "source_revision": source_revision,
        "installable": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    describe_command = commands.add_parser("describe", help="从本地字节生成发行清单")
    describe_command.add_argument("--source-root", type=Path, default=ROOT)
    describe_command.add_argument("--notes", type=Path, required=True)
    describe_command.add_argument("--assets-dir", type=Path)
    lint = commands.add_parser("lint", help="检查发行清单结构")
    lint.add_argument("--manifest", type=Path, required=True)
    for name, help_text in (
        ("check-source", "核对源码、说明和可选资产"),
        ("prepare-request", "生成与本地字节绑定的中立发行对象"),
        ("verify-release", "核对已取得的发行元数据与资产字节"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--source-root", type=Path, default=ROOT)
        command.add_argument("--notes", type=Path, required=True)
        command.add_argument("--assets-dir", type=Path)
        if name == "verify-release":
            command.add_argument("--metadata", type=Path, required=True)
    for name, help_text in (
        ("prepare-installable-request", "生成绑定仓库与源码修订的可安装发行对象"),
        ("verify-installable-release", "核对仓库、标签目标与可安装资产"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--source-root", type=Path, default=ROOT)
        command.add_argument("--notes", type=Path, required=True)
        command.add_argument("--assets-dir", type=Path, required=True)
        command.add_argument("--repository", required=True)
        command.add_argument("--source-revision", required=True)
        if name == "verify-installable-release":
            command.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "describe":
            result = describe(args.source_root, args.notes, args.assets_dir)
        elif args.action == "lint":
            result = {"result": "PASS", "manifest": validate_manifest(read_json(args.manifest))}
        else:
            manifest = read_json(args.manifest)
            if args.action == "check-source":
                assets = check_local(
                    manifest, args.source_root, args.notes, args.assets_dir)
                result = {"result": "PASS", "assets": sorted(assets)}
            elif args.action == "prepare-request":
                result = prepare_request(
                    manifest, args.source_root, args.notes, args.assets_dir)
            elif args.action == "verify-release":
                result = verify_observation(
                    manifest, args.source_root, args.notes,
                    read_json(args.metadata), args.assets_dir)
            elif args.action == "prepare-installable-request":
                result = prepare_installable_request(
                    manifest, args.source_root, args.notes,
                    args.repository, args.source_revision, args.assets_dir)
            else:
                result = verify_installable_observation(
                    manifest, args.source_root, args.notes,
                    read_json(args.metadata), args.repository,
                    args.source_revision, args.assets_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ReleaseError, PackageError, OSError, ValueError, TypeError, UnicodeError) as exc:
        print(f"发行字节校验失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
