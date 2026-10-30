#!/usr/bin/env python3
"""Validate catalogue structure and cross-record evidence contracts offline."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
import ipaddress
from pathlib import Path
import re
import stat
import sys
import unicodedata
from urllib.parse import urlparse, unquote

sys.dont_write_bytecode = True

from atlas import (CATEGORIES, SHARDS, STATUS, ROOT, CatalogueError,
                   load_catalogue, load_json)

ENTRY_KEYS = {"id", "term", "aliases", "category", "era", "meaning", "origin", "usage",
              "lifecycle", "sensitivity", "relations", "evidence_status", "evidence", "notes"}
SOURCE_KEYS = {"id", "title", "url", "publisher", "source_type", "access_level", "checked_at", "supports", "limitations"}
CLAIMS = {"meaning", "origin", "date", "platform_usage"}
PRECISIONS = ("exact", "year", "range", "unknown")
BILIBILI_ROLES = ("origin", "adaptation", "amplifier", "imported", "uncertain")
LIFECYCLES = ("classic", "periodic", "contextual", "volatile")
SOURCE_TYPES = ("primary", "secondary", "community")
ACCESS_LEVELS = ("full", "snippet", "metadata")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
METADATA_KEYS = {
    "schema_version", "version", "knowledge_cutoff", "language",
    "coverage_start", "coverage_mode", "year_interpretation", "license_status",
    "license_scope", "third_party_rights",
}
METADATA_FIXED = {
    "schema_version": "1.0",
    "language": "zh-CN",
    "coverage_start": "2009",
    "coverage_mode": "curated-historical-catalogue",
    "year_interpretation": "era为索引时期，origin.date为其summary明确说明的起源节点；不得互换",
    "license_status": "MIT",
    "license_scope": "project-owned-material-only",
    "third_party_rights": "retained-by-respective-rightsholders",
}
INTERFACE_FIELDS = {"display_name", "short_description", "default_prompt", "icon_small", "icon_large"}
POLICY_FIELDS = {"allow_implicit_invocation"}
EXPECTED_INTERFACE = {
    "display_name": "B站热梗知识库",
    "short_description": "检索B站历史热梗与弹幕表达，核对出处、传播和适用语境",
    "default_prompt": "使用 $bilibili-meme-atlas 解释这些B站热梗的含义、出处和合适用法，并给出可核验来源。",
    "icon_small": "assets/icon.png",
    "icon_large": "assets/icon.png",
}
PROJECT_FILES = frozenset("""
.gitattributes .gitignore AGENT_INSTALL.md CODE_OF_CONDUCT.md CONTENT_RIGHTS.md CONTRIBUTING.md LICENSE README.md SECURITY.md SKILL.md
.github/dependabot.yml .github/ISSUE_TEMPLATE/bug.yml .github/ISSUE_TEMPLATE/entry.yml .github/pull_request_template.md
.github/workflows/local-lifecycle-verify.yml .github/workflows/release-byte-verify.yml .github/workflows/verify.yml
agents/openai.yaml assets/cover.webp assets/icon.png
docs/COMPATIBILITY.md docs/DEPENDENCIES.md docs/INSTALL.md docs/INSTALL.en.md docs/MAINTENANCE.md docs/README.en.md
docs/RELEASING.md docs/VERIFICATION.md
references/2009-2015.json references/2016-2020.json references/2021-2026.json
references/architecture.md references/catalog.md references/corpus-summary.json
references/coverage.md references/history-index.md references/history-map.md references/metadata.json
references/platform.md references/refresh.md references/research.md references/safety.md
references/schema.json references/sources.json references/sources.schema.json references/rights.json references/usage.md
scripts/atlas.py scripts/build.py scripts/history.py scripts/research.py scripts/update.py
scripts/install.py scripts/package.py scripts/release.py scripts/validate.py scripts/verify.py
scripts/writer_lock.py
tests/test_contracts.py tests/test_hardening.py tests/test_history.py
tests/test_install.py tests/test_package.py tests/test_project_contract.py tests/test_release.py
tests/test_research.py tests/test_transactions.py tests/test_update.py
""".split())
PROJECT_FILES_0_1_0 = PROJECT_FILES - {
    "AGENT_INSTALL.md", ".github/dependabot.yml",
}
PROJECT_DIRECTORIES = frozenset({
    ".github", ".github/ISSUE_TEMPLATE", ".github/workflows",
    "agents", "assets", "docs", "references", "scripts", "tests",
})
TRANSIENT_DIRECTORIES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
MAX_PROJECT_FILE_BYTES = 10 * 1024 * 1024


def root_git_token(root: Path):
    """Validate only the repository root's VCS management entry."""
    path = Path(root) / ".git"
    try:
        before = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"无法读取根 .git 入口: {exc}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise ValueError("根 .git 入口不得是符号链接")
    if stat.S_ISDIR(before.st_mode):
        return ("directory", before.st_dev, before.st_ino, before.st_mode)
    if not stat.S_ISREG(before.st_mode) or before.st_size > 4096:
        raise ValueError("根 .git 入口必须是管理目录或工作树管理文件")
    try:
        body = path.read_bytes()
        after = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"无法读取根 .git 管理文件: {exc}") from exc
    identity = (before.st_dev, before.st_ino, before.st_mode,
                before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_mode,
                      after.st_size, after.st_mtime_ns)
    if (identity != after_identity or len(body) != before.st_size
            or not re.fullmatch(br"gitdir: [^\x00\r\n]+(?:\r?\n)?", body)):
        raise ValueError("根 .git 工作树管理文件无效或在读取期间改变")
    return ("file", identity, body)


