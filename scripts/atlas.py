#!/usr/bin/env python3
"""Search a local, provenance-aware Bilibili meme catalogue."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import stat
import sys
import unicodedata

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SHARDS = ("2009-2015.json", "2016-2020.json", "2021-2026.json")
FUTURE_SHARD = re.compile(r"([0-9]{4})-([0-9]{4})\.json\Z")
CATEGORIES = ("community", "acg", "guichu", "game", "live", "music", "film", "life", "tech", "esports", "vtuber")
STATUS = ("verified", "partial", "candidate")
LABELS = {"verified": "已核实", "partial": "部分核实", "candidate": "待补证"}


class CatalogueError(ValueError):
    pass


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(c for c in value if not c.isspace() and not unicodedata.category(c).startswith("P"))


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 对象字段重复: {key}")
        result[key] = value
    return result


def _json_constant(value):
    raise ValueError(f"不允许非标准 JSON 数值: {value}")


def _json_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"JSON 数值超过有限浮点范围: {value}")
    return number


def strict_json_loads(payload, *, max_depth=None, max_nodes=None):
    """Decode UTF-8 JSON with duplicate, constant, size-shape and Unicode checks."""
    if isinstance(payload, (bytes, bytearray)):
        payload = bytes(payload).decode("utf-8")
    elif not isinstance(payload, str):
        raise TypeError("JSON 输入必须是 UTF-8 字节或文本")
    value = json.loads(payload, object_pairs_hook=_json_object,
                       parse_constant=_json_constant, parse_float=_json_float)
    if max_depth is None and max_nodes is None:
        return value
    if (max_depth is not None
            and (type(max_depth) is not int or max_depth < 0)):
        raise ValueError("JSON 深度上限无效")
    if (max_nodes is not None
            and (type(max_nodes) is not int or max_nodes < 1)):
        raise ValueError("JSON 节点上限无效")
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if ((max_depth is not None and depth > max_depth)
                or (max_nodes is not None and nodes > max_nodes)):
            raise ValueError("JSON 嵌套或节点数量超过安全上限")
        if isinstance(item, dict):
            for key, child in item.items():
                key.encode("utf-8")
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            item.encode("utf-8")
    return value


def load_json(path: Path):
    try:
        return strict_json_loads(path.read_bytes())
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise CatalogueError(f"无法读取知识库文件 {path.name}: {exc}") from exc


def era_for_year(year: int) -> str:
    """Return the index window for a spread year, not a claim of first origin."""
    if not isinstance(year, int) or not 2009 <= year <= 9999:
        raise CatalogueError("时期年份无效")
    if year <= 2015:
        return "2009-2015"
    if year <= 2020:
        return "2016-2020"
    if year <= 2026:
        return "2021-2026"
    first = 2027 + (year - 2027) // 5 * 5
    return f"{first}-{first + 4}"


def shard_files(root: Path = ROOT) -> tuple[str, ...]:
    """List known partitions; require consecutive 5-year windows after 2026."""
    reference = Path(root) / "references"
    for name in SHARDS:
        if not (reference / name).is_file():
            raise CatalogueError(f"缺少知识库分片: {name}")
    later = []
    for path in reference.glob("[0-9]*-[0-9]*.json"):
        if path.name in SHARDS:
            continue
        match = FUTURE_SHARD.fullmatch(path.name)
        if not match:
            raise CatalogueError(f"未知时期分片: {path.name}")
        start, end = map(int, match.groups())
        if start < 2027 or end != start + 4 or (start - 2027) % 5:
            raise CatalogueError(f"时期分片范围无效: {path.name}")
        later.append((start, path.name))
    later.sort()
    for index, (start, name) in enumerate(later):
        if start != 2027 + index * 5:
            raise CatalogueError(f"时期分片不连续: {name}")
    return SHARDS + tuple(name for _, name in later)


@dataclass(frozen=True)
class _CatalogueSnapshot:
    entries: list
    sources: dict
    metadata: dict
    shards: tuple[str, ...]


def _catalogue_path_identity(path):
    value = path.stat(follow_symlinks=False)
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _check_catalogue_update_lock(root, active_update_lock, expected_token):
    lock = root / ".atlas-update.lock"
    if expected_token is not None:
        from writer_lock import WriterLockError, lock_directory_token
        try:
            observed = lock_directory_token(
                lock, "知识更新", expected_names={"journal.json"})
        except WriterLockError as exc:
            raise CatalogueError("知识更新恢复记录在读取期间发生变化") from exc
        if observed != expected_token:
            raise CatalogueError("知识更新恢复记录在读取期间发生变化")
    elif not active_update_lock and (lock.exists() or lock.is_symlink()):
        raise CatalogueError("知识库正在更新或等待恢复，暂不可查询")


def _capture_catalogue(root, *, active_update_lock=False,
                       _update_lock_token=None):
    if type(active_update_lock) is not bool:
        raise CatalogueError("更新读取状态必须为布尔值")
    reference = root / "references"
    if root.is_symlink() or reference.is_symlink():
        raise CatalogueError("知识库目录不能是符号链接")
    try:
        root_before = _catalogue_path_identity(root)
        reference_before = _catalogue_path_identity(reference)
        if (not stat.S_ISDIR(root_before[2])
                or not stat.S_ISDIR(reference_before[2])):
            raise CatalogueError("知识库目录必须是普通目录")
        _check_catalogue_update_lock(root, active_update_lock, _update_lock_token)
        for path in reference.rglob("*"):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise CatalogueError(f"参考资料包含符号链接或特殊文件: {path.name}")
        names = shard_files(root)
        inputs = (*names, "sources.json", "metadata.json")
        before = {name: _catalogue_path_identity(reference / name)
                  for name in inputs}
        if any(not stat.S_ISREG(identity[2]) for identity in before.values()):
            raise CatalogueError("知识库输入必须是普通文件")
        captured = {}
        for name in inputs:
            path = reference / name
            body = path.read_bytes()
            if (_catalogue_path_identity(path) != before[name]
                    or len(body) != before[name][3]):
                raise CatalogueError("知识库在读取期间发生变化，请重试")
            captured[name] = body
        after = {name: _catalogue_path_identity(reference / name)
                 for name in inputs}
        if (names != shard_files(root) or before != after
                or root_before != _catalogue_path_identity(root)
                or reference_before != _catalogue_path_identity(reference)):
            raise CatalogueError("知识库在读取期间发生变化，请重试")
        _check_catalogue_update_lock(root, active_update_lock, _update_lock_token)
    except OSError as exc:
        raise CatalogueError(f"知识库在读取期间发生变化或无法读取: {exc}") from exc
    return names, captured


def _load_catalogue_snapshot(root: Path = ROOT, *, as_of=None,
                             active_update_lock=False, _update_lock_token=None):
    root = Path(root)
    names, captured = _capture_catalogue(
        root, active_update_lock=active_update_lock,
        _update_lock_token=_update_lock_token)

    def decoded(name):
        try:
            return strict_json_loads(captured[name])
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise CatalogueError(f"无法读取知识库文件 {name}: {exc}") from exc

    entries = []
    locations = []
    for shard in names:
        rows = decoded(shard)
        if not isinstance(rows, list):
            raise CatalogueError(f"{shard} 必须是数组")
        entries.extend(rows)
        locations.extend((row, shard) for row in rows)
    rows = decoded("sources.json")
    if not isinstance(rows, list):
        raise CatalogueError("sources.json 必须是数组")
    sources = {}
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise CatalogueError("来源记录缺少有效 ID")
        if row["id"] in sources:
            raise CatalogueError(f"来源 ID 重复: {row['id']}")
        sources[row["id"]] = row
    for row in entries:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise CatalogueError("词条缺少有效 ID")
        if row["id"] in ids:
            raise CatalogueError(f"词条 ID 重复: {row['id']}")
        ids.add(row["id"])
    metadata = decoded("metadata.json")
    # Import after this module is initialized; the validator shares these constants.
    from validate import validate_records
    errors = validate_records(
        entries, sources, metadata,
        allowed_eras=tuple(s[:-5] for s in names),
        as_of=as_of)
    for row, shard in locations:
        if row.get("era") != shard[:-5]:
            errors.append(f"{row['id']}: era 与所在分片 {shard} 不一致")
    if errors:
        raise CatalogueError("数据契约校验失败：\n" + "\n".join(errors))
    return _CatalogueSnapshot(entries, sources, metadata, names)


def load_catalogue(root: Path = ROOT, *, as_of=None, active_update_lock=False):
    snapshot = _load_catalogue_snapshot(
        root, as_of=as_of, active_update_lock=active_update_lock)
    return snapshot.entries, snapshot.sources, snapshot.metadata


def _validate_query(value):
    if not isinstance(value, str):
        raise CatalogueError("查询内容必须是字符串")
    if len(value) > 1000:
        raise CatalogueError("查询内容请控制在 1000 个字符以内")
    if any(unicodedata.category(c) == "Cs" for c in value):
        raise CatalogueError("查询内容包含无效 Unicode 字符")
    if not normalize(value):
        raise CatalogueError("查询内容不能为空或只有标点")


def match_score(entry: dict, query: str):
    q = normalize(query)
    if not q:
        return 0, "none"
    term = normalize(entry["term"])
    aliases = [normalize(a) for a in entry["aliases"]]
    if q == normalize(entry["id"]) or q == term:
        return 1000, "exact"
    if q in aliases:
        return 950, "alias"
    names = [term, *aliases]
    if any(q in name for name in names):
        return 700, "name_contains"
    # Avoid one-character slang swallowing unrelated sentences.
    embedded = [name for name in names if len(name) >= 2 and name in q]
    if embedded:
        return 600 + min(max(map(len, embedded)), 80), "embedded_name"
    fields = normalize(" ".join([entry["meaning"], entry["origin"]["summary"],
                                  *entry["usage"]["contexts"], *entry.get("tags", [])]))
    tokens = [normalize(t) for t in re.split(r"[\s,，;；/]+", query)]
    tokens = [t for t in tokens if t]
    if tokens and all(t in fields for t in tokens):
        return 300 + min(sum(map(len, tokens)), 80), "keywords"
    return 0, "none"


def search(entries, query, *, category=None, era=None, evidence=None,
           include_candidates=False, limit=8, offset=0):
    _validate_query(query)
    if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 50 or offset < 0:
        raise CatalogueError("limit 必须为 1–50 之间的整数，offset 必须为非负整数")
    for value, choices, label in ((category, CATEGORIES, "category"),
                                  (era, tuple(dict.fromkeys([*(name[:-5] for name in SHARDS), *(e["era"] for e in entries)])), "era"),
                                  (evidence, STATUS, "evidence")):
        if value is not None and value not in choices:
            raise CatalogueError(f"无效的 {label} 筛选条件")
    if not isinstance(include_candidates, bool):
        raise CatalogueError("include_candidates 必须为布尔值")
    matches = []
    candidate_matches = 0
    for entry in entries:
        if category and entry["category"] != category:
            continue
        if era and entry["era"] != era:
            continue
        if evidence and entry["evidence_status"] != evidence:
            continue
        score, kind = match_score(entry, query)
        if not score:
            continue
        if entry["evidence_status"] == "candidate" and not (include_candidates or evidence == "candidate"):
            candidate_matches += 1
            continue
        matches.append((score, kind, entry))
    matches.sort(key=lambda item: (-item[0], STATUS.index(item[2]["evidence_status"]), item[2]["id"]))
    return {"query": query, "total": len(matches), "offset": offset, "limit": limit,
            "has_more": offset + limit < len(matches),
            "candidate_matches_excluded": candidate_matches,
            "results": [dict(entry=row, match_type=kind, relevance_score=score)
                        for score, kind, row in matches[offset:offset + limit]]}


def get_entries(entries, identifier):
    _validate_query(identifier)
    q = normalize(identifier)
    return [e for e in entries if q in [normalize(e["id"]), normalize(e["term"]),
                                       *(normalize(a) for a in e["aliases"])]]


def attach_sources(result, sources):
    used = {ref["source_id"] for item in result["results"] for ref in item["entry"]["evidence"]}
    result["sources"] = [sources[sid] for sid in sorted(used)]
    return result


def stats(entries, sources, metadata):
    return {"version": metadata["version"], "knowledge_cutoff": metadata["knowledge_cutoff"],
            "entries": len(entries), "sources": len(sources),
            "by_evidence": dict(sorted(Counter(e["evidence_status"] for e in entries).items())),
            "by_era": dict(sorted(Counter(e["era"] for e in entries).items())),
            "by_category": dict(sorted(Counter(e["category"] for e in entries).items())),
            "source_access": dict(sorted(Counter(s["access_level"] for s in sources.values()).items())),
            "coverage": "按年代与分区组织的收录集；未建立全站历史梗总量，不计算全量覆盖率"}


def markdown(result):
    if "results" not in result:
        return "```json\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n```\n"
    lines = []
    if "version" in result and "knowledge_cutoff" in result:
        lines += [f"资料版本：{result['version']}；资料截止日：{result['knowledge_cutoff']}。", ""]
    lines += [f"找到 {result['total']} 项，本页显示 {len(result['results'])} 项。", ""]
    source_map = {s["id"]: s for s in result.get("sources", [])}
    for item in result["results"]:
        e = item["entry"]
        lines += [f"## {e['term']}", "", e["meaning"], "",
                  f"**出处：** {e['origin']['summary']}",
                  f"**证据：** {LABELS[e['evidence_status']]}；索引时期 {e['era']}。", "",
                  f"**适用场景：** {'；'.join(e['usage']['contexts'])}",
                  f"**例句：** {e['usage']['example']}",
                  f"**使用边界：** {'；'.join(e['usage']['avoid'])}", ""]
        if e.get("notes"):
            lines += [f"**资料说明：** {e['notes']}", ""]
        for ref in e["evidence"]:
            s = source_map[ref["source_id"]]
            lines += [f"- [{s['title']}]({s['url']})：{ref['note']}（{s['access_level']}）"]
        lines.append("")
    if result.get("candidate_matches_excluded"):
        lines += [f"另有 {result['candidate_matches_excluded']} 项待补证命中；使用 --include-candidates 查看。", ""]
    if result.get("has_more"):
        lines += [f"下一页：--offset {result['offset'] + result['limit']}", ""]
    return "\n".join(lines)


def bounded_int(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("请输入整数") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("不得为负数")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    lookup = sub.add_parser("search", help="按词条、别名与关键词检索")
    lookup.add_argument("query")
    lookup.add_argument("--category", choices=CATEGORIES)
    lookup.add_argument("--era")
    lookup.add_argument("--evidence", choices=STATUS)
    lookup.add_argument("--include-candidates", action="store_true")
    lookup.add_argument("--limit", type=bounded_int, default=8)
    lookup.add_argument("--offset", type=bounded_int, default=0)
    lookup.add_argument("--format", choices=("json", "markdown"), default="json")
    get = sub.add_parser("get", help="读取 ID、词条名或别名的完整记录")
    get.add_argument("identifier")
    get.add_argument("--format", choices=("json", "markdown"), default="json")
    summary = sub.add_parser("stats", help="显示真实收录统计")
    summary.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    try:
        entries, sources, metadata = load_catalogue()
        if args.command == "search":
            result = search(entries, args.query, category=args.category, era=args.era,
                            evidence=args.evidence, include_candidates=args.include_candidates,
                            limit=args.limit, offset=args.offset)
            attach_sources(result, sources)
        elif args.command == "get":
            found = get_entries(entries, args.identifier)
            result = {"query": args.identifier, "total": len(found), "ambiguous": len(found) > 1,
                      "results": [{"entry": e, "match_type": "exact"} for e in found]}
            attach_sources(result, sources)
        else:
            result = stats(entries, sources, metadata)
        if args.command in ("search", "get"):
            result["version"] = metadata["version"]
            result["knowledge_cutoff"] = metadata["knowledge_cutoff"]
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(markdown(result) if args.format == "markdown" else json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CatalogueError, KeyError, TypeError) as exc:
        print(f"知识库查询失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
