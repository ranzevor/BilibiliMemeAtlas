#!/usr/bin/env python3
"""Build or verify a deterministic local archive of this Skill."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from zipfile import BadZipFile, ZIP_STORED, ZipFile, ZipInfo

sys.dont_write_bytecode = True

from validate import (MAX_PROJECT_FILE_BYTES, allowed_project_path,
                      project_paths, root_git_token, validate_all)

ROOT = Path(__file__).resolve().parents[1]
NAME = "bilibili-meme-atlas"
TIMESTAMP = (1980, 1, 1, 0, 0, 0)
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024


class PackageError(ValueError):
    pass


def archive_name(relative: str) -> str:
    """Return the portable archive member name for a validated relative path."""
    if not isinstance(relative, str):
        raise PackageError("归档路径必须是字符串")
    path = PurePosixPath(relative)
    parts = relative.split("/")
    if (not relative or path.is_absolute() or "\\" in relative
            or any(ord(char) < 32 for char in relative)
            or any(part in ("", ".", "..") for part in parts)
            or any(char in relative for char in ':<>"|?*')
            or any(part.endswith((" ", ".")) for part in parts)
            or any(re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?",
                                part, re.IGNORECASE) for part in parts)):
        raise PackageError(f"归档路径非法: {relative}")
    return f"{NAME}/{relative}"


def _file_identity(path: Path):
    value = path.stat(follow_symlinks=False)
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns)


def _scan_project(root: Path):
    """Return one lstat inventory without following project links."""
    inventory = {}
    try:
        paths = list(project_paths(root))
    except OSError as exc:
        raise PackageError(f"无法枚举项目目录: {exc}") from exc
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            inventory[relative] = _file_identity(path)
        except OSError as exc:
            raise PackageError(f"无法读取项目路径属性: {relative}") from exc
    return inventory


def _capture_project(root: Path):
    """Capture one stable tree inventory and its exact regular-file bytes."""
    root = Path(root)
    try:
        root_before = _file_identity(root)
    except OSError as exc:
        raise PackageError(f"无法读取项目根目录: {exc}") from exc
    if root.is_symlink() or not stat.S_ISDIR(root_before[2]):
        raise PackageError("项目根目录必须是普通目录")
    try:
        git_before = root_git_token(root)
    except ValueError as exc:
        raise PackageError(str(exc)) from exc
    before = _scan_project(root)
    files = {}
    directories = set()
    for relative, identity in before.items():
        path = root / relative
        mode = identity[2]
        if stat.S_ISDIR(mode):
            directories.add(relative)
            continue
        if not stat.S_ISREG(mode):
            raise PackageError(f"项目路径不是普通文件或目录: {relative}")
        try:
            data = path.read_bytes()
            after_read = _file_identity(path)
        except OSError as exc:
            raise PackageError(f"无法读取项目文件: {relative}") from exc
        if after_read != identity or len(data) != identity[3]:
            raise PackageError(f"项目文件在捕获期间已改变: {relative}")
        if len(data) > MAX_PROJECT_FILE_BYTES:
            raise PackageError(f"单文件超过体积限制: {relative}")
        files[relative] = data
    after = _scan_project(root)
    try:
        root_after = _file_identity(root)
        git_after = root_git_token(root)
    except OSError as exc:
        raise PackageError(f"项目根目录在捕获期间已改变: {exc}") from exc
    except ValueError as exc:
        raise PackageError(str(exc)) from exc
    if (before != after or root_before != root_after
            or git_before != git_after):
        raise PackageError("项目树在捕获期间已改变")
    return files, directories


def _validate_snapshot(files, directories):
    """Validate the exact captured bytes in an isolated project tree."""
    with tempfile.TemporaryDirectory(
            prefix="bilibili-meme-atlas-snapshot-") as temporary:
        snapshot = Path(temporary) / NAME
        snapshot.mkdir()
        for relative in sorted(
                directories, key=lambda item: (item.count("/"), item)):
            (snapshot / relative).mkdir(exist_ok=True)
        for relative, data in sorted(files.items()):
            path = snapshot / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        errors = validate_all(snapshot)
        if errors:
            raise PackageError("项目目录无效：" + "; ".join(errors))
        from build import projections
        for name, expected in projections(snapshot).items():
            observed = files.get(name)
            if observed != expected.encode("utf-8"):
                raise PackageError(f"确定性派生文件损坏或过期: {name}")


def source_files(root: Path = ROOT) -> dict[str, bytes]:
    """Capture and validate the exact bytes returned for packaging."""
    root = Path(root)
    files, directories = _capture_project(root)
    _validate_snapshot(files, directories)
    for relative in files:
        if not allowed_project_path(relative):
            raise PackageError(f"项目文件边界无效: {relative}")
        archive_name(relative)
    if not files:
        raise PackageError("项目目录没有可归档文件")
    return files


def version_of(files: dict[str, bytes]) -> str:
    try:
        metadata = json.loads(files["references/metadata.json"].decode("utf-8"))
        version = metadata["version"]
    except (KeyError, UnicodeError, ValueError, TypeError) as exc:
        raise PackageError("无法读取项目版本") from exc
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise PackageError("项目版本必须为三段数字版本")
    return version


def verify_archive(path: Path, files: dict[str, bytes] | None = None) -> dict:
    """Verify member names, types and bytes against an expected project tree."""
    path = Path(path)
    if files is None:
        files = source_files(ROOT)
    expected = {archive_name(name): data for name, data in files.items()}
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PackageError("归档缺失或超过体积限制")
    try:
        blob = path.read_bytes()
        if len(blob) > MAX_ARCHIVE_BYTES:
            raise PackageError("归档缺失或超过体积限制")
        with ZipFile(io.BytesIO(blob)) as archive:
            members = archive.infolist()
            for info in members:
                if (info.orig_filename != info.filename
                        or any(ord(char) < 32 for char in info.orig_filename)):
                    raise PackageError("归档原始文件名含控制字符或截断")
            names = [info.filename for info in members]
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise PackageError("归档文件列表与项目不一致")
            total = 0
            for name, data in expected.items():
                info = archive.getinfo(name)
                mode = info.external_attr >> 16
                if (info.is_dir()
                        or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG))):
                    raise PackageError(f"归档包含目录、符号链接或特殊文件: {name}")
                total += info.file_size
                if (info.file_size != len(data)
                        or info.file_size > MAX_PROJECT_FILE_BYTES
                        or total > MAX_ARCHIVE_BYTES):
                    raise PackageError(f"归档文件长度无效: {name}")
            bad = archive.testzip()
            if bad is not None:
                raise PackageError(f"归档数据损坏: {bad}")
            for name, data in expected.items():
                if archive.read(name) != data:
                    raise PackageError(f"归档文件内容与项目不一致: {name}")
    except (BadZipFile, EOFError, OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, PackageError):
            raise
        raise PackageError(f"无法校验归档: {exc}") from exc
    return {
        "files": len(expected),
        "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
    }


def build_archive(root: Path = ROOT, output: Path | None = None) -> dict:
    """Write an atomic, byte-reproducible ZIP containing the complete project."""
    root = Path(root)
    if root.is_symlink():
        raise PackageError("项目根目录不能是符号链接")
    root = root.resolve()
    files = source_files(root)
    version = version_of(files)
    if output is None:
        output = root.parent / f"{NAME}-v{version}.zip"
    output = Path(output).absolute()
    if any(part.is_symlink() for part in (output, *output.parents)):
        raise PackageError("归档输出路径不能经过符号链接")
    output = output.resolve()
    if output.is_relative_to(root):
        raise PackageError("归档输出必须位于项目目录之外")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".bilibili-meme-atlas-", suffix=".zip", dir=output.parent)
    os.close(descriptor)
    try:
        with ZipFile(temporary, "w", compression=ZIP_STORED) as archive:
            for name, data in sorted(files.items()):
                info = ZipInfo(archive_name(name), TIMESTAMP)
                info.compress_type = ZIP_STORED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data, compress_type=ZIP_STORED)
        report = verify_archive(Path(temporary), files)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"version": version, "archive": str(output), **report}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("build", help="构建确定性的本地完整归档")
    create.add_argument("--output", type=Path)
    check = commands.add_parser("verify", help="将归档与当前项目逐文件比较")
    check.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build_archive(output=args.output)
        else:
            files = source_files(ROOT)
            result = {
                "version": version_of(files),
                "archive": str(args.archive.resolve()),
                **verify_archive(args.archive, files),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PackageError, OSError, ValueError) as exc:
        print(f"归档处理失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
