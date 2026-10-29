# 验证

## 完整检查

在项目根目录运行：

```bash
python3 scripts/build.py --check
python3 scripts/validate.py
python3 scripts/verify.py
```

`build.py --check` 比较确定性派生文件；`validate.py` 检查 Skill 入口、五个界面字段、项目文件边界、语料结构、来源、日期和跨记录引用；`verify.py` 在前两项通过后运行全部行为与负例测试，并输出语料统计。

## 分层检查

| 入口 | 主要合同 |
| --- | --- |
| `tests/test_contracts.py` | 查询、字段、证据状态、日期和损坏数据 |
| `tests/test_hardening.py` | 路径、符号链接、更新冲突和回滚 |
| `tests/test_history.py` | 年份/分类索引和未知日期 |
| `tests/test_research.py` | 固定网络目标、拒绝、响应上限和输出边界 |
| `tests/test_update.py` | 提案预览、写入、去重、分片和资料升格 |
| `tests/test_transactions.py` | 中断恢复和计划后原始输入变化 |
| `tests/test_project_contract.py` | 文件、模式、当前语料摘要一致性、界面和固定摘要值边界 |
| `tests/test_package.py` | 确定性归档、成员字节、路径和输出边界 |
| `tests/test_install.py` | 安装、升级、卸载、回滚、租约和恢复竞态 |
| `tests/test_release.py` | 源码、说明、资产、清单、校验和与回读负例 |

单独运行技术子集：

```bash
python3 -B -m unittest -v tests.test_package tests.test_install tests.test_release
```

## 归档回读

归档必须写到项目目录之外：

```bash
python3 scripts/package.py build --output /已有目录/bilibili-meme-atlas.zip
python3 scripts/package.py verify /已有目录/bilibili-meme-atlas.zip
```

`verify` 要求成员集合、普通文件类型、长度和内容与当前项目完全一致。安装测试还会以匹配摘要解析归档，并在临时父目录中运行真实查询、升级、卸载、回滚和 POSIX 恢复负例。

## Skill 结构快速检查

需要检查 Skill 文件布局和 frontmatter 时，对名为 `bilibili-meme-atlas` 的完整项目目录运行 Skill Creator 的 `quick_validate.py`。该检查补充本项目验证，但不替代语料、来源或行为测试。
