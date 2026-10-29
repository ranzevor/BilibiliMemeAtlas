#!/usr/bin/env python3
"""Build an evidence-aware year/category matrix and a deterministic research queue."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from atlas import ROOT, CatalogueError, _load_catalogue_snapshot

CATEGORIES = ('community','acg','guichu','game','live','music','film','life','tech','esports','vtuber')

def matrix(root=ROOT, *, as_of=None, active_update_lock=False, _snapshot=None):
    snapshot = _snapshot if _snapshot is not None else _load_catalogue_snapshot(
        root, as_of=as_of, active_update_lock=active_update_lock)
    entries, sources, metadata = snapshot.entries, snapshot.sources, snapshot.metadata
    end = int(metadata['knowledge_cutoff'][:4])
    cells = []
    for year in range(2009, end + 1):
        for category in CATEGORIES:
            related = [e for e in entries if e['category'] == category and
                       int(e['era'][:4]) <= year <= int(e['era'][-4:])]
            dated = [e for e in related if e['origin']['precision'] in ('year','exact')
                     and str(e['origin']['date'])[:4] == str(year)]
            # A date is usable only for the explicitly summarized node, not a Bilibili debut.
            supported = [e for e in dated if any('date' in ref['claims'] and
                         sources[ref['source_id']]['access_level'] == 'full' for ref in e['evidence'])]
            cells.append({'year': year, 'category': category,
                          'date_supported_entry_ids': [e['id'] for e in supported],
                          'dated_but_not_full_supported_ids': [e['id'] for e in dated if e not in supported],
                          'era_index_entry_ids': [e['id'] for e in related],
                          'support_basis': 'registered_full_access_and_date_claim',
                          'status': 'has_registered_date_claim' if supported else 'needs_research'})
    queue = []
    for e in entries:
        missing = sorted({'meaning','origin','platform_usage'} - {
            claim for ref in e['evidence'] if sources[ref['source_id']]['access_level']=='full'
            for claim in ref['claims']})
        if e['evidence_status'] != 'verified' or missing or e['origin']['date'] is None:
            queue.append({'entry_id': e['id'], 'term':e['term'], 'category':e['category'],
                          'era':e['era'], 'status':e['evidence_status'], 'missing_full_claims':missing,
                          'date_precision':e['origin']['precision'], 'gap':e['notes'],
                          'source_urls':[sources[ref['source_id']]['url'] for ref in e['evidence']],
                          'priority':0 if e['evidence_status']=='candidate' else 1 if e['evidence_status']=='partial' else 2})
    queue.sort(key=lambda v:(v['priority'],v['era'],v['category'],v['entry_id']))
    return {'format':'bilibili-meme-atlas-history-matrix/v2','cutoff':metadata['knowledge_cutoff'],
            'interpretation':'单元格只索引origin.date为该年、era包含该年且登记full/date命题的记录；full标记不表示其他命题均已确认。无命中不表示该年不存在来源或使用，date=null的传播节点不计入。不是诞生或B站首发清单；不计算覆盖率。',
            'cells':cells,'research_queue':queue}


def markdown_index(root=ROOT, *, as_of=None, active_update_lock=False,
                   _snapshot=None):
    """Render the maintained year/category navigation from the same matrix."""
    snapshot = _snapshot if _snapshot is not None else _load_catalogue_snapshot(
        root, as_of=as_of, active_update_lock=active_update_lock)
    data = matrix(root, as_of=as_of, _snapshot=snapshot)
    entries = snapshot.entries
    names = {entry['id']: entry['term'] for entry in entries}
    by_year = {}
    for cell in data['cells']:
        by_year.setdefault(cell['year'], []).append(cell)
    lines = [
        '# 年份与分类研究索引', '',
        '本表按当前语料生成。分区为本库检索分类，不等同于B站历年官方分区。', '',
        '单元格只索引 `origin.date` 为该年、`era` 包含该年且登记 `full/date` 命题的记录；`full` 标记不等于所有命题均已独立确认。无命中不表示该年不存在来源或使用，`date=null` 的传播节点不计入。这里不是诞生或B站首发清单，也不计算覆盖率。', '',
        '`date_supported_entry_ids` 是输出字段名，只表示登记为 `full/date` 且通过日期/时期索引条件；不表示词条的其他命题均已确认。', '',
        '## 逐年入口', '',
        '| 年 | 有登记日期命题的分类格 / 11 | 研究锚点（至多4项） |',
        '| --- | ---: | --- |',
    ]
    for year in sorted(by_year):
        cells = by_year[year]
        identifiers = []
        for cell in cells:
            identifiers.extend(cell['date_supported_entry_ids'])
        anchors = '；'.join(
            f"{names[identifier]}（{identifier}）"
            for identifier in identifiers[:4])
        if not anchors:
            anchors = '本索引条件未命中；需继续研究，不表示不存在材料'
        populated = sum(bool(cell['date_supported_entry_ids']) for cell in cells)
        lines.append(f'| {year} | {populated} | {anchors} |')
    lines += [
        '', '## 研究队列', '',
        f"当前队列{len(data['research_queue'])}条，包含所有非 `verified`、缺少 `meaning/origin/platform_usage` 全文登记或 `origin.date` 未知的记录。队列之外的记录仍须按具体来源判断词义、别名、风险和材料局限。", '',
        '```bash',
        'python3 "SKILL_DIR/scripts/history.py" --output OUTPUT_DIR/history.json',
        '```', '',
        f"输出为{len(data['cells'])}个年份×分类格及确定性排序队列。只读本地数据，输出必须是新文件；完整词条及来源由查询工具或JSON分片读取。", ''
    ]
    return '\n'.join(lines)

def checked_output(path, root=ROOT):
    path = Path(path).absolute()
    if path.exists():
        raise ValueError('输出文件已存在；请选择新文件')
    if not path.parent.is_dir():
        raise ValueError('输出父目录不存在')
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('输出路径不能经过符号链接')
    if path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('历史研究输出不能保存到技能目录')
    return path

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path)
    args=p.parse_args(argv)
    try:
        output = checked_output(args.output) if args.output else None
        text=json.dumps(matrix(),ensure_ascii=False,indent=2)+'\n'
        if output:
            with output.open('x',encoding='utf-8') as stream:stream.write(text)
        else: print(text,end='')
        return 0
    except (CatalogueError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f'历史索引生成失败：{exc}',file=sys.stderr)
        return 2
if __name__=='__main__':raise SystemExit(main())
