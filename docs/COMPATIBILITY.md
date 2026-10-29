# 兼容性

## Python 与依赖

运行基线为 CPython 3.10+，查询、构建、校验、归档、目录生命周期和离线发行字节核对均只使用标准库。项目不要求包管理器、模型 SDK、数据库、账号 Cookie 或令牌。公开来源研究能否取得响应取决于固定 B站端点的可用性，但不影响本地检索。

## 路径与文件

源码检出目录可以使用合法的任意名称，支持空格和 Unicode；宿主发现的已安装技能目录须与 `SKILL.md` 的 `name: bilibili-meme-atlas` 一致。本地安装目标固定为父目录下的 `bilibili-meme-atlas`，便于身份绑定的升级和卸载。项目、归档输出、安装父目录和恢复对象不得经过符号链接。

普通查询、构建、校验和归档使用 `pathlib`、`json`、`zipfile` 等跨平台标准库接口。安装、升级和卸载要求暂存、目标与备份位于同一父目录，以便使用原子目录替换。遗留锁的自动恢复还使用 POSIX 文件租约；非 POSIX 环境会明确拒绝该恢复入口，普通备份仍可在目标不存在且身份核对后由调用者恢复。

## Agent Skills 与宿主适配

本项目面向 [Agent Skills 开放格式](https://agentskills.io/specification)。`SKILL.md` frontmatter 包含 `name` 和 `description`，正文为 Agent 的任务指令；`scripts/`、`references/` 和 `assets/` 随任务按需使用。安装后的技能目录名为 `bilibili-meme-atlas`，与 `name` 一致。

发现目录、安装范围、缓存刷新和显式调用语法由宿主实现决定。`.agents/skills` 是目录约定示例，`$skill`、`/skill` 等是具体客户端的调用方式。跨宿主的获取、安装与首次调用步骤见 [Agent 安装指南](../AGENT_INSTALL.md)，目录生命周期见[安装指南](INSTALL.md#宿主发现与调用)。

`agents/openai.yaml` 是面向 OpenAI/Codex 宿主的附加适配元数据。它的 `interface` 恰好包含 `display_name`、`short_description`、`default_prompt`、`icon_small`、`icon_large` 五个双引号字符串；两个图标路径固定为 `./assets/icon.png`。`policy` 只包含布尔真的 `allow_implicit_invocation`。项目校验器检查该适配文件的字段、路径与含义；其他宿主通过 `SKILL.md` 读取通用指令和资源。

已完成的真实宿主样本为 Codex CLI 0.147.0 对 v0.1.0 的显式调用和按任务自动选用，均读取技能并执行离线查询。其他宿主需按各自发现与调用机制验证。

## 0.x 兼容面与版本

兼容面包括词条与来源 schema、已发布的稳定 ID、`atlas.py` 的命令与退出状态、项目文件边界、确定性归档布局、更新提案格式，以及安装、升级、卸载和恢复所使用的目录身份。语料增补或兼容修复递增补丁版本；向后兼容的功能扩展递增次版本。

项目仍处于 0.x。如必须改变已公开的 schema、CLI、归档或安装合同，须递增次版本和 `schema_version`，在发行说明中列出破坏面、可用迁移和回退方式，并为可安全自动转换的机器数据提供明确迁移入口。无法无损迁移时，不就地猜测转换；使用经核对的新归档安装，并保留原目录备份。

回退使用已发布且经字节核对的先前归档，并将当前目录身份传给安装器；替换后的当前目录仍作为备份保留。如果操作中断，按[安装指南](INSTALL.md)的记录与租约流程恢复，不覆盖未知目标。

## 测试范围

Python 3.10+ 是语言与标准库基线。完整验证工作流配置 Ubuntu 24.04、Windows Server 2022 与 CPython 3.10、3.13 的矩阵，启用 UTF-8 模式并在日志中输出 `python -VV`。配置矩阵和实际执行结果分别记录；其他 3.10+ 小版本维持语法与 API 基线声明。每次发行说明应记录实际通过完整检查的 Python 和操作系统版本。
