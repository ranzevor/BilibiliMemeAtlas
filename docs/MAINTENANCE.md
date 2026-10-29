# 维护

## 语料更新

新增或纠错前，先按名称、别名、来源 URL 和关联梗查询现有记录。来源登记须写明读取范围、支持命题和局限；不能确认首创、原台词或日期时保留相应缺口，不用搜索列表、转载量或编辑时间替代内容证据。

更新提案使用 `bilibili-meme-atlas-update/v1`，并绑定当前语料的运行时指纹。先预览，再显式写入：

```bash
python3 scripts/update.py fingerprint
python3 scripts/update.py preview --batch /输出目录/proposal.json
python3 scripts/update.py apply --batch /输出目录/proposal.json --write
```

`preview` 在临时目录构建完整候选，不修改项目。`apply` 使用与安装、升级、卸载和恢复共享的写入租约，并保留原字节记录和失败回滚；冲突基线、重复 ID、同名义项冲突、悬空来源、晚于当前 UTC 日期的核查或截止日、其他日期越界和弱证据升格均拒绝。完整字段和恢复合同见 `references/refresh.md`。

## 派生文件与回归

原始语料变更后运行：

```bash
python3 scripts/build.py
python3 scripts/build.py --check
python3 scripts/validate.py
python3 scripts/verify.py
```

`catalog.md`、`coverage.md`、`history-index.md` 和 `corpus-summary.json` 只由 `build.py` 根据原始语料生成。`verify.py` 检查当前词条与来源计数、年份索引和派生摘要是否一致，并检查跨记录引用、查询行为、网络边界、更新事务、项目文件、归档、目录生命周期和离线发行字节合同。

## 本地制品与目录

完整项目归档使用 `scripts/package.py`；安装、身份绑定升级、卸载和中断恢复使用 `scripts/install.py`；提供方中立的源码、说明、归档与发行回读比较使用 `scripts/release.py`。这些工具共享项目文件白名单。增加、移动或删除项目文件时，须同步更新 `scripts/validate.py` 的项目边界并补充相应测试。

归档输出、研究结果、更新提案、发行说明和发行清单均放在项目目录之外。备份、卸载副本和未完成操作的暂存目录不自动删除。

## 来源与权利

第三方视频、文章、台词、音乐、角色和商标不因进入来源登记而适用项目 MIT 许可。只保存解释与核查所需的有限事实、短标识和链接；新增长文本、媒体或可再分发素材前，按 `CONTENT_RIGHTS.md` 核对权利条件。

## 弃用与退役

已发布的稳定 ID 不重新分配给其他词条。命令、字段或格式需要弃用时，在实际发行说明中标出受影响对象、替代入口、迁移和回退方法；本项目不预设固定支持期。发布后的版本和资产不就地改写；需要修正时发布新版本。完全退役前，须保留可读的最终源码、许可与权利边界、已发布资产的核对方法，以及卸载与本地数据处理说明。
