#!/usr/bin/env python3
"""Bounded public discovery and checked, incremental catalogue updates."""
from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.dont_write_bytecode = True

from atlas import (FUTURE_SHARD, ROOT, SHARDS, CatalogueError, era_for_year,
                   _load_catalogue_snapshot, get_entries, load_catalogue, load_json, normalize,
                   shard_files, strict_json_loads)
from build import atomic_write, projections
from validate import (expected_rights_registry, iso_date, validate_all,
                      validate_records, validation_date)
from writer_lock import (WriterLockError, lock_directory_token,
                         retire_lock_directory, writer_lease)

DISCOVERY_FORMAT = "bilibili-meme-atlas-discovery/v1"
UPDATE_FORMAT = "bilibili-meme-atlas-update/v1"
POPULAR_URL = "https://api.bilibili.com/x/web-interface/popular"
BVID = re.compile(r"BV[0-9A-Za-z]{10}\Z")
VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)\Z")
MAX_BATCH_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 50000
MAX_ENTRIES_PER_BATCH = 25
MAX_SOURCES_PER_BATCH = 60
UPDATE_DERIVED_PATHS = frozenset({
    "references/catalog.md",
    "references/coverage.md",
    "references/history-index.md",
    "references/corpus-summary.json",
})
UPDATE_MANDATORY_PATHS = frozenset({
    "references/metadata.json", *UPDATE_DERIVED_PATHS,
})
UPDATE_STATIC_CONTENT_PATHS = frozenset({
    "references/sources.json", "references/rights.json", *UPDATE_MANDATORY_PATHS,
})


class UpdateError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_json(url: str) -> dict:
    """Request only a fixed, uncredentialed public Bilibili API endpoint."""
    if not re.fullmatch(r"https://api\.bilibili\.com/x/web-interface/popular\?pn=[1-3]&ps=20", url):
        raise UpdateError("网络请求目标不在允许的公开热门列表范围")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "BilibiliMemeAtlas/0.1.0 (read-only)"})
    opener = build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=8) as response:
            if response.status != 200:
                raise UpdateError(f"热门列表返回 HTTP {response.status}")
            if "json" not in response.headers.get("Content-Type", ""):
                raise UpdateError("热门列表响应不是 JSON")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise UpdateError("热门列表响应超过体积上限")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"热门列表不可访问：{exc}") from exc
    try:
        obj = strict_json_loads(
            payload, max_depth=MAX_JSON_DEPTH, max_nodes=MAX_JSON_NODES)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise UpdateError("热门列表返回非有效 JSON") from exc
    if not isinstance(obj, dict):
        raise UpdateError("热门列表响应根对象无效")
    return obj


