# Agent 安装指南

供执行安装任务的 Agent 使用。安装完成需要三项结果：完整 Skill 目录通过校验；当前 Agent 实际加载 `bilibili-meme-atlas`，宿主支持原生发现时还须确认发现结果；使用已安装技能完成一次离线查询。

## 1. 确认宿主与安装范围

从当前会话、配置和宿主官方文档确认 Agent 客户端及其技能安装方式。默认仅安装到用户当前项目；用户明确要求全局使用时，才选择用户级目录。机器上存在某个客户端命令，不能据此认定它就是当前宿主。

本项目采用 [Agent Skills 格式](https://agentskills.io/specification)：`SKILL.md` 提供名称、描述和任务指令，`scripts/`、`references/`、`assets/` 提供按需读取的工具和资料。安装目录名必须为 `bilibili-meme-atlas`，其下直接包含 `SKILL.md`。`.agents/skills` 是部分宿主采用的目录约定；按当前宿主文档选择实际父目录，不把某个目录或 `$skill`、`/skill` 语法当作通用要求。

检查 CPython 3.10+、源码获取和 HTTPS 下载能力。查询及本地校验只需 Python 标准库；源码检出可使用 Git 或宿主提供的等价工具。`agents/openai.yaml` 是可选的宿主适配元数据，保留在完整目录中，通用任务入口仍为 `SKILL.md`。

## 2. 获取同一发行的源码与资产

仓库固定为 `https://github.com/ranzevor/BilibiliMemeAtlas.git`。用户指定版本时使用该版本；否则从[发行页](https://github.com/ranzevor/BilibiliMemeAtlas/releases/latest)选择最新正式发行，并取得其实际标签。后续步骤固定使用同一次发行及同一标签最终解析到的完整提交，不随 `main` 变化。

在安装目标之外准备独立工作目录，取得：

- 准确标签提交的完整源码检出，记为 `SOURCE_DIR`。从这里运行安装器和发行校验工具。
- `bilibili-meme-atlas-vX.Y.Z.zip` 与 `SHA256SUMS-vX.Y.Z.txt`，放在只含这两个文件的 `ASSETS_DIR`。
- 该发行的正文、元数据及托管平台提供的资产长度和 `sha256:` 摘要。

托管平台自动生成的源码 ZIP 不能充当 Skill 发行包。不要在核验前从下载的 Skill ZIP 中解压并运行脚本。

将实际发行正文按原始 UTF-8 字节保存为 `NOTES_FILE`，保留换行和末尾字节。把回读元数据转换为 `OBSERVED_FILE`，字段恰好如下：

| 字段 | 实际取值 |
| --- | --- |
| `repository` | `ranzevor/BilibiliMemeAtlas`，同时核对读取对象确属该仓库 |
| `tag` | 发行的 `tag_name` |
| `tag_target` | 该标签最终解析到的完整提交；不能用 `target_commitish` 的分支名代替 |
| `name`、`body`、`draft`、`prerelease` | 发行回读的对应值，保留正文 |
| `assets` | 所有发行资产，每项仅含 `name`、`size`、`digest`，使用实际名称、长度和平台可信摘要 |

使用已有连接器、GitHub CLI 或 HTTPS API 获取这些输入；不要求用户手工填入摘要。平台未提供可信摘要时，报告具体缺项并停止安装，不能用对下载文件自行计算的摘要代替发行回读依据。

## 3. 核对发行字节

以下 `SOURCE_DIR`、`ASSETS_DIR`、`NOTES_FILE`、`OBSERVED_FILE`、`MANIFEST_FILE` 和 `SOURCE_REVISION` 均替换为上一步取得的实际路径或提交。选择实际可用的 Python 3.10+ 命令；Windows 可使用 `python`。

```bash
python3 "SOURCE_DIR/scripts/build.py" --check
python3 "SOURCE_DIR/scripts/validate.py"
python3 "SOURCE_DIR/scripts/release.py" describe --notes "NOTES_FILE" --assets-dir "ASSETS_DIR"
```

将 `describe` 的 JSON 标准输出以 UTF-8、无 BOM 保存为 `MANIFEST_FILE`，再运行：

```bash
python3 "SOURCE_DIR/scripts/release.py" verify-installable-release --manifest "MANIFEST_FILE" --notes "NOTES_FILE" --assets-dir "ASSETS_DIR" --metadata "OBSERVED_FILE" --repository ranzevor/BilibiliMemeAtlas --source-revision SOURCE_REVISION
```

仅当命令成功并返回 `MATCH`、`installable: true` 时继续。从成功结果的 `assets` 中按归档文件名选择 `digest`，去掉 `sha256:` 前缀后作为安装所用的 `ARCHIVE_DIGEST`。完整字段和失败条件见[发行字节合同](docs/RELEASING.md#可安装发行门禁)。

## 4. 安装完整 Skill

按宿主约定创建技能父目录 `PARENT`，然后运行：

```bash
python3 "SOURCE_DIR/scripts/install.py" install "ASSETS_DIR/bilibili-meme-atlas-vX.Y.Z.zip" --sha256 ARCHIVE_DIGEST --parent "PARENT"
```

结果应为 `PARENT/bilibili-meme-atlas/SKILL.md`，同时包含完整脚本、语料、资源及许可。不要只复制 `SKILL.md`，不要改为符号链接，也不要同时安装到所有检测到的客户端。

若目标已存在，先取得完整目录身份。相同内容可复用；需要替换时按[身份绑定升级](docs/INSTALL.md#身份绑定的升级)传入 `--expected-current` 并保留旧备份。遇到恢复锁或未知内容时，按[生命周期指南](docs/INSTALL.md#中断恢复)处理，不覆盖目标。

## 5. 验证发现、加载与使用

按宿主支持的技能列表、加载工具或调用方式，确认技能名称和安装路径。若需要重新加载或新会话，给出该宿主的具体操作，并在真正加载前将发现验证保留为未完成。

加载已安装目录中的 `SKILL.md`，使用其离线入口完成任务：

```text
使用 bilibili-meme-atlas 解释“金坷垃”的含义和常见使用语境，并核对本地来源。
```

确认 Agent 读取了实际词条和来源，保留相应证据状态；查询不修改语料、不联网。只有手工运行脚本、尚未被宿主发现时，只能报告目录安装与脚本检查结果。

宿主没有原生技能发现能力、但能读取本地文件时，可在当前任务中显式读取该完整目录的 `SKILL.md` 并按需读取资源；说明采用显式加载方式。无法执行 Python 时可按 `SKILL.md` 的目录与分片路径读取资料，不能把未执行的脚本检查报告为通过。

完成后简要告知用户：安装版本、范围与路径、实际发现或显式加载结果，以及查询结果。后续使用只需提出自然语言任务，示例见 [README](README.md#快速开始)。
