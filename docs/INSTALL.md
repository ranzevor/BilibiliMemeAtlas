# Skill 安装与本地生命周期

快速入口见 [README](../README.md#快速开始)，供 Agent 执行的完整流程见 [Agent 安装指南](../AGENT_INSTALL.md)。本页提供发行核对、归档和目录生命周期接口。

## 从已发布版本安装

首次安装同时需要两类已核对输入：从发布页取得的 `bilibili-meme-atlas-vX.Y.Z.zip`，以及与可安装发行门禁中仓库和标签最终提交一致的源码检出。只从该检出运行 `scripts/install.py`；不从未核对归档中预先解压并执行脚本，也不把托管平台自动生成的源码归档当作 Skill 归档。

安装所用摘要必须来自 [`verify-installable-release`](RELEASING.md#可安装发行门禁) 的成功回读比较，不能只对已下载归档自行计算后就视为可信。仓库、标签提交、发行正文、资产名称、长度或可信摘要任一不符时不安装。

## 构建归档

从完整项目根目录运行验证，再把确定性归档写到项目目录之外：

```bash
python3 scripts/build.py --check
python3 scripts/validate.py
python3 scripts/verify.py
python3 scripts/package.py build --output /已有目录/bilibili-meme-atlas.zip
python3 scripts/package.py verify /已有目录/bilibili-meme-atlas.zip
```

归档固定成员排序、时间字段和普通文件权限。`verify` 把成员名、文件类型、长度和字节与当前项目逐项比较。归档摘要由命令在运行时返回；项目内不保存固定摘要。

## 安装

`--parent` 必须是已存在的父目录；安装目标固定为其下的 `bilibili-meme-atlas`。将独立取得并核对的归档摘要传给安装器：

```bash
python3 scripts/install.py install /已有目录/bilibili-meme-atlas.zip \
  --sha256 ARCHIVE_DIGEST \
  --parent /已有父目录
```

安装器先核对完整归档摘要，再限制成员根目录、路径、类型、数量、逐文件大小和总展开大小。它拒绝绝对路径、路径穿越、控制字符、Windows 保留名、符号链接、特殊文件、大小写冲突、未登记文件和缺少文件。内容在同一父目录暂存并通过完整项目校验后，才替换固定目标。

## 宿主发现与调用

给 Agent 的完整安装步骤见 [Agent 安装指南](../AGENT_INSTALL.md)。Agent Skills 规定技能目录内的格式；实际发现目录、安装范围、刷新方式和显式调用语法由宿主决定。

按当前宿主文档选择 `--parent`。例如，支持项目级 `.agents/skills` 的宿主可使用：

```text
<项目>/.agents/skills/bilibili-meme-atlas/
├── SKILL.md
├── scripts/
├── references/
└── …（完整发行中的其余文件）
```

完成安装后，确认宿主实际发现并加载了技能，再用自然语言调用：

```text
使用 bilibili-meme-atlas 解释“金坷垃”的含义和合适用法，并核对本地来源。
```

宿主也可按技能描述为匹配的任务选择该技能。安装成功、宿主发现与实际任务完成分别核对；需要刷新或新会话时，按当前宿主的步骤处理。`agents/openai.yaml` 的适配范围见[兼容性](COMPATIBILITY.md#agent-skills-与宿主适配)。

## 身份绑定的升级

先计算当前完整目录身份，再用新项目中的安装器执行升级：

```bash
python3 scripts/install.py identity /已有父目录/bilibili-meme-atlas
python3 scripts/install.py install /已有目录/new.zip \
  --sha256 NEW_ARCHIVE_DIGEST \
  --parent /已有父目录 \
  --expected-current CURRENT_IDENTITY
```

版本字符串不能替代目录身份。当前目录缺失、已修改或在暂存期间发生变化时，升级停止。成功替换后，旧目录保留为同父目录的 `.bilibili-meme-atlas.backup-<随机标识>`；脚本不删除该备份。

## 卸载与普通备份恢复

```bash
python3 scripts/install.py uninstall \
  --parent /已有父目录 \
  --expected-current CURRENT_IDENTITY
```

卸载仅把准确匹配的目标移动为 `.bilibili-meme-atlas.removed-<随机标识>`，不删除其中内容。普通备份恢复要求固定目标不存在、没有安装锁或知识更新锁，并且备份身份已核对；将完整备份改名为 `bilibili-meme-atlas` 后，再运行 `validate.py`、所需查询和完整项目检查。

## 中断恢复

安装、升级、卸载和已安装目录的知识更新共用同一个父目录写入租约；任一操作持有租约时，其他写操作和恢复都拒绝。目录操作还使用 `.bilibili-meme-atlas.install-lock` 保存可解释的阶段。普通失败会尝试恢复旧目录；已经进入固定目标的失败新目录会先移到 `.bilibili-meme-atlas.failed-<随机标识>`。记录只含操作类型、相对成员名和运行时身份，不含主机名或进程号。暂存、失败目录和备份原位保留。

结束操作时，工具先原子移走活动锁名，再尝试删除退役清理目录。若后一步失败，成功结果中的 `cleanup_pending` 指向不再阻塞新操作的退役目录；可在确认它不再需要后手工清理。若连原子移走也失败，活动锁保留完整记录，`recovery-info` 将其区分为待回滚、已回滚待清理或已提交待清理。

先只读取得待恢复记录，再核对记录摘要和备份身份：

```bash
python3 scripts/install.py recovery-info --parent /已有父目录
python3 scripts/install.py identity /已有父目录/.bilibili-meme-atlas.backup-BACKUP_ID
python3 scripts/install.py recover \
  --parent /已有父目录 \
  --expected-recovery RECORD_DIGEST \
  --backup /已有父目录/.bilibili-meme-atlas.backup-BACKUP_ID \
  --expected-backup BACKUP_IDENTITY \
  --acknowledge-stopped-writers
```

`recover` 当前使用 POSIX 文件租约。活动安装或另一恢复仍持有租约时拒绝；记录、来源、目标或目录对象在恢复期间变化时也拒绝。目标已存在时不覆盖。若备份已经完整改名回目标而锁仍在，使用 `--already-restored --expected-current CURRENT_IDENTITY` 完成同一记录的回读与锁移除。成功恢复不删除暂存内容。

若只是已提交操作的锁清理未完成，先核对 `recovery-info` 的 `committed_cleanup_pending` 状态，再不传备份参数地完成清理：

```bash
python3 scripts/install.py recover \
  --parent /已有父目录 \
  --expected-recovery RECORD_DIGEST \
  --acknowledge-stopped-writers \
  --finalize-committed
```
