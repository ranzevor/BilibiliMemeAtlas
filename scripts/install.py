#!/usr/bin/env python3
"""Install, upgrade, remove, or recover a verified local Skill directory."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import uuid
from zipfile import BadZipFile, ZipFile

sys.dont_write_bytecode = True

from build import projections
from package import (MAX_ARCHIVE_BYTES, NAME, PackageError, archive_name,
                     source_files)
from validate import MAX_PROJECT_FILE_BYTES, allowed_project_path, validate_all
from writer_lock import (WriterLockError, lock_directory_token,
                         retire_lock_directory, writer_lease)

MAX_FILES = 512
LOCK_NAME = f".{NAME}.install-lock"
RECORD_NAME = "operation.json"
LEASE_NAME = "lease"
RECORD_FORMAT = "bilibili-meme-atlas-local-operation/v1"


def checked_path(path) -> Path:
    path = Path(path).expanduser()
    if ".." in path.parts:
        raise PackageError("本地路径不得包含 ..")
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise PackageError(f"本地路径不能经过符号链接: {part}")
    return path


def tree_identity(root) -> str:
    files = source_files(checked_path(root))
    rows = [
        {"path": name, "bytes": len(data),
         "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]
    serialized = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def archive_files(archive, expected_sha256: str) -> dict[str, bytes]:
    archive = checked_path(archive)
    if (not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)):
        raise PackageError("必须提供归档的 SHA-256")
    if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PackageError("归档缺失或超过体积限制")
    blob = archive.read_bytes()
    if hashlib.sha256(blob).hexdigest() != expected_sha256:
        raise PackageError("归档校验和不匹配")
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    with ZipFile(io.BytesIO(blob)) as zipped:
        members = zipped.infolist()
        if not 1 <= len(members) <= MAX_FILES:
            raise PackageError("归档文件数量不合法")
        for info in members:
            if (info.orig_filename != info.filename
                    or any(ord(char) < 32 for char in info.orig_filename)):
                raise PackageError("归档原始文件名含控制字符或截断")
            prefix = NAME + "/"
            if not info.filename.startswith(prefix):
                raise PackageError("归档必须只有固定项目根目录")
            relative = info.filename[len(prefix):]
            if (archive_name(relative) != info.filename
                    or not allowed_project_path(relative)):
                raise PackageError(f"文件不在项目边界内: {relative}")
            folded = relative.casefold()
            if folded in seen:
                raise PackageError("归档路径重复或大小写冲突")
            seen.add(folded)
            mode = info.external_attr >> 16
            if (info.flag_bits & 0x1 or info.is_dir()
                    or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG))):
                raise PackageError("归档只能包含未加密的普通文件")
            total += info.file_size
            if (info.file_size > MAX_PROJECT_FILE_BYTES
                    or total > MAX_ARCHIVE_BYTES):
                raise PackageError("归档展开内容超过体积限制")
            files[relative] = zipped.read(info)
    for name in files:
        parts = name.split("/")
        if any("/".join(parts[:index]) in files
               for index in range(1, len(parts))):
            raise PackageError("归档文件与目录冲突")
    return files


def validate_installation(root) -> str:
    root = Path(root)
    errors = validate_all(root)
    if errors:
        raise PackageError("目录内容无效：" + "; ".join(errors))
    for name, text in projections(root).items():
        path = root / name
        if not path.is_file() or path.read_bytes() != text.encode("utf-8"):
            raise PackageError(f"确定性派生文件损坏或过期: {name}")
    return tree_identity(root)


def _lock(parent: Path):
    lock = parent / LOCK_NAME
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        if (not lock.is_symlink() and lock.is_dir()
                and {path.name for path in lock.iterdir()} == {LEASE_NAME}):
            stale_token = lock_directory_token(
                lock, "本地目录操作", expected_names={LEASE_NAME})
            retire_lock_directory(
                lock, "本地目录操作", expected_token=stale_token)
            lock.mkdir(mode=0o700)
        else:
            raise PackageError("已有本地目录操作或待恢复状态") from exc
    lease = lock / LEASE_NAME
    handle = None
    try:
        handle = lease.open("x+b")
        os.chmod(lease, 0o600)
        if os.name == "posix":
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        token = lock_directory_token(
            lock, "本地目录操作", expected_names={LEASE_NAME})
        return lock, handle, token
    except BaseException:
        if handle is not None:
            handle.close()
        lease.unlink(missing_ok=True)
        lock.rmdir()
        raise


def _write_record(lock: Path, value: dict) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
           + "\n").encode("utf-8")
    record = lock / RECORD_NAME
    descriptor, temporary = tempfile.mkstemp(
        prefix=".operation-", suffix=".tmp", dir=lock)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, record)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return raw


def _finish_lock(lock: Path, handle, expected_token):
    handle.close()
    return retire_lock_directory(
        lock, "本地目录操作", expected_token=expected_token)


def _record(operation: str, *, backup: Path | None, stage: Path | None,
            new_identity: str | None, previous_identity: str | None) -> dict:
    return {
        "format": RECORD_FORMAT,
        "operation": operation,
        "target": NAME,
        "backup": backup.name if backup else None,
        "stage": (f"{stage.parent.name}/{stage.name}" if stage else None),
        "new_identity": new_identity,
        "previous_identity": previous_identity,
    }


def _directory_object(path):
    value = Path(path).stat(follow_symlinks=False)
    if not stat.S_ISDIR(value.st_mode):
        raise PackageError("目录对象已改变")
    return value.st_dev, value.st_ino


def _retain_failed_candidate(target, failed, expected_object):
    if _directory_object(target) != expected_object:
        raise PackageError("回滚目标不是本次安装的目录对象")
    os.replace(target, failed)
    moved_object = _directory_object(failed)
    if moved_object != expected_object:
        if target.exists() or target.is_symlink():
            raise PackageError("回滚移动期间出现未知目标，保留现场")
        os.rename(failed, target)
        if _directory_object(target) != moved_object:
            raise PackageError("回滚移动期间未知目录再次变化")
        raise PackageError("回滚移动期间目录对象已改变，未知目标已保留原位")


def _install_locked(archive, parent, expected_sha256: str, *, expected_current=None):
    """Install a new tree, or replace an exact current tree while retaining it."""
    parent = checked_path(parent)
    if not parent.is_dir():
        raise PackageError("父目录必须已经存在")
    target = checked_path(parent / NAME)
    files = archive_files(archive, expected_sha256)
    lock, lease, retirement_token = _lock(parent)
    stage_parent: Path | None = None
    backup: Path | None = None
    keep_lock = False
    result = None
    try:
        if target.exists():
            if (not expected_current
                    or tree_identity(target) != expected_current):
                raise PackageError("升级须绑定当前目录身份；不得覆盖未知或已修改内容")
        elif expected_current is not None:
            raise PackageError("声明的当前目录不存在")
        stage_parent = Path(tempfile.mkdtemp(prefix=f".{NAME}.stage-", dir=parent))
        stage = stage_parent / NAME
        stage.mkdir(mode=0o755)
        for name, data in sorted(files.items()):
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as handle:
                handle.write(data)
            os.chmod(path, 0o644)
        identity = validate_installation(stage)
        staged_object = _directory_object(stage)
        if target.exists():
            if tree_identity(target) != expected_current:
                raise PackageError("升级期间当前目录已改变")
            target_stat = target.stat()
            target_object = (target_stat.st_dev, target_stat.st_ino)
            backup = parent / f".{NAME}.backup-{uuid.uuid4().hex}"
        else:
            target_object = None
        _write_record(lock, _record(
            "install", backup=backup, stage=stage,
            new_identity=identity, previous_identity=expected_current))
        retirement_token = lock_directory_token(
            lock, "本地目录操作",
            expected_object=retirement_token[0],
            expected_names={LEASE_NAME, RECORD_NAME})
        try:
            if backup:
                os.replace(target, backup)
                backup_stat = backup.stat()
                if ((backup_stat.st_dev, backup_stat.st_ino) != target_object
                        or tree_identity(backup) != expected_current):
                    raise PackageError("升级移动期间当前目录已改变")
                if target.exists() or target.is_symlink():
                    raise PackageError("升级移动期间目标路径已出现")
            os.replace(stage, target)
            if (validate_installation(target) != identity
                    or _directory_object(target) != staged_object):
                raise PackageError("安装回读身份不一致")
            if backup and tree_identity(backup) != expected_current:
                raise PackageError("升级完成前旧目录已改变")
        except BaseException:
            try:
                failed = parent / f".{NAME}.failed-{uuid.uuid4().hex}"
                if backup and backup.exists():
                    if target.exists() or target.is_symlink():
                        _retain_failed_candidate(target, failed, staged_object)
                    if target.exists() or target.is_symlink():
                        raise PackageError("回滚目标已被占用")
                    os.replace(backup, target)
                elif backup is None and (target.exists() or target.is_symlink()):
                    _retain_failed_candidate(target, failed, staged_object)
                elif backup is not None and (target.exists() or target.is_symlink()):
                    if _directory_object(target) != target_object:
                        raise PackageError("原安装目标的目录对象已改变")
            except BaseException as rollback_error:
                keep_lock = True
                raise PackageError("安装回滚未完成；保留待恢复状态") from rollback_error
            raise
        result = {
            "status": "installed",
            "path": str(target),
            "identity": identity,
            "backup": str(backup) if backup else None,
        }
        return result
    finally:
        if keep_lock:
            lease.close()
        else:
            if stage_parent is not None:
                shutil.rmtree(stage_parent, ignore_errors=True)
            pending = _finish_lock(lock, lease, retirement_token)
            if result is not None:
                result["cleanup_pending"] = str(pending) if pending else None


def _uninstall_locked(parent, expected_current: str):
    """Move an exact installed tree out of the discovery path without deleting it."""
    parent = checked_path(parent)
    if not parent.is_dir():
        raise PackageError("父目录必须已经存在")
    target = checked_path(parent / NAME)
    lock, lease, retirement_token = _lock(parent)
    keep_lock = False
    result = None
    try:
        if not expected_current or tree_identity(target) != expected_current:
            raise PackageError("卸载须绑定当前目录身份")
        target_stat = target.stat()
        target_object = (target_stat.st_dev, target_stat.st_ino)
        backup = parent / f".{NAME}.removed-{uuid.uuid4().hex}"
        _write_record(lock, _record(
            "uninstall", backup=backup, stage=None,
            new_identity=None, previous_identity=expected_current))
        retirement_token = lock_directory_token(
            lock, "本地目录操作",
            expected_object=retirement_token[0],
            expected_names={LEASE_NAME, RECORD_NAME})
        try:
            os.replace(target, backup)
            backup_stat = backup.stat()
            if ((backup_stat.st_dev, backup_stat.st_ino) != target_object
                    or tree_identity(backup) != expected_current):
                raise PackageError("卸载移动期间当前目录已改变")
            if target.exists() or target.is_symlink():
                raise PackageError("卸载移动期间目标路径已出现")
        except BaseException:
            try:
                if backup.exists():
                    if target.exists() or target.is_symlink():
                        raise PackageError("卸载回滚目标已被占用")
                    os.replace(backup, target)
            except BaseException as rollback_error:
                keep_lock = True
                raise PackageError("卸载回滚未完成；保留待恢复状态") from rollback_error
            raise
        result = {
            "status": "uninstalled",
            "backup": str(backup),
            "identity": expected_current,
        }
        return result
    finally:
        if keep_lock:
            lease.close()
        else:
            pending = _finish_lock(lock, lease, retirement_token)
            if result is not None:
                result["cleanup_pending"] = str(pending) if pending else None


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise PackageError("操作记录含重复字段")
        value[key] = item
    return value


def _relative_member(value, *, prefixes=()):
    if value is None:
        return None
    if (not isinstance(value, str) or "/" in value or "\\" in value
            or value in ("", ".", "..")
            or not any(value.startswith(prefix) for prefix in prefixes)):
        raise PackageError("操作记录含非法相对路径")
    return value


def _recovery_scene(parent):
    parent = checked_path(parent)
    lock = checked_path(parent / LOCK_NAME)
    record = checked_path(lock / RECORD_NAME)
    lease = checked_path(lock / LEASE_NAME)
    try:
        directory_token = lock_directory_token(
            lock, "本地目录操作",
            expected_names={RECORD_NAME, LEASE_NAME})
    except WriterLockError as exc:
        raise PackageError("恢复锁缺失或包含未知对象") from exc
    members = {name: (identity, body)
               for name, identity, body in directory_token[1]}
    record_identity, raw = members[RECORD_NAME]
    lease_identity, lease_raw = members[LEASE_NAME]
    if len(raw) > 16384 or lease_raw:
        raise PackageError("操作记录或租约文件无效")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise PackageError("操作记录无法读取") from exc
    keys = {"format", "operation", "target", "backup", "stage",
            "new_identity", "previous_identity"}
    if not isinstance(value, dict) or set(value) != keys:
        raise PackageError("操作记录字段无效")
    if (value["format"] != RECORD_FORMAT or value["target"] != NAME
            or value["operation"] not in {"install", "uninstall"}):
        raise PackageError("操作记录类型或目标无效")
    _relative_member(value["backup"], prefixes=(f".{NAME}.backup-", f".{NAME}.removed-"))
    if value["stage"] is not None:
        if (not isinstance(value["stage"], str)
                or not re.fullmatch(
                    rf"\.{re.escape(NAME)}\.stage-[^/\\]+/{re.escape(NAME)}",
                    value["stage"])):
            raise PackageError("操作记录暂存路径无效")
    for key in ("new_identity", "previous_identity"):
        identity = value[key]
        if identity is not None and not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise PackageError("操作记录身份无效")
    if value["operation"] == "install":
        valid_operation = (
            value["new_identity"] is not None
            and value["stage"] is not None
            and ((value["backup"] is None and value["previous_identity"] is None)
                 or (value["backup"] is not None
                     and value["previous_identity"] is not None))
        )
    else:
        valid_operation = (
            value["backup"] is not None
            and value["stage"] is None
            and value["new_identity"] is None
            and value["previous_identity"] is not None
        )
    if not valid_operation:
        raise PackageError("操作记录缺少所需身份")
    token = (
        directory_token[0],
        record_identity[:2],
        lease_identity[:2],
        raw,
        directory_token,
    )
    return lock, record, lease, value, token


def recovery_info(parent):
    """Read a pending local operation without changing it."""
    _lock, _record_path, _lease, value, token = _recovery_scene(parent)
    parent = checked_path(parent)
    target = parent / NAME

    def identity(path):
        try:
            return validate_installation(path) if path.is_dir() else None
        except (OSError, RuntimeError, ValueError, TypeError, KeyError):
            return None

    target_identity = identity(target)
    backup_path = parent / value["backup"] if value["backup"] else None
    backup_identity = identity(backup_path) if backup_path else None
    if value["operation"] == "install" and target_identity == value["new_identity"]:
        state = "committed_cleanup_pending"
    elif (value["operation"] == "uninstall" and not target.exists()
          and backup_identity == value["previous_identity"]):
        state = "committed_cleanup_pending"
    elif target_identity == value["previous_identity"]:
        state = "rollback_completed_cleanup_pending"
    else:
        state = "recovery_pending"
    return {
        "status": "recovery_pending",
        "state": state,
        "recovery_sha256": hashlib.sha256(token[3]).hexdigest(),
        "operation": value,
    }


def _recover_locked(parent, expected_recovery: str, *, backup=None,
                    expected_backup=None, already_restored=False,
                    expected_current=None, acknowledge_stopped_writers=False,
                    finalize_committed=False):
    """Restore the exact prior tree of an interrupted local directory operation."""
    if not acknowledge_stopped_writers:
        raise PackageError("恢复前须确认其他安装、更新和编辑写入者已停止")
    if os.name != "posix":
        raise PackageError("自动恢复租约当前仅支持 POSIX 文件锁")
    parent = checked_path(parent)
    target = checked_path(parent / NAME)
    lock, record, lease, value, token = _recovery_scene(parent)
    if (not isinstance(expected_recovery, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected_recovery)
            or hashlib.sha256(token[3]).hexdigest() != expected_recovery):
        raise PackageError("操作记录摘要不匹配")
    import fcntl
    with lease.open("rb") as lease_handle:
        try:
            fcntl.flock(lease_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PackageError("原目录操作或另一恢复进程仍持有租约") from exc

        def unchanged_scene():
            current = _recovery_scene(parent)[4]
            if current != token:
                raise PackageError("恢复期间操作记录或锁已改变")
            descriptor = os.fstat(lease_handle.fileno())
            if (descriptor.st_dev, descriptor.st_ino) != token[2]:
                raise PackageError("恢复租约已改变")

        unchanged_scene()
        if finalize_committed:
            if (backup is not None or expected_backup is not None
                    or already_restored or expected_current is not None):
                raise PackageError("已提交清理模式不接受恢复对象参数")
            recorded_backup = (parent / value["backup"]
                               if value["backup"] is not None else None)
            if value["operation"] == "install":
                if (not target.is_dir()
                        or validate_installation(target) != value["new_identity"]):
                    raise PackageError("安装结果未完整提交")
                if (recorded_backup is not None
                        and (not recorded_backup.is_dir()
                             or validate_installation(recorded_backup)
                             != value["previous_identity"])):
                    raise PackageError("已提交升级的旧目录备份不一致")
                identity = value["new_identity"]
            else:
                if target.exists() or target.is_symlink():
                    raise PackageError("卸载结果仍占用固定目标")
                if (recorded_backup is None or not recorded_backup.is_dir()
                        or validate_installation(recorded_backup)
                        != value["previous_identity"]):
                    raise PackageError("已提交卸载的保留目录不一致")
                identity = value["previous_identity"]
            unchanged_scene()
            retired = retire_lock_directory(
                lock, "本地目录操作", expected_token=token[4])
            return {
                "status": "committed_cleanup_completed",
                "operation": value["operation"],
                "identity": identity,
                "cleanup_pending": str(retired) if retired else None,
            }
        if already_restored:
            if backup is not None or expected_backup is not None or not expected_current:
                raise PackageError("已恢复模式只接受明确的当前身份")
            source = target
            expected = expected_current
        else:
            if expected_current is not None or backup is None or not expected_backup:
                raise PackageError("恢复须明确备份路径和备份身份")
            source = checked_path(backup)
            expected = expected_backup
            if (source.parent != parent or source.name != value["backup"]
                    or not source.name.startswith(
                        (f".{NAME}.backup-", f".{NAME}.removed-"))):
                raise PackageError("备份不属于此操作记录")
            if target.exists() or target.is_symlink():
                raise PackageError("恢复目标已存在；不得覆盖")
        if (not isinstance(expected, str)
                or not re.fullmatch(r"[a-f0-9]{64}", expected)):
            raise PackageError("恢复身份格式无效")
        recorded_identities = {value["previous_identity"]}
        if already_restored and value["operation"] == "install":
            recorded_identities.add(value["new_identity"])
        recorded_identities.discard(None)
        if expected not in recorded_identities:
            raise PackageError("恢复身份与操作记录不一致")
        if (source / ".atlas-update.lock").exists():
            raise PackageError("知识更新仍活动或待恢复")
        source_stat = source.stat()
        source_object = (source_stat.st_dev, source_stat.st_ino)
        if validate_installation(source) != expected:
            raise PackageError("恢复对象身份不匹配")
        unchanged_scene()
        if ((source.stat().st_dev, source.stat().st_ino) != source_object
                or tree_identity(source) != expected):
            raise PackageError("恢复期间来源已改变")
        if not already_restored:
            if target.exists() or target.is_symlink():
                raise PackageError("恢复期间目标已出现")
            os.rename(source, target)
        if (validate_installation(target) != expected
                or tree_identity(target) != expected
                or (target.stat().st_dev, target.stat().st_ino) != source_object):
            raise PackageError("恢复回读身份不一致")
        unchanged_scene()
        retired = retire_lock_directory(
            lock, "本地目录操作", expected_token=token[4])
        return {
            "status": "recovered",
            "path": str(target),
            "identity": expected,
            "staged_content": value["stage"],
            "cleanup_pending": str(retired) if retired else None,
        }


def install(archive, parent, expected_sha256: str, *, expected_current=None):
    parent = checked_path(parent)
    if not parent.is_dir():
        raise PackageError("父目录必须已经存在")
    try:
        with writer_lease(parent / NAME):
            return _install_locked(
                archive, parent, expected_sha256,
                expected_current=expected_current)
    except WriterLockError as exc:
        raise PackageError(str(exc)) from exc


def uninstall(parent, expected_current: str):
    parent = checked_path(parent)
    if not parent.is_dir():
        raise PackageError("父目录必须已经存在")
    try:
        with writer_lease(parent / NAME):
            return _uninstall_locked(parent, expected_current)
    except WriterLockError as exc:
        raise PackageError(str(exc)) from exc


def recover(parent, expected_recovery: str, *, backup=None,
            expected_backup=None, already_restored=False,
            expected_current=None, acknowledge_stopped_writers=False,
            finalize_committed=False):
    if not acknowledge_stopped_writers:
        raise PackageError("恢复前须确认其他安装、更新和编辑写入者已停止")
    parent = checked_path(parent)
    if not parent.is_dir():
        raise PackageError("父目录必须已经存在")
    try:
        with writer_lease(parent / NAME):
            return _recover_locked(
                parent, expected_recovery, backup=backup,
                expected_backup=expected_backup,
                already_restored=already_restored,
                expected_current=expected_current,
                acknowledge_stopped_writers=acknowledge_stopped_writers,
                finalize_committed=finalize_committed)
    except WriterLockError as exc:
        raise PackageError(str(exc)) from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    identity = commands.add_parser("identity", help="计算完整目录身份")
    identity.add_argument("directory", type=Path)
    add = commands.add_parser("install", help="安装或按准确身份升级本地归档")
    add.add_argument("archive", type=Path)
    add.add_argument("--parent", required=True, type=Path)
    add.add_argument("--sha256", required=True)
    add.add_argument("--expected-current")
    remove = commands.add_parser("uninstall", help="移出安装目录并保留可恢复副本")
    remove.add_argument("--parent", required=True, type=Path)
    remove.add_argument("--expected-current", required=True)
    info = commands.add_parser("recovery-info", help="只读显示待恢复操作记录")
    info.add_argument("--parent", required=True, type=Path)
    restore = commands.add_parser("recover", help="恢复中断操作的准确旧目录")
    restore.add_argument("--parent", required=True, type=Path)
    restore.add_argument("--expected-recovery", required=True)
    restore.add_argument("--backup", type=Path)
    restore.add_argument("--expected-backup")
    restore.add_argument("--already-restored", action="store_true")
    restore.add_argument("--expected-current")
    restore.add_argument("--acknowledge-stopped-writers", action="store_true")
    restore.add_argument("--finalize-committed", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "identity":
            result = {"identity": tree_identity(args.directory)}
        elif args.action == "install":
            result = install(
                args.archive, args.parent, args.sha256,
                expected_current=args.expected_current)
        elif args.action == "uninstall":
            result = uninstall(args.parent, args.expected_current)
        elif args.action == "recovery-info":
            result = recovery_info(args.parent)
        else:
            result = recover(
                args.parent, args.expected_recovery,
                backup=args.backup, expected_backup=args.expected_backup,
                already_restored=args.already_restored,
                expected_current=args.expected_current,
                acknowledge_stopped_writers=args.acknowledge_stopped_writers,
                finalize_committed=args.finalize_committed)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BadZipFile, OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        print(f"本地目录操作失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
