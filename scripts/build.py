#!/usr/bin/env python3
"""Build deterministic catalogue and coverage projections from checked data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True

from atlas import ROOT, LABELS, CATEGORIES, _load_catalogue_snapshot, stats
from validate import validate_records

NAMES = {"community": "社区/弹幕", "acg": "ACG", "guichu": "鬼畜", "game": "游戏",
         "live": "直播", "music": "音乐", "film": "影视", "life": "生活/跨圈",
         "tech": "科技/知识", "esports": "电竞", "vtuber": "虚拟主播"}


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def projections(root: Path = ROOT, *, as_of=None, active_update_lock=False,
                _snapshot=None):
    snapshot = _snapshot if _snapshot is not None else _load_catalogue_snapshot(
        root, as_of=as_of, active_update_lock=active_update_lock)
    entries, sources, metadata = snapshot.entries, snapshot.sources, snapshot.metadata
    names = snapshot.shards
    errors = validate_records(
        entries, sources, metadata,
        allowed_eras=tuple(name[:-5] for name in names), as_of=as_of)
    if errors:
        raise ValueError("\n".join(errors))
    catalog = ["# 词条目录", "", "通过条目 ID 或名称读取完整记录；表中的时期是索引分组，不是诞生年份。", "", "## 目录", ""]
    for shard in names:
        era = shard[:-5]
        catalog.append(f"- [{era}](#{era})")
    catalog.append("")
    for shard in names:
        era = shard[:-5]
        catalog += [f"## {era}", "", f"完整记录：[{shard}]({shard})。", "",
                    "| 词条 | ID | 分类 | 证据 | 简义 |", "| --- | --- | --- | --- | --- |"]
        for e in sorted((e for e in entries if e["era"] == era), key=lambda e: e["id"]):
            catalog.append("| " + " | ".join(map(cell, [e["term"], e["id"], NAMES[e["category"]], LABELS[e["evidence_status"]], e["meaning"]])) + " |")
        catalog.append("")
    info = stats(entries, sources, metadata)
    coverage = ["# 覆盖与缺口", "", f"资料截止日：{metadata['knowledge_cutoff']}；版本：{metadata['version']}。", "",
                f"收录 {len(entries)} 个独立知识条目、{len(sources)} 条来源记录。包括固定表达、素材/作品族与少量文化概念（如音MAD），并非每条都是一句台词。别名不重复计数，基础平台术语另列。没有全站历史梗总量分母，不计算全量覆盖率。", "",
                "## 目录", "", "- [证据状态](#证据状态)", "- [年代与分区](#年代与分区)", "- [具体缺口](#具体缺口)", "",
                "## 证据状态", "", "| 状态 | 数量 |", "| --- | ---: |"]
    for status in ("verified", "partial", "candidate"):
        coverage.append(f"| {LABELS[status]} | {info['by_evidence'].get(status, 0)} |")
    coverage += ["", "这些状态衡量资料支持程度，不能转成事实概率。", "", "来源读取范围：" + "；".join(f"{k} {v}" for k, v in info['source_access'].items()) + "。full表示已取得正文相关段落，snippet为检索摘要，metadata为标题/日期等元数据。", "", "## 年代与分区", "",
                 "| 分区 | " + " | ".join(n[:-5].replace("-", "–") for n in names) + " |", "| --- | " + " | ".join("---:" for _ in names) + " |"]
    for category in CATEGORIES:
        counts = [sum(e["category"] == category and e["era"] == s[:-5] for e in entries) for s in names]
        coverage.append(f"| {NAMES[category]} | {' | '.join(map(str, counts))} |")
    coverage += ["", "归类是查询导航，不声称各分区拥有独占使用权。一个词条可以跨圈传播，来源日期也可早于B站建站。", "",
                 "## 具体缺口", "", (f"{metadata['knowledge_cutoff'][:4]}年尚未结束，本库不预设该年年度弹幕。确切首创、原视频台词和实时热度只能按具体来源追加证据。" if metadata['knowledge_cutoff'][:4] != '2026' else "2026年尚未结束，本库不预设2026年度弹幕。确切首创、原视频台词和实时热度只能按具体来源追加证据。"), "",
                 "未建立完整条目集的梗族见[历史导航的后续范围](history-map.md#继续补录的范围)。逐项缺口在各词条 notes 与来源 limitations；下表保留所有未完全核实项目，便于按用户问题定向补录。", "",
                 "| 词条 | 状态 | 需补材料 |", "| --- | --- | --- |"]
    for e in sorted(entries, key=lambda e: e["id"]):
        if e["evidence_status"] != "verified":
            coverage.append(f"| {cell(e['term'])} | {LABELS[e['evidence_status']]} | {cell(e['notes'])} |")
    coverage.append("")
    from history import markdown_index
    return {"references/catalog.md": "\n".join(catalog),
            "references/coverage.md": "\n".join(coverage),
            "references/history-index.md": markdown_index(
                root, as_of=as_of, _snapshot=snapshot),
            "references/corpus-summary.json": json.dumps({
                "source_files": [*names, "sources.json", "metadata.json"],
                "statistics": info,
            }, ensure_ascii=False, indent=2) + "\n"}


def atomic_write(path, text):
    mode = path.stat().st_mode & 0o777 if path.exists() and path.is_file() and not path.is_symlink() else 0o644
    fd, temporary = tempfile.mkstemp(prefix=".build-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="检查生成物，无写入")
    args = parser.parse_args()
    expected = projections()
    stale = []
    for name, content in expected.items():
        path = ROOT / name
        if args.check:
            if not path.exists() or path.read_bytes() != content.encode("utf-8"):
                stale.append(name)
        else:
            atomic_write(path, content)
    for name in stale:
        print(f"生成物与数据不一致: {name}")
    print("build: " + ("FAIL" if stale else "PASS"))
    return bool(stale)


if __name__ == "__main__":
    raise SystemExit(main())