def project_paths(root: Path):
    """Walk the product tree without entering the root VCS management entry."""
    root = Path(root)
    stack = sorted(root.iterdir(), reverse=True)
    while stack:
        path = stack.pop()
        relative = path.relative_to(root)
        if relative.parts == (".git",):
            continue
        yield path
        if path.is_dir() and not path.is_symlink():
            stack.extend(sorted(path.iterdir(), reverse=True))


def allowed_project_path(relative: str) -> bool:
    if relative in PROJECT_FILES:
        return True
    match = re.fullmatch(r"references/([0-9]{4})-([0-9]{4})\.json", relative)
    if not match:
        return False
    start, end = map(int, match.groups())
    return start >= 2027 and (start - 2027) % 5 == 0 and end == start + 4


def validate_project_files(root: Path = ROOT, *, active_update_lock=False):
    """Keep the Skill boundary explicit without creating or comparing an archive."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return ["项目目录必须是普通目录，不能是符号链接"]
    errors = []
    try:
        root_git_token(root)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
    seen = set()
    try:
        paths = project_paths(root)
        paths = list(paths)
    except OSError as exc:
        errors.append(f"项目目录无法枚举: {exc}")
        paths = []
    for path in paths:
        relative_path = path.relative_to(root)
        relative = relative_path.as_posix()
        if (active_update_lock and relative_path.parts
                and relative_path.parts[0] == ".atlas-update.lock"):
            continue
        if path.is_symlink():
            errors.append(f"项目路径不能是符号链接: {relative}")
            continue
        if any(part in TRANSIENT_DIRECTORIES for part in relative_path.parts):
            if path.is_dir() and path.name in TRANSIENT_DIRECTORIES:
                errors.append(f"项目含缓存目录: {relative}")
            continue
        if path.is_dir():
            if relative not in PROJECT_DIRECTORIES:
                errors.append(f"项目含未登记目录: {relative}")
            continue
        if not path.is_file():
            errors.append(f"项目路径不是普通文件: {relative}")
            continue
        if not allowed_project_path(relative):
            errors.append(f"项目含未登记文件: {relative}")
            continue
        seen.add(relative)
        try:
            if path.stat().st_size > MAX_PROJECT_FILE_BYTES:
                errors.append(f"项目文件超过体积上限: {relative}")
        except OSError as exc:
            errors.append(f"项目文件无法读取属性: {relative}: {exc}")
    required = PROJECT_FILES
    if "references/metadata.json" in seen:
        try:
            metadata = load_json(root / "references/metadata.json")
        except (CatalogueError, OSError, ValueError):
            pass
        else:
            if isinstance(metadata, dict) and metadata.get("version") == "0.1.0":
                required = PROJECT_FILES_0_1_0
    missing = required - seen
    if missing:
        errors.append("项目缺少文件: " + ", ".join(sorted(missing)))
    if "LICENSE" in seen:
        try:
            if not (root / "LICENSE").read_bytes().startswith(b"MIT License\n"):
                errors.append("MIT 许可文件内容无效")
        except OSError as exc:
            errors.append(f"MIT 许可文件无法读取: {exc}")
    return errors


def string(value, label, errors, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        errors.append(f"{label}: 必须是{'可为空的' if empty else '非空'}字符串")
        return False
    good = True
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in value):
        errors.append(f"{label}: 不允许控制字符")
        good = False
    if any(c in value for c in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"):
        errors.append(f"{label}: 不允许双向隐藏控制符")
        good = False
    if any(unicodedata.category(c) == "Cs" for c in value):
        errors.append(f"{label}: 不允许无效 Unicode 代理字符")
        good = False
    return good


def strings(value, label, errors, *, nonempty=False):
    if not isinstance(value, list):
        errors.append(f"{label}: 必须是字符串数组")
        return False
    if nonempty and not value:
        errors.append(f"{label}: 至少包含一项")
        return False
    good = all([string(v, label, errors) for v in value])
    if good and len(value) != len(set(value)):
        errors.append(f"{label}: 数组含重复项")
        return False
    return good


def object_keys(obj, keys, label, errors, optional=frozenset()):
    if not isinstance(obj, dict):
        errors.append(f"{label}: 必须是对象")
        return False
    missing = keys - obj.keys()
    extra = obj.keys() - keys - optional
    if missing:
        errors.append(f"{label}: 缺字段 {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"{label}: 未定义字段 {', '.join(sorted(map(str, extra)))}")
    return not missing


def iso_date(value):
    # fromisoformat also accepts compact and week dates on newer Python releases.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("日期必须为YYYY-MM-DD")
    return date.fromisoformat(value)


def validation_date(as_of=None):
    """Return an explicit UTC catalogue validation date, injectable by tests."""
    if as_of is None:
        return datetime.now(timezone.utc).date()
    if type(as_of) is not date:
        raise ValueError("as_of 必须是日期")
    return as_of


def valid_url(value, label, errors):
    if not string(value, label, errors):
        return
    try:
        if any(c.isspace() for c in value) or "\\" in value:
            raise ValueError("URL不得包含空白或反斜杠")
        parsed = urlparse(value)
        host = (parsed.hostname or "").rstrip(".")
        if parsed.scheme not in ("http", "https") or not host or parsed.username is not None or parsed.password is not None:
            raise ValueError("须使用无凭据的公开HTTP(S)内容页")
        if host in {"localhost", "search.bilibili.com", "www.google.com", "www.bing.com"} or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError("本地地址或搜索页不能作证据来源")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError("来源不能是内网或保留地址")
        if "search" in unquote(parsed.path).casefold().strip("/").split("/"):
            raise ValueError("应登记具体内容页而非搜索列表")
        if parsed.port not in (None, 80, 443):
            raise ValueError("来源只使用标准HTTP(S)端口")
    except ValueError as exc:
        errors.append(f"{label}: {exc}")


def _string_schema(*, empty=False):
    value = {"type": "string"}
    if not empty:
        value["minLength"] = 1
    return value


def _string_array_schema(*, nonempty=False):
    value = {
        "type": "array",
        "items": _string_schema(),
        "uniqueItems": True,
    }
    if nonempty:
        value["minItems"] = 1
    return value


def _object_schema(properties, required):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def expected_entry_schema():
    origin = _object_schema({
        "summary": _string_schema(),
        "date": {"type": ["string", "null"]},
        "precision": {"enum": list(PRECISIONS)},
        "bilibili_role": {"enum": list(BILIBILI_ROLES)},
    }, ("summary", "date", "precision", "bilibili_role"))
    usage = _object_schema({
        "contexts": _string_array_schema(nonempty=True),
        "example": _string_schema(),
        "avoid": _string_array_schema(nonempty=True),
    }, ("contexts", "example", "avoid"))
    evidence_item = _object_schema({
        "source_id": _string_schema(),
        "claims": {
            "type": "array", "minItems": 1, "uniqueItems": True,
            "items": {"enum": sorted(CLAIMS)},
        },
        "note": _string_schema(),
    }, ("source_id", "claims", "note"))
    properties = {
        "id": {"type": "string", "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"},
        "term": _string_schema(),
        "aliases": _string_array_schema(),
        "category": {"enum": list(CATEGORIES)},
        "era": {
            "type": "string", "pattern": "^[0-9]{4}-[0-9]{4}$",
            "description": "2009-2015、2016-2020、2021-2026；2027年起连续五年窗口。实际时期由脚本交叉校验。",
        },
        "meaning": _string_schema(),
        "origin": origin,
        "usage": usage,
        "lifecycle": {"enum": list(LIFECYCLES)},
        "sensitivity": _string_array_schema(),
        "relations": _string_array_schema(),
        "evidence_status": {"enum": list(STATUS)},
        "evidence": {"type": "array", "items": evidence_item},
        "notes": _string_schema(empty=True),
        "tags": _string_array_schema(),
    }
    return {
        "title": "Bilibili Meme Atlas entries",
        "type": "array",
        "items": _object_schema(properties, tuple(ENTRY_KEYS)),
    }


def expected_source_schema():
    properties = {
        "id": {"type": "string", "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"},
        "title": _string_schema(),
        "url": {"type": "string", "format": "uri", "pattern": "^https?://"},
        "publisher": _string_schema(),
        "source_type": {"enum": list(SOURCE_TYPES)},
        "access_level": {"enum": list(ACCESS_LEVELS)},
        "checked_at": {"type": "string", "format": "date"},
        "supports": _string_schema(),
        "limitations": _string_schema(empty=True),
    }
    return {
        "title": "Bilibili Meme Atlas source registry",
        "type": "array",
        "items": _object_schema(properties, tuple(SOURCE_KEYS)),
    }


def _canonical_schema(value, parent_key=None):
    if isinstance(value, dict):
        return {key: _canonical_schema(item, key) for key, item in value.items()}
    if isinstance(value, list):
        items = [_canonical_schema(item) for item in value]
        if parent_key in {"enum", "required", "type"}:
            return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        return items
    return value


def _rights_source_record(source_id: str):
    return {
        "source_id": source_id,
        "external_rights": "retained-by-respective-rightsholders",
        "project_use": "source-metadata-and-project-authored-claim-support",
        "bundled_full_work": False,
        "relicensed_by_project": False,
    }


def expected_rights_registry(root: Path = ROOT, *, source_ids=None):
    """Build the project-owned asset identities and external-source use boundary."""
    root = Path(root)
    if source_ids is None:
        source_rows = load_json(root / "references" / "sources.json")
        if not isinstance(source_rows, list):
            raise CatalogueError("references/sources.json: 必须是数组")
        source_ids = [row.get("id") if isinstance(row, dict) else None
                      for row in source_rows]
    expected_assets = (
        ("assets/cover.webp", "cover"),
        ("assets/icon.png", "icon"),
    )
    assets = []
    for relative, kind in expected_assets:
        path = root / relative
        body = path.read_bytes()
        record = {
            "path": relative,
            "kind": kind,
            "sha256": hashlib.sha256(body).hexdigest(),
            "rights_status": "project-owned",
            "project_license": "MIT",
            "provenance": {
                "status": "project-controlled",
                "basis": "CONTENT_RIGHTS.md",
                "external_source": None,
            },
            "third_party_boundary": (
                "independent names or marks, if present, retain their own rights"),
        }
        assets.append(record)
    return {
        "schema_version": "1.0",
        "project_license": "MIT",
        "project_license_scope": "project-owned-material-only",
        "external_rights_policy": "retained-by-respective-rightsholders",
        "assets": assets,
        "sources": [_rights_source_record(source_id) for source_id in source_ids],
    }


def validate_rights_registry(root: Path = ROOT):
    root = Path(root)
    try:
        observed = load_json(root / "references" / "rights.json")
        expected = expected_rights_registry(root)
    except (CatalogueError, OSError, UnicodeError) as exc:
        return [f"references/rights.json: 无法核对权利登记: {exc}"]
    if observed != expected:
        return ["references/rights.json: 与当前项目资源、来源身份或权利边界不一致"]
    return []


def validate_schema_contracts(root: Path = ROOT):
    errors = []
    root = Path(root)
    for name, expected in (
        ("schema.json", expected_entry_schema()),
        ("sources.schema.json", expected_source_schema()),
    ):
        try:
            observed = load_json(root / "references" / name)
        except CatalogueError as exc:
            errors.append(f"references/{name}: {exc}")
            continue
        if _canonical_schema(observed) != _canonical_schema(expected):
            errors.append(f"references/{name}: 与运行时字段、约束或枚举合同不一致")
    return errors


def validate_records(entries, sources, metadata, *, allowed_eras=None,
                     as_of=None):
    allowed_eras = tuple(s[:-5] for s in SHARDS) if allowed_eras is None else tuple(allowed_eras)
    as_of = validation_date(as_of)
    errors = []
    if not isinstance(entries, list):
        errors.append("entries: 必须是数组")
        entries = []
    if not isinstance(sources, dict):
        errors.append("sources: 必须是以来源ID为键的对象")
        sources = {}
    if not isinstance(metadata, dict):
        errors.append("metadata: 必须是对象")
        metadata = {}
    object_keys(metadata, METADATA_KEYS, "metadata", errors)
    version = metadata.get("version")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        errors.append("metadata.version: 必须是三段数字版本")
    for key, expected in METADATA_FIXED.items():
        if metadata.get(key) != expected:
            errors.append(f"metadata.{key}: 与项目合同不一致")
    cutoff_text = metadata.get("knowledge_cutoff", "")
    try:
        cutoff = iso_date(cutoff_text)
        if cutoff > as_of:
            errors.append("metadata.knowledge_cutoff: 资料截止日不得晚于当前UTC日期")
    except ValueError:
        errors.append("metadata.knowledge_cutoff: 日期必须为有效YYYY-MM-DD")
        cutoff = date.max
    ids = set()
    for source_id, s in sources.items():
        p = f"source:{source_id}"
        if not object_keys(s, SOURCE_KEYS, p, errors):
            continue
        for key in ("id", "title", "publisher", "supports", "limitations"):
            string(s[key], p + "." + key, errors, empty=key == "limitations")
        if not isinstance(s["id"], str) or s["id"] != source_id or not SLUG.fullmatch(s["id"]):
            errors.append(f"{p}: 来源ID无效")
        valid_url(s["url"], p + ".url", errors)
        if s["source_type"] not in SOURCE_TYPES:
            errors.append(f"{p}: 来源关系无效")
        if s["access_level"] not in ACCESS_LEVELS:
            errors.append(f"{p}: 读取范围无效")
        try:
            checked = iso_date(s["checked_at"])
            if checked > cutoff:
                errors.append(f"{p}: 核查日期晚于资料截止日")
            if checked > as_of:
                errors.append(f"{p}: 核查日期晚于当前UTC日期")
        except ValueError:
            errors.append(f"{p}: 核查日期必须为有效YYYY-MM-DD")
        if s["access_level"] != "full" and isinstance(s["limitations"], str) and not s["limitations"].strip():
            errors.append(f"{p}: 非全文来源须注明读取局限")
    for e in entries:
        p = f"entry:{e.get('id', '?')}" if isinstance(e, dict) else "entry"
        if not object_keys(e, ENTRY_KEYS, p, errors, {"tags"}):
            continue
        for key in ("id", "term", "meaning", "notes"):
            string(e[key], p + "." + key, errors, empty=key == "notes")
        if not isinstance(e["id"], str) or not SLUG.fullmatch(e["id"]):
            errors.append(f"{p}: ID必须为小写英文、数字和连字符")
        elif e["id"] in ids:
            errors.append(f"{p}: 重复ID")
        else:
            ids.add(e["id"])
        for key in ("aliases", "sensitivity", "relations"):
            strings(e[key], p + "." + key, errors)
        if "tags" in e:
            strings(e["tags"], p + ".tags", errors)
        if e["category"] not in CATEGORIES:
            errors.append(f"{p}: 无效分类")
        if e["era"] not in allowed_eras:
            errors.append(f"{p}: 无效时期")
        elif isinstance(e["era"], str):
            era_match = re.fullmatch(r"([0-9]{4})-([0-9]{4})", e["era"])
            if (not era_match
                    or int(era_match.group(1)) > cutoff.year):
                errors.append(f"{p}: 时期起年晚于资料截止年份")
        if e["evidence_status"] not in STATUS:
            errors.append(f"{p}: 无效证据状态")
        if e["lifecycle"] not in LIFECYCLES:
            errors.append(f"{p}: 无效生命周期")
        origin = e["origin"]
        if object_keys(origin, {"summary", "date", "precision", "bilibili_role"}, p + ".origin", errors):
            string(origin["summary"], p + ".origin.summary", errors)
            if origin["bilibili_role"] not in BILIBILI_ROLES:
                errors.append(f"{p}: 无效B站传播角色")
            precision, d = origin["precision"], origin["date"]
            if precision not in PRECISIONS:
                errors.append(f"{p}: 无效日期精度")
            if precision == "unknown":
                if d is not None:
                    errors.append(f"{p}: 未知日期必须为null")
            elif not string(d, p + ".origin.date", errors):
                pass
            elif precision == "exact":
                try:
                    if iso_date(d) > cutoff:
                        errors.append(f"{p}: 起源晚于资料截止日")
                except ValueError:
                    errors.append(f"{p}: 精确日期必须为YYYY-MM-DD")
            elif precision == "year":
                if not re.fullmatch(r"[12][0-9]{3}", d):
                    errors.append(f"{p}: 年份必须为YYYY")
                elif int(d) > cutoff.year:
                    errors.append(f"{p}: 年份晚于资料截止日")
            elif precision == "range":
                years = [int(y) for y in re.findall(r"(?<![0-9])([12][0-9]{3})(?![0-9])", d)]
                if len(years) != 2:
                    errors.append(f"{p}: 日期区间须包含两个年份")
                else:
                    if years[0] > years[1]:
                        errors.append(f"{p}: 日期区间起始年份晚于结束年份")
                    if max(years) > cutoff.year:
                        errors.append(f"{p}: 年份晚于资料截止日")
        usage = e["usage"]
        if object_keys(usage, {"contexts", "example", "avoid"}, p + ".usage", errors):
            strings(usage["contexts"], p + ".usage.contexts", errors, nonempty=True)
            string(usage["example"], p + ".usage.example", errors)
            strings(usage["avoid"], p + ".usage.avoid", errors, nonempty=True)
        evidence = e["evidence"]
        if not isinstance(evidence, list):
            errors.append(f"{p}: evidence必须为数组")
            continue
        if e["evidence_status"] != "candidate" and not evidence:
            errors.append(f"{p}: 非候选词条必须有证据")
        if e["evidence_status"] != "verified" and isinstance(e["notes"], str) and not e["notes"].strip():
            errors.append(f"{p}: 未全核词条必须注明具体缺口")
        full_claims = set()
        for ref in evidence:
            if not object_keys(ref, {"source_id", "claims", "note"}, p + ".evidence", errors):
                continue
            string(ref["note"], p + ".evidence.note", errors)
            sid = ref["source_id"]
            if not isinstance(sid, str) or sid not in sources:
                errors.append(f"{p}: 悬空来源引用")
                continue
            if strings(ref["claims"], p + ".evidence.claims", errors, nonempty=True):
                if set(ref["claims"]) - CLAIMS:
                    errors.append(f"{p}: 未定义的证据命题")
                if isinstance(sources[sid], dict) and sources[sid].get("access_level") == "full":
                    full_claims.update(ref["claims"])
        if e["evidence_status"] == "partial":
            if not any(isinstance(ref, dict) and isinstance(ref.get("claims"), list)
                       and "meaning" in ref["claims"]
                       and isinstance(ref.get("source_id"), str)
                       and isinstance(sources.get(ref.get("source_id")), dict)
                       and sources[ref["source_id"]].get("access_level") in ("full", "snippet")
                       for ref in evidence):
                errors.append(f"{p}: partial缺非元数据释义依据；须补证或保留为candidate")
        if e["evidence_status"] == "verified":
            required = {"meaning", "origin", "platform_usage"}
            if isinstance(origin, dict) and origin.get("date") is not None:
                required.add("date")
            if required - full_claims:
                errors.append(f"{p}: verified缺全文支持命题 {sorted(required - full_claims)}")
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("relations"), list):
            for target in e["relations"]:
                if not isinstance(target, str) or target not in ids or target == e.get("id"):
                    errors.append(f"entry:{e.get('id')}: 无效关联 {target}")
    return errors


def validate_skill(root: Path = ROOT):
    errors = []
    root = Path(root)
    if root.is_symlink() or (root / "SKILL.md").is_symlink():
        return ["SKILL.md: 不接受符号链接入口"]
    try:
        skill = (root / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"SKILL.md: 无法读取 {exc}"]
    front = re.match(r"\A---\n(.*?)\n---\n", skill, re.S)
    if not front:
        errors.append("SKILL.md: 缺少YAML frontmatter")
    else:
        fields = {}
        for line in front[1].splitlines():
            match = re.fullmatch(r"([a-z-]+): (.+)", line)
            if not match or match[1] in fields:
                errors.append("SKILL.md: frontmatter须为唯一单行标量字段")
                continue
            key, value = match.groups()
            if key in {"description", "compatibility", "license"} and value[:1] not in {'"', "'"}:
                errors.append(f"SKILL.md: {key} 须使用单行引号字符串")
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except ValueError:
                    errors.append(f"SKILL.md: {key} 引号无效")
            elif value.startswith("'"):
                if len(value) < 2 or not value.endswith("'") or "'" in value[1:-1].replace("''", ""):
                    errors.append(f"SKILL.md: {key} 引号无效")
                else:
                    value = value[1:-1].replace("''", "'")
            elif (value[:1] in "[{#&*!|>@`%?" or ": " in value or " #" in value
                  or value.casefold() in {"null", "~", "true", "false", "yes", "no", "on", "off", ".nan", ".inf", "-.inf"}
                  or re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value)
                  or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
                errors.append(f"SKILL.md: {key} 须为字符串标量；请使用双引号")
            fields[key] = value
        if set(fields) != {"name", "description"}:
            errors.append("SKILL.md: frontmatter须恰好包含name与description")
        name, desc = fields.get("name"), fields.get("description")
        if not isinstance(name, str) or len(name) > 64 or not SLUG.fullmatch(name) or name != "bilibili-meme-atlas":
            errors.append("SKILL.md: 名称不符合技能命名约定")
        if not isinstance(desc, str) or not 1 <= len(desc.strip()) <= 1024:
            errors.append("SKILL.md: description须为1–1024字符的字符串")
    if len(skill.splitlines()) >= 500:
        errors.append("SKILL.md: 主入口应少于500行")
    for target in re.findall(r"\]\(([^)]+)\)", skill):
        if "://" in target or target.startswith("#"):
            continue
        try:
            destination = (root / unquote(target.split("#")[0])).resolve()
            relative = unquote(target.split("#")[0])
            components = Path(relative).parts
            valid = (not Path(relative).is_absolute() and ".." not in components
                     and "\\" not in relative and destination.is_relative_to(root.resolve())
                     and destination.is_file()
                     and not any((root.joinpath(*components[:i])).is_symlink() for i in range(1, len(components)+1)))
        except (OSError, RuntimeError, ValueError):
            valid = False
        if not valid:
            errors.append(f"SKILL.md: 引用缺失或越出技能目录 {target}")
    return errors


def _interface_scalar(raw, label, errors):
    """Parse one canonical double-quoted interface string."""
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        errors.append(f"agents/openai.yaml: {label} 必须是非空字符串标量")
        return None
    if not raw.startswith('"'):
        errors.append(f"agents/openai.yaml: {label} 须使用双引号字符串")
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if not isinstance(value, str) or not value.strip():
        errors.append(f"agents/openai.yaml: {label} 双引号字符串无效")
        return None
    return value


def _parse_interface_yaml(lines, errors):
    """Parse the deliberately small agents/openai.yaml schema without a YAML dependency."""
    sections = {}
    current = None
    for number, line in enumerate(lines, 1):
        heading = re.fullmatch(r"([a-z_]+):", line)
        if heading:
            current = heading[1]
            if current in sections:
                errors.append(f"agents/openai.yaml: 重复区段 {current}")
            else:
                sections[current] = {}
            continue
        item = re.fullmatch(r"  ([a-z_]+):(.*)", line)
        if not item or current is None:
            errors.append(f"agents/openai.yaml:{number}: 只接受已定义的映射")
            continue
        key, suffix = item.groups()
        if key in sections[current]:
            errors.append(f"agents/openai.yaml: 重复字段 {current}.{key}")
            continue
        if not suffix.startswith(" ") or len(suffix) == 1:
            errors.append(f"agents/openai.yaml:{number}: 字段值无效")
            sections[current][key] = None
            continue
        raw = suffix[1:]
        sections[current][key] = raw
    return sections


def validate_interface(root: Path = ROOT):
    errors = []
    root = Path(root)
    path = root / "agents" / "openai.yaml"
    if path.is_symlink():
        return ["agents/openai.yaml: 不接受符号链接"]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [f"agents/openai.yaml: 无法读取 {exc}"]
    sections = _parse_interface_yaml(lines, errors)
    if set(sections) != {"interface", "policy"}:
        errors.append("agents/openai.yaml: 只允许 interface 与 policy 区段")
    raw_interface = sections.get("interface", {})
    if set(raw_interface) != INTERFACE_FIELDS:
        errors.append("agents/openai.yaml: interface 必须恰含五个项目字段")
    interface = {}
    for key, raw in raw_interface.items():
        value = _interface_scalar(raw, f"interface.{key}", errors)
        if value is not None:
            interface[key] = value
    for key in ("display_name", "short_description", "default_prompt"):
        value = interface.get(key)
        if isinstance(value, str) and value != EXPECTED_INTERFACE[key]:
            errors.append(f"agents/openai.yaml: interface.{key} 与项目合同不一致")
    for key in ("icon_small", "icon_large"):
        value = interface.get(key)
        if not isinstance(value, str):
            continue
        relative = value[2:] if value.startswith("./") else value
        parts = Path(relative).parts
        try:
            target = (root / relative).resolve()
            valid = (value == "./assets/icon.png"
                     and relative == EXPECTED_INTERFACE[key]
                     and not Path(relative).is_absolute()
                     and ".." not in parts and target.is_relative_to(root.resolve())
                     and target.is_file() and not target.is_symlink()
                     and not any((root.joinpath(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)))
        except (OSError, RuntimeError, ValueError):
            valid = False
        if not valid:
            errors.append(f"agents/openai.yaml: {key} 必须指向技能内 assets/icon.png 普通资源文件")
    policy = sections.get("policy", {})
    if set(policy) != POLICY_FIELDS:
        errors.append("agents/openai.yaml: policy 须恰好包含allow_implicit_invocation")
    if policy.get("allow_implicit_invocation") != "true":
        errors.append("agents/openai.yaml: 必须启用隐式调用")
    return errors


def validate_all(root: Path = ROOT, *, as_of=None, active_update_lock=False,
                 _snapshot=None):
    try:
        if _snapshot is None:
            load_catalogue(root, as_of=as_of,
                           active_update_lock=active_update_lock)
        else:
            failures = validate_records(
                _snapshot.entries, _snapshot.sources, _snapshot.metadata,
                allowed_eras=tuple(name[:-5] for name in _snapshot.shards),
                as_of=as_of)
            if failures:
                raise CatalogueError("数据契约校验失败：\n" + "\n".join(failures))
        errors = []
    except CatalogueError as exc:
        errors = [str(exc)]
    return (errors + validate_schema_contracts(root)
            + validate_rights_registry(root)
            + validate_skill(root) + validate_interface(root)
            + validate_project_files(
                root, active_update_lock=active_update_lock))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    failures = validate_all()
    for failure in failures:
        print(failure)
    print(f"validation: {'FAIL' if failures else 'PASS'}")
    raise SystemExit(bool(failures))
