#!/usr/bin/env python3
"""Cross-process writer lease shared by corpus and directory lifecycle operations."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import uuid


class WriterLockError(ValueError):
    pass


MAX_LOCK_FILE_BYTES = 8 * 1024 * 1024


def _open_lease_handle(descriptor):
    return os.fdopen(descriptor, "r+b", buffering=0)


def _move_lock(source, destination):
    os.replace(source, destination)


def lock_directory_token(lock, label, *, expected_object=None,
                         expected_names=None):
    """Bind one shallow, regular-file-only lock directory and its contents."""
    lock = Path(lock)
    try:
        root_before = lock.stat(follow_symlinks=False)
    except OSError as exc:
        raise WriterLockError(f"{label}锁无法读取: {exc}") from exc
    root_object = (root_before.st_dev, root_before.st_ino)
    if (lock.is_symlink() or not stat.S_ISDIR(root_before.st_mode)
            or (expected_object is not None
                and root_object != expected_object)):
        raise WriterLockError(f"{label}锁目录对象已改变")
    try:
        children = sorted(lock.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise WriterLockError(f"{label}锁目录无法枚举: {exc}") from exc
    names = {path.name for path in children}
    if expected_names is not None and names != set(expected_names):
        raise WriterLockError(f"{label}锁目录成员已改变")
    entries = []
    for child in children:
        try:
            before = child.stat(follow_symlinks=False)
            if (child.is_symlink() or not stat.S_ISREG(before.st_mode)
                    or before.st_size > MAX_LOCK_FILE_BYTES):
                raise WriterLockError(f"{label}锁含未知或超限对象")
            body = child.read_bytes()
            after = child.stat(follow_symlinks=False)
        except WriterLockError:
            raise
        except OSError as exc:
            raise WriterLockError(f"{label}锁成员无法读取: {exc}") from exc
        before_identity = (
            before.st_dev, before.st_ino, before.st_mode, before.st_size,
            before.st_mtime_ns)
        after_identity = (
            after.st_dev, after.st_ino, after.st_mode, after.st_size,
            after.st_mtime_ns)
        if before_identity != after_identity or len(body) != before.st_size:
            raise WriterLockError(f"{label}锁成员在读取期间已改变")
        entries.append((child.name, before_identity, body))
    try:
        root_after = lock.stat(follow_symlinks=False)
    except OSError as exc:
        raise WriterLockError(f"{label}锁目录在读取期间已改变") from exc
    if ((root_after.st_dev, root_after.st_ino) != root_object
            or {path.name for path in lock.iterdir()} != names):
        raise WriterLockError(f"{label}锁目录在读取期间已改变")
    return root_object, tuple(entries)


def retire_lock_directory(lock, label, *, retired_parent=None,
                          retired_prefix=None, expected_token=None):
    """Atomically remove a live lock name before best-effort physical cleanup."""
    lock = Path(lock)
    observed = lock_directory_token(lock, label)
    if expected_token is not None and observed != expected_token:
        raise WriterLockError(f"{label}锁已在清理前改变")
    parent = Path(retired_parent) if retired_parent is not None else lock.parent
    if parent.is_symlink() or not parent.is_dir():
        raise WriterLockError(f"{label}清理目录不安全")
    prefix = retired_prefix if retired_prefix is not None else lock.name
    if (not isinstance(prefix, str) or not prefix
            or "/" in prefix or "\\" in prefix or prefix in (".", "..")):
        raise WriterLockError(f"{label}清理名称不安全")
    retired = parent / f"{prefix}.cleanup-{uuid.uuid4().hex}"
    _move_lock(lock, retired)
    try:
        retired_token = lock_directory_token(retired, label)
    except WriterLockError as exc:
        raise WriterLockError(
            f"{label}退役目录无法安全检查；已保留: {retired}") from exc
    if retired_token != observed:
        raise WriterLockError(
            f"{label}退役时对象已被替换；已保留: {retired}")

    # The lock format is shallow. Remove only the exact regular files that were
    # bound above, never recursively delete a directory that gained an unknown
    # member after retirement.
    try:
        if lock_directory_token(retired, label) != observed:
            raise WriterLockError(
                f"{label}退役目录在删除前已改变；已保留: {retired}")
        for name, identity, body in observed[1]:
            child = retired / name
            before = child.stat(follow_symlinks=False)
            current_identity = (
                before.st_dev, before.st_ino, before.st_mode,
                before.st_size, before.st_mtime_ns)
            if (child.is_symlink() or not stat.S_ISREG(before.st_mode)
                    or current_identity != identity
                    or child.read_bytes() != body):
                raise WriterLockError(
                    f"{label}退役成员在删除前已改变；已保留: {retired}")
            child.unlink()
        retired.rmdir()
    except WriterLockError:
        raise
    except OSError:
        return retired
    return None


@contextmanager
def writer_lease(root):
    """Hold the sibling lease for every writer that can replace or edit root."""
    root = Path(root).absolute()
    if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
        raise WriterLockError("写入目标路径不能经过符号链接")
    parent = root.parent
    if not parent.is_dir():
        raise WriterLockError("写入目标的父目录必须已存在")
    lease = parent / f".{root.name}.writer-lease"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lease, flags, 0o600)
    except OSError as exc:
        raise WriterLockError(f"无法打开共享写入租约: {exc}") from exc
    try:
        handle = _open_lease_handle(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    locked = False
    try:
        mode = os.fstat(handle.fileno()).st_mode
        if not stat.S_ISREG(mode) or lease.is_symlink():
            raise WriterLockError("共享写入租约必须是普通文件")
        os.chmod(lease, 0o600)
        if os.name == "posix":
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WriterLockError("另一个安装、更新或恢复写入者仍在活动") from exc
            locked = True
        elif os.name == "nt":
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise WriterLockError("另一个安装、更新或恢复写入者仍在活动") from exc
            locked = True
        else:
            raise WriterLockError("当前系统不支持共享写入租约")
        yield lease
    finally:
        # The operation body has already returned or raised. Lease release must
        # never replace that result, especially after its transaction record was
        # atomically retired. Closing the descriptor releases the OS lock even
        # when an explicit unlock reports an error.
        try:
            if locked:
                if os.name == "posix":
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except BaseException:
            pass
        finally:
            try:
                handle.close()
            except BaseException:
                # A close implementation may report failure before releasing
                # the descriptor. Retry once, then fall back to the descriptor
                # captured at open time.
                try:
                    handle.close()
                except BaseException:
                    try:
                        if not getattr(handle, "closed", False):
                            os.close(descriptor)
                    except BaseException:
                        pass