def discover(pages: int = 1, *, fetch=public_json, pause=time.sleep, now=None) -> dict:
    if type(pages) is not int or not 1 <= pages <= 3:
        raise UpdateError("每轮仅可读取 1–3 页热门视频")
    now = now or datetime.now(timezone.utc)
    seen = set()
    items = []
    for pn in range(1, pages + 1):
        if pn > 1:
            pause(1.2)
        result = fetch(f"{POPULAR_URL}?pn={pn}&ps=20")
        if not isinstance(result, dict) or type(result.get("code")) is not int:
            raise UpdateError("热门列表响应缺少状态码")
        if result["code"] != 0:
            raise UpdateError(f"热门列表拒绝请求（code={result['code']}），当前读取停止，不重试或尝试绕过风控")
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise UpdateError("热门列表响应缺少视频列表")
        for rank, record in enumerate(data["list"][:20], start=1):
            if not isinstance(record, dict):
                continue
            bvid = record.get("bvid")
            title = record.get("title")
            if not isinstance(bvid, str) or not BVID.fullmatch(bvid) or not isinstance(title, str) or not title.strip() or bvid in seen:
                continue
            seen.add(bvid)
            def clean(v, length):
                return html.unescape(re.sub(r"<[^>]+>", "", v if isinstance(v, str) else ""))[:length]
            pub = record.get("pubdate")
            try:
                published = datetime.fromtimestamp(pub, timezone.utc).date().isoformat() if type(pub) is int and pub > 0 else None
            except (OSError, ValueError, OverflowError):
                published = None
            owner = record.get("owner") or {}
            stat = record.get("stat") or {}
            stat = stat if isinstance(stat, dict) else {}
            def count(k):
                v = stat.get(k)
                return v if type(v) is int and v >= 0 else None
            items.append({"bvid": bvid, "url": f"https://www.bilibili.com/video/{bvid}",
                          "list_page": pn, "list_rank": rank,
                          "video_views": count("view"), "video_likes": count("like"),
                          "title": clean(title, 200), "description": clean(record.get("desc", ""), 600),
                          "category": clean(record.get("tname", ""), 80),
                          "creator": clean(owner.get("name", "") if isinstance(owner, dict) else "", 80),
                          "published_at": published})
        if data.get("no_more") is True:
            break
    return {"format": DISCOVERY_FORMAT, "collected_at": now.isoformat(),
            "source": {"name": "Bilibili public popular list", "endpoint": POPULAR_URL,
                       "pages_requested": pages, "auth": "none"},
            "items": items, "access_level": "metadata", "interpretation": "仅是热门视频候选线索，不证明标题为热梗、梗的首创、流行程度或出处"}


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_record(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return digest_bytes(payload)


def semantic_record(value: dict) -> str:
    """Ignore order only in contract-defined set-like fields, preserve prose and dates."""
    row = json.loads(json.dumps(value))
    for key in ("aliases", "relations", "sensitivity", "tags"):
        if isinstance(row.get(key), list):
            row[key] = sorted(row[key])
    if isinstance(row.get("evidence"), list):
        for ref in row["evidence"]:
            if isinstance(ref, dict) and isinstance(ref.get("claims"), list):
                ref["claims"] = sorted(ref["claims"])
        row["evidence"] = sorted(row["evidence"], key=lambda ref: json.dumps(ref, sort_keys=True))
    return digest_record(row)


def fingerprint(root: Path = ROOT) -> str:
    root = Path(root)
    chunks = []
    for name in (*shard_files(root), "sources.json", "metadata.json", "rights.json"):
        body = (root / "references" / name).read_bytes()
        encoded = name.encode("utf-8")
        chunks.extend((len(encoded).to_bytes(4, "big"), encoded,
                       len(body).to_bytes(8, "big"), body))
    return digest_bytes(b"".join(chunks))


def read_proposal(path: Path, *, as_of=None) -> dict:
    try:
        if path.stat().st_size > MAX_BATCH_BYTES:
            raise UpdateError("提案超过 2 MiB 上限")
        data = load_json(path)
    except (CatalogueError, OSError) as exc:
        raise UpdateError(f"读取提案失败：{exc}") from exc
    if not isinstance(data, dict) or set(data) != {"format", "base_manifest_sha256", "checked_at", "sources", "entries"}:
        raise UpdateError("提案字段不符合更新合同")
    if data["format"] != UPDATE_FORMAT:
        raise UpdateError("不支持的提案格式")
    if not isinstance(data["base_manifest_sha256"], str) or not re.fullmatch("[a-f0-9]{64}", data["base_manifest_sha256"]):
        raise UpdateError("提案缺少有效基线指纹")
    try:
        checked = iso_date(data["checked_at"])
    except (TypeError, ValueError) as exc:
        raise UpdateError("checked_at 必须为有效 YYYY-MM-DD") from exc
    if checked > validation_date(as_of):
        raise UpdateError("资料核查时间不能是未来日期")
    for key, limit in (("sources", MAX_SOURCES_PER_BATCH), ("entries", MAX_ENTRIES_PER_BATCH)):
        values = data[key]
        if not isinstance(values, list) or len(values) > limit:
            raise UpdateError(f"{key} 列表超出数量上限或不是数组")
        for item in values:
            if not isinstance(item, dict) or set(item) - {"action", "record", "before_sha256", "distinct_sense_of"} or not {"action", "record"} <= set(item):
                raise UpdateError(f"{key} 存在无效操作字段")
            if item["action"] not in ("add", "replace") or not isinstance(item["record"], dict):
                raise UpdateError(f"{key} 仅支持完整新增或替换对象，不自动删除")
            if item["action"] == "replace":
                if set(item) != {"action", "record", "before_sha256"} or not isinstance(item["before_sha256"], str) or not re.fullmatch("[a-f0-9]{64}", item["before_sha256"]):
                    raise UpdateError("替换操作必须绑定旧记录的 SHA-256")
            elif "before_sha256" in item:
                raise UpdateError("新增操作不得携带替换前指纹")
            if key == "sources" and "distinct_sense_of" in item:
                raise UpdateError("来源不能声明词义消歧")
            if "distinct_sense_of" in item and (not isinstance(item["distinct_sense_of"], list) or
                                                  not item["distinct_sense_of"] or
                                                  any(not isinstance(v, str) for v in item["distinct_sense_of"])):
                raise UpdateError("多义记录必须提供已有记录 ID")
    return data


def canonical_source_url(url: str) -> str:
    """Conservative identity: discard fragments and known tracking, not semantic queries."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/") or "/"
    if host in ("www.bilibili.com", "m.bilibili.com", "bilibili.com"):
        match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})", path)
        if match:
            # p identifies a part and is intentionally retained.
            query = [(k, v) for k, v in parse_qsl(parsed.query) if k == "p"]
            return urlunsplit(("https", "www.bilibili.com", path, urlencode(sorted(query)), ""))
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.startswith("utm_") and k not in {"spm_id_from", "vd_source"}]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urlencode(sorted(query)), ""))


def _apply_operations(data, existing: dict, *, label: str, checked: date):
    values = dict(existing)
    added, changed = [], []
    touched = set()
    for op in data:
        row = op["record"]
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            raise UpdateError(f"{label} 记录缺少 ID")
        if label == "entry" and (not isinstance(row.get("aliases"), list)
                                 or any(not isinstance(v, str) for v in row["aliases"])):
            raise UpdateError(f"词条 {rid} 的 aliases 必须是字符串数组")
        if label == "source" and not isinstance(row.get("url"), str):
            raise UpdateError(f"来源 {rid} 的 URL 必须是字符串")
        if rid in touched:
            raise UpdateError(f"同一提案重复操作 {label} ID：{rid}")
        touched.add(rid)
        if op["action"] == "add":
            if rid in values:
                raise UpdateError(f"{label} 已存在相同 ID，应显式替换：{rid}")
            if label == "source":
                # A URL can support multiple claims, but should have one source identity.
                if any(canonical_source_url(s.get("url", "")) == canonical_source_url(row.get("url", "")) for s in values.values()):
                    raise UpdateError(f"来源 URL 已登记，请复用原 source_id：{rid}")
            if label == "entry":
                names = {normalize(v) for v in [row.get("term", ""), *row.get("aliases", [])] if isinstance(v, str)}
                overlaps = {old["id"] for old in values.values()
                            if names & {normalize(v) for v in [old["term"], *old["aliases"]]}}
                declaration = set(op.get("distinct_sense_of", []))
                if overlaps != declaration:
                    raise UpdateError(f"词条同名或同别名须明确区分义项 {rid}：{', '.join(sorted(overlaps))}")
            added.append(rid)
        else:
            if rid not in values:
                raise UpdateError(f"{label} 无法替换不存在的 ID：{rid}")
            if digest_record(values[rid]) != op["before_sha256"]:
                raise UpdateError(f"{label} 源记录已变化：{rid}")
            if label == "source" and values[rid]["url"] != row.get("url"):
                raise UpdateError("来源链接变更必须登记新来源 ID，保留历史引用")
            if label == "entry":
                old_names = {normalize(v) for v in [values[rid].get("term", ""), *values[rid].get("aliases", [])] if isinstance(v, str)}
                new_names = {normalize(v) for v in [row.get("term", ""), *row.get("aliases", [])] if isinstance(v, str)}
                for other_id, other in values.items():
                    if other_id == rid:
                        continue
                    other_names = {normalize(v) for v in [other.get("term", ""), *other.get("aliases", [])] if isinstance(v, str)}
                    if (new_names & other_names) - (old_names & other_names):
                        raise UpdateError(f"替换不能引入未经消歧的同名或同别名：{rid}/{other_id}")
            if semantic_record(values[rid]) != semantic_record(row):
                changed.append(rid)
            else:
                row = values[rid]  # Preserve bytes/order of semantically unchanged data.
        if label == "source":
            try:
                source_date = iso_date(row.get("checked_at"))
            except (TypeError, ValueError) as exc:
                raise UpdateError(f"来源 {rid} 核查日期无效") from exc
            if source_date > checked:
                raise UpdateError(f"来源 {rid} 的核查日期晚于提案核查日期")
        values[rid] = row
    return values, added, changed


def _json_file(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def plan(root: Path, batch: dict, *, as_of=None,
         active_update_lock=False) -> tuple[dict, dict[str, str]]:
    root = Path(root)
    as_of = validation_date(as_of)
    # A fresh projection prevents overwriting unseen local changes or stale generators.
    snapshot = _load_catalogue_snapshot(
        root, as_of=as_of, active_update_lock=active_update_lock)
    expected = projections(root, as_of=as_of, _snapshot=snapshot)
    for name, content in expected.items():
        if (root / name).read_bytes() != content.encode("utf-8"):
            raise UpdateError(f"资料基线与派生文件不一致，先解决差异：{name}")
    if fingerprint(root) != batch["base_manifest_sha256"]:
        raise UpdateError("资料基线已变化，须在当前版本重新核对提案")
    entries, sources, metadata = snapshot.entries, snapshot.sources, snapshot.metadata
    checked = iso_date(batch["checked_at"])
    if checked > as_of:
        raise UpdateError("资料核查时间不能晚于当前UTC日期")
    source_records, added_sources, changed_sources = _apply_operations(
        batch["sources"], sources, label="source", checked=checked)
    entry_records, added_entries, changed_entries = _apply_operations(
        batch["entries"], {e["id"]: e for e in entries}, label="entry", checked=checked)

    # Candidate-only evidence cannot silently become a verified statement.
    for item in batch["entries"]:
        row = item["record"]
        if not isinstance(row.get("evidence"), list):
            raise UpdateError(f"词条 {row.get('id')} 的 evidence 必须是数组")
        for ref in row.get("evidence", []):
            if not isinstance(ref, dict) or not isinstance(ref.get("source_id"), str):
                raise UpdateError(f"词条 {row.get('id')} 的 source_id 必须是字符串")
            if ref.get("source_id") not in source_records:
                raise UpdateError(f"词条 {row.get('id')} 存在悬空来源引用：{ref.get('source_id')}")
        if row.get("evidence_status") == "partial":
            if not any(source_records.get(r.get("source_id"), {}).get("access_level") in ("full", "snippet")
                       and "meaning" in r.get("claims", []) for r in row.get("evidence", []) if isinstance(r, dict)):
                raise UpdateError(f"部分核实词条缺少实际释义依据：{row.get('id')}")
        if row.get("evidence_status") == "verified":
            if not any(source_records.get(r.get("source_id"), {}).get("access_level") == "full"
                       for r in row.get("evidence", []) if isinstance(r, dict)):
                raise UpdateError(f"已核实词条需要真正读取的完整证据：{row.get('id')}")

    if not (added_entries or changed_entries or added_sources or changed_sources):
        return ({"status": "no_update", "reason": "没有通过核查的新内容；版本和资料截止日保持不变",
                 "added_entries": [], "updated_entries": [], "added_sources": [], "updated_sources": [],
                 "version": metadata["version"], "knowledge_cutoff": metadata["knowledge_cutoff"],
                 "base_manifest_sha256": batch["base_manifest_sha256"], "changed_files": []}, {})

    latest_cutoff = max(iso_date(metadata["knowledge_cutoff"]), checked)
    ver = VERSION.fullmatch(metadata["version"])
    if not ver:
        raise UpdateError("原版本不满足 SemVer 三段式")
    updated_metadata = dict(metadata)
    updated_metadata["knowledge_cutoff"] = latest_cutoff.isoformat()
    updated_metadata["version"] = f"{ver[1]}.{ver[2]}.{int(ver[3]) + 1}"

    # Stage the complete revised reference directory before any write to the real one.
    with tempfile.TemporaryDirectory(prefix="atlas-stage-") as temp:
        temp_root = Path(temp)
        shutil.copytree(root / "references", temp_root / "references")
        periods = {e["era"] for e in entry_records.values()}
        for era in periods:
            span = re.fullmatch(r"([0-9]{4})-([0-9]{4})", era) if isinstance(era, str) else None
            if not span:
                raise UpdateError(f"词条时期无效：{era}")
            first, last = map(int, span.groups())
            if era_for_year(first) != era or last < first:
                raise UpdateError(f"时期范围非法：{era}")
            if first > latest_cutoff.year:
                raise UpdateError(f"时期晚于核查截止年份：{era}")
        future = sorted(x for x in periods if int(x[:4]) >= 2027)
        for era in future:
            year = 2027
            while year <= int(era[:4]):
                path = temp_root / "references" / f"{year}-{year + 4}.json"
                if not path.exists():
                    path.write_text("[]\n", encoding="utf-8")
                year += 5
        all_shards = shard_files(temp_root)
        changed_files = {}
        for name in all_shards:
            era = name[:-5]
            old_path = root / "references" / name
            existed = old_path.exists()
            rows = [v for v in entry_records.values() if v.get("era") == era]
            baseline_rows = [v for v in entries if v["era"] == era]
            if not existed or rows != baseline_rows:
                new_text = _json_file(rows)
                (temp_root / "references" / name).write_text(new_text, encoding="utf-8")
                changed_files["references/" + name] = new_text
        if added_sources or changed_sources:
            # Retain original ordering; append new sources in proposal order.
            existing_ids = [s["id"] for s in load_json(root / "references" / "sources.json")]
            new_ids = [s for s in source_records if s not in existing_ids]
            text = _json_file([source_records[s] for s in existing_ids + new_ids])
            (temp_root / "references" / "sources.json").write_text(text, encoding="utf-8")
            changed_files["references/sources.json"] = text
            if added_sources:
                rights_text = _json_file(expected_rights_registry(
                    root, source_ids=existing_ids + new_ids))
                (temp_root / "references" / "rights.json").write_text(
                    rights_text, encoding="utf-8")
                changed_files["references/rights.json"] = rights_text
        meta_text = _json_file(updated_metadata)
        (temp_root / "references" / "metadata.json").write_text(meta_text, encoding="utf-8")
        changed_files["references/metadata.json"] = meta_text
        # The normal validator enforces evidence, origin dates, references, and shard identity.
        load_catalogue(temp_root, as_of=as_of)
        derived = projections(temp_root, as_of=as_of)
        for filename, content in derived.items():
            changed_files[filename] = content
        return ({"added_entries": added_entries, "updated_entries": changed_entries,
                 "added_sources": added_sources, "updated_sources": changed_sources,
                 "version": updated_metadata["version"], "knowledge_cutoff": updated_metadata["knowledge_cutoff"],
                 "base_manifest_sha256": batch["base_manifest_sha256"],
                 "changed_files": sorted(changed_files)}, changed_files)


def reference_snapshot(root):
    root = Path(root)
    if root.is_symlink() or (root / "references").is_symlink():
        raise UpdateError("知识库目录不能是符号链接")
    files = {}
    for path in (root / "references").iterdir():
        if path.is_symlink() or not path.is_file():
            raise UpdateError(f"参考资料只能是普通文件，不接受符号链接: {path.name}")
        files[path.name] = path.read_bytes()
    return files


def restore_bytes(path, before):
    if before is None:
        path.unlink(missing_ok=True)
        return
    mode = path.stat().st_mode & 0o777 if path.exists() and path.is_file() and not path.is_symlink() else 0o644
    fd, temp_path = tempfile.mkstemp(prefix=".atlas-rollback-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(before)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _retire_update_lock(root: Path, lock: Path, expected_token):
    """Move update cleanup state outside the validated project tree atomically."""
    return retire_lock_directory(
        lock, "知识更新", retired_parent=root.parent,
        retired_prefix=f".{root.name}.atlas-update-lock",
        expected_token=expected_token)


def _validate_update_journal_paths(root: Path, names) -> None:
    """Accept exactly the files that the update planner can produce."""
    names = set(names)
    if not UPDATE_MANDATORY_PATHS <= names:
        raise UpdateError("恢复记录缺少必需的元数据或派生文件")
    content_paths = set()
    journal_future = set()
    for name in names:
        if name in UPDATE_STATIC_CONTENT_PATHS:
            if name in {"references/sources.json", "references/rights.json"}:
                content_paths.add(name)
            continue
        match = re.fullmatch(
            r"references/([0-9]{4})-([0-9]{4})\.json", name)
        if not match:
            raise UpdateError(f"恢复路径不属于更新计划: {name}")
        basename = name.removeprefix("references/")
        if basename in SHARDS:
            content_paths.add(name)
            continue
        start, end = map(int, match.groups())
        if (start < 2027 or end != start + 4
                or (start - 2027) % 5):
            raise UpdateError(f"恢复记录的时期分片非法: {name}")
        journal_future.add(start)
        content_paths.add(name)
    if not content_paths:
        raise UpdateError("恢复记录没有可更新的资料文件")

    actual_future = set()
    for path in (Path(root) / "references").glob("[0-9]*-[0-9]*.json"):
        if path.name in SHARDS:
            continue
        match = FUTURE_SHARD.fullmatch(path.name)
        if not match:
            raise UpdateError(f"项目含未知时期分片: {path.name}")
        start, end = map(int, match.groups())
        if (start < 2027 or end != start + 4
                or (start - 2027) % 5):
            raise UpdateError(f"项目时期分片非法: {path.name}")
        actual_future.add(start)
    combined = actual_future | journal_future
    if combined:
        expected = set(range(2027, max(combined) + 1, 5))
        if combined != expected:
            raise UpdateError("恢复记录的未来时期分片不连续")


def _write_update_journal(lock: Path, journal: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=".journal-", suffix=".tmp", dir=lock)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_json_file(journal))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, lock / "journal.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validate_update_project(root: Path, *, as_of=None,
                             _update_lock_token=None) -> None:
    try:
        snapshot = _load_catalogue_snapshot(
            root, as_of=as_of,
            active_update_lock=_update_lock_token is None,
            _update_lock_token=_update_lock_token)
    except CatalogueError as exc:
        raise UpdateError("更新后项目验证失败：" + str(exc)) from exc
    errors = validate_all(
        root, as_of=as_of, active_update_lock=True, _snapshot=snapshot)
    if errors:
        raise UpdateError("更新后项目验证失败：" + "; ".join(errors))
    expected = projections(root, as_of=as_of, _snapshot=snapshot)
    for name, text in expected.items():
        path = Path(root) / name
        if not path.is_file() or path.read_bytes() != text.encode("utf-8"):
            raise UpdateError(f"更新后派生文件不一致: {name}")


def _update_recovery_scene(root: Path):
    """Classify a strict journal without mutating either data or lock state."""
    root = Path(root)
    reference_snapshot(root)
    lock = root / ".atlas-update.lock"
    if lock.is_symlink() or (lock / "journal.json").is_symlink():
        raise UpdateError("恢复记录不能是符号链接")
    try:
        directory_token = lock_directory_token(
            lock, "知识更新", expected_names={"journal.json"})
    except WriterLockError as exc:
        raise UpdateError("恢复锁缺失或包含未知对象") from exc
    journal_body = directory_token[1][0][2]
    try:
        data = strict_json_loads(journal_body)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise UpdateError("无有效恢复记录；保留现场并人工检查") from exc
    if (not isinstance(data, dict)
            or set(data) != {"format", "files"}
            or data.get("format") != "atlas-update-journal/v1"
            or not isinstance(data.get("files"), list)
            or not data["files"]):
        raise UpdateError("无有效恢复记录；保留现场并人工检查")
    restored, states, seen = [], [], set()
    for item in data["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "before", "after_sha256"}:
            raise UpdateError("恢复记录字段无效")
        name = item["path"]
        if (not isinstance(name, str)
                or not re.fullmatch(
                    r"references/[A-Za-z0-9][A-Za-z0-9._-]*", name)
                or name in seen
                or not isinstance(item["after_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", item["after_sha256"])):
            raise UpdateError("恢复路径非法或重复")
        seen.add(name)
        if item["before"] is not None and not isinstance(item["before"], str):
            raise UpdateError("恢复记录的旧内容无效")
        try:
            before = (None if item["before"] is None
                      else base64.b64decode(item["before"], validate=True))
        except (ValueError, TypeError) as exc:
            raise UpdateError("恢复记录的旧内容无效") from exc
        current = (root / name).read_bytes() if (root / name).exists() else None
        if current == before:
            states.append("before")
        elif current is not None and digest_bytes(current) == item["after_sha256"]:
            states.append("after")
        else:
            raise UpdateError(f"恢复对象含未知修改，保留现场：{name}")
        restored.append((root / name, before))
    _validate_update_journal_paths(root, seen)
    try:
        current_token = lock_directory_token(
            lock, "知识更新", expected_names={"journal.json"})
    except WriterLockError as exc:
        raise UpdateError("读取期间恢复锁已改变") from exc
    if current_token != directory_token:
        raise UpdateError("读取期间恢复锁已改变")
    return lock, restored, states, directory_token


def recovery_info(root: Path = ROOT):
    """Read and classify a pending knowledge update without changing it."""
    root = Path(root)
    _lock, restored, states, token = _update_recovery_scene(root)
    if all(state == "after" for state in states):
        state = "committed_cleanup_pending"
    elif all(state == "before" for state in states):
        state = "rollback_completed_cleanup_pending"
    else:
        state = "recovery_pending"
    if state != "recovery_pending":
        _validate_update_project(root, _update_lock_token=token)
        current = _update_recovery_scene(root)
        if current[2] != states or current[3] != token:
            raise UpdateError("验证期间更新恢复现场已改变")
    return {"status": "recovery_pending", "state": state,
            "files": len(restored)}


def _recover_locked(root: Path, *, writers_stopped=False,
                    finalize_committed=False):
    """Restore only a recognized interrupted update; preserve unexplained edits."""
    if not writers_stopped:
        raise UpdateError("恢复前必须确认其他写入进程已停止")
    root = Path(root)
    lock, restored, states, retirement_token = _update_recovery_scene(root)
    if states and all(state == "after" for state in states):
        _validate_update_project(root)
        if not finalize_committed:
            raise UpdateError("更新已提交但清理未完成；核对后使用 --finalize-committed")
        current = _update_recovery_scene(root)
        if current[2] != states or current[3] != retirement_token:
            raise UpdateError("验证期间更新恢复现场已改变")
        retired = _retire_update_lock(root, lock, retirement_token)
        return {"status": "committed_cleanup_completed", "files": len(restored),
                "cleanup_pending": str(retired) if retired else None}
    if finalize_committed:
        raise UpdateError("当前记录不是已提交待清理状态")
    current_bytes = [
        (path, path.read_bytes() if path.exists() else None)
        for path, _before in restored
    ]
    try:
        for path, before in restored:
            restore_bytes(path, before)
        _validate_update_project(root)
    except BaseException as original:
        errors = []
        for path, current_bytes_value in reversed(current_bytes):
            try:
                restore_bytes(path, current_bytes_value)
            except BaseException as exc:
                errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
        if errors:
            raise UpdateError(
                "恢复验证失败且无法还原恢复前现场；保留记录："
                + "; ".join(errors)) from original
        raise
    current = _update_recovery_scene(root)
    if (not all(state == "before" for state in current[2])
            or current[3] != retirement_token):
        raise UpdateError("恢复验证后现场已改变")
    retired = _retire_update_lock(root, lock, retirement_token)
    return {"status": "recovered", "files": len(restored),
            "cleanup_pending": str(retired) if retired else None}


def _apply_locked(root: Path, batch: dict, *, as_of=None) -> dict:
    root = Path(root)
    reference_snapshot(root)
    lock = root / ".atlas-update.lock"
    if lock.exists() or lock.is_symlink():
        if (not lock.is_symlink() and lock.is_dir()
                and not any(lock.iterdir())):
            stale_token = lock_directory_token(
                lock, "知识更新", expected_names=set())
            _retire_update_lock(root, lock, stale_token)
        else:
            raise UpdateError("另一个知识库更新正在进行或等待恢复，禁止并发覆盖")
    preview, pending = plan(root, batch, as_of=as_of)
    if not pending:
        return preview
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise UpdateError("另一个知识库更新正在进行，禁止并发覆盖") from exc
    snapshots = {}
    written = []
    keep_journal = False
    completed = None
    cleanup_pending = None
    retirement_token = lock_directory_token(
        lock, "知识更新", expected_names=set())
    try:
        # Bind all input bytes, not merely the manifest (which a non-cooperating
        # editor might not regenerate). Cooperative writers use the lock above.
        baseline = reference_snapshot(root)
        result, changes = plan(root, batch, as_of=as_of, active_update_lock=True)
        current = reference_snapshot(root)
        if current != baseline:
            raise UpdateError("预审期间资料基线已变化，拒绝覆盖")
        for name in changes:
            path = root / name
            snapshots[name] = path.read_bytes() if path.exists() else None
        if fingerprint(root) != batch["base_manifest_sha256"]:
            raise UpdateError("写入前基线已变化")
        journal = {"format": "atlas-update-journal/v1", "files": [
            {"path": name, "before": None if snapshots[name] is None else base64.b64encode(snapshots[name]).decode("ascii"),
             "after_sha256": digest_bytes(content.encode("utf-8"))} for name, content in changes.items()]}
        _validate_update_journal_paths(root, changes)
        _write_update_journal(lock, journal)
        retirement_token = lock_directory_token(
            lock, "知识更新",
            expected_object=retirement_token[0],
            expected_names={"journal.json"})
        if reference_snapshot(root) != baseline:
            raise UpdateError("保存恢复记录期间资料基线已变化，拒绝覆盖")
        for name, content in changes.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            written.append(name)  # Include a partially failed write in rollback.
            atomic_write(path, content)
        # Verify the final projection; failure restores the original bytes.
        if any((root / name).read_bytes() != text.encode("utf-8")
               for name, text in projections(
                   root, as_of=as_of, active_update_lock=True).items()):
            raise UpdateError("写入后派生内容不一致")
        _validate_update_project(root, as_of=as_of)
        completed = result
    except BaseException as original:
        # Catch user interrupts too; re-raise after restoring original bytes.
        errors = []
        for name in reversed(written):
            try:
                restore_bytes(root / name, snapshots[name])
            except BaseException as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        if errors:
            keep_journal = True
            raise UpdateError("回滚未完成，已保留锁和恢复记录；停止写入并执行受控恢复：" + "; ".join(errors)) from original
        raise
    finally:
        if not keep_journal:
            cleanup_pending = _retire_update_lock(
                root, lock, retirement_token)
    completed = dict(completed)
    completed["cleanup_pending"] = str(cleanup_pending) if cleanup_pending else None
    return completed


def recover(root: Path, *, writers_stopped=False, finalize_committed=False):
    if not writers_stopped:
        raise UpdateError("恢复前必须确认其他写入进程已停止")
    root = Path(root)
    try:
        with writer_lease(root):
            return _recover_locked(
                root, writers_stopped=writers_stopped,
                finalize_committed=finalize_committed)
    except WriterLockError as exc:
        raise UpdateError(str(exc)) from exc


def apply(root: Path, batch: dict, *, as_of=None) -> dict:
    root = Path(root)
    try:
        with writer_lease(root):
            return _apply_locked(root, batch, as_of=as_of)
    except WriterLockError as exc:
        raise UpdateError(str(exc)) from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("fingerprint", help="打印当前语料输入的运行时指纹")
    inspect = sub.add_parser("inspect", help="查看现有条目或来源及替换前内容指纹")
    inspect.add_argument("identifier")
    inspect.add_argument("--kind", choices=("entry", "source"), default="entry")
    get = sub.add_parser("discover", help="有网络时读取公开热门视频，生成待研究线索，不写知识库")
    get.add_argument("--pages", type=int, choices=(1, 2, 3), default=1)
    get.add_argument("--output", type=Path, help="可选：将发现线索另存到文件")
    check = sub.add_parser("preview", help="检查并预览来源核查后的增量提案，不修改知识库")
    check.add_argument("--batch", type=Path, required=True)
    write = sub.add_parser("apply", help="把通过预审的增量写入本地知识库")
    write.add_argument("--batch", type=Path, required=True)
    write.add_argument("--write", action="store_true", help="确认当前任务已经允许修改本地知识库")
    restore = sub.add_parser("recover", help="从保留的事务记录恢复失败的本地更新")
    restore.add_argument("--write", action="store_true")
    restore.add_argument("--acknowledge-stopped-writers", action="store_true")
    restore.add_argument("--finalize-committed", action="store_true")
    sub.add_parser("recovery-info", help="只读显示待恢复的知识更新状态")
    args = parser.parse_args(argv)
    try:
        if args.action == "fingerprint":
            output = {"base_manifest_sha256": fingerprint(), "version": load_catalogue()[2]["version"]}
        elif args.action == "inspect":
            entries, sources, _ = load_catalogue()
            if args.kind == "source":
                found = [sources[args.identifier]] if args.identifier in sources else []
            else:
                found = get_entries(entries, args.identifier)
            output = {"kind": args.kind, "total": len(found),
                      "results": [{"record": v, "before_sha256": digest_record(v)} for v in found]}
        elif args.action == "discover":
            out = args.output.resolve() if args.output else None
            if out:
                if out.is_relative_to(ROOT.resolve()):
                    raise UpdateError("发现线索不得保存到技能目录")
                if out.exists() or not out.parent.is_dir():
                    raise UpdateError("发现线索输出文件已存在或父目录不存在")
            try:
                output = discover(args.pages)
            except UpdateError as exc:
                if out:
                    failure = {"format": "bilibili-meme-atlas-discovery-failure/v1",
                               "collected_at": datetime.now(timezone.utc).isoformat(),
                               "status": "unavailable", "endpoint": POPULAR_URL,
                               "pages_requested": args.pages, "error": str(exc),
                               "interpretation": "发现未完成，不代表没有新梗；不重试或绕过拒绝"}
                    with out.open("x", encoding="utf-8") as handle:
                        handle.write(_json_file(failure))
                raise
            if out:
                with out.open("x", encoding="utf-8") as handle:
                    handle.write(_json_file(output))
        elif args.action == "recovery-info":
            output = recovery_info(ROOT)
        elif args.action == "recover":
            if not args.write:
                raise UpdateError("恢复须显式指定 --write")
            output = recover(
                ROOT, writers_stopped=args.acknowledge_stopped_writers,
                finalize_committed=args.finalize_committed)
        else:
            batch = read_proposal(args.batch)
            if args.action == "preview":
                output, _ = plan(ROOT, batch)
                output["mode"] = "preview"
            else:
                if not args.write:
                    raise UpdateError("仅在获授权修改知识库时指定 --write")
                output = apply(ROOT, batch)
                output["mode"] = "applied"
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (UpdateError, CatalogueError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"更新失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
