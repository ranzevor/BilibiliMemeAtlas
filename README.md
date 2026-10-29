<p align="center"><img src="assets/cover.webp" width="100%" alt="B站热梗知识库：以文化语境、来源和自然表达组织的弹幕与二创知识导航" /></p>
<p align="center"><a href="SKILL.md"><img alt="B站热梗知识库图标" src="assets/icon.png" width="20" height="20" /></a> <a href="docs/COMPATIBILITY.md"><img alt="Python语言基线：3.10+" src="https://img.shields.io/badge/Python-3.10%2B-blue.svg" /></a> <a href="LICENSE"><img alt="项目自有内容许可：MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg" /></a></p>

# B站热梗知识库 · BilibiliMemeAtlas

用于查询和解释 B站历史热梗、弹幕黑话、鬼畜素材、ACG、游戏电竞、直播与跨圈表达。离线语料提供含义、别名、传播关系、使用边界和来源索引；需要精确溯源或补录时，再核读相应公开来源。运行与开发检查采用 Python 3.10+ 标准库。

当前项目版本见 [`references/metadata.json`](references/metadata.json)，Skill 名称为 `bilibili-meme-atlas`。本库不是全站梗总表，也不提供账号互动、视频下载或无人值守抓取。

## 项目状态与边界

当前成熟度为 **0.x / pre-1.0**。目标用户是需要离线查询、核对和维护 B站历史热梗资料的使用者与维护者；项目以可复核来源、明确证据状态和不臆造出处的查询行为为核心。兼容面与破坏性变更策略见[兼容性](docs/COMPATIBILITY.md)。

质量基线：确定性派生文件与源码一致；项目结构和语料合同验证通过；完整行为测试通过；同一源码可确定性构建并核验归档；默认事实检索不采用 `candidate`；仓库不打包未获适用权利的第三方完整作品。公开来源失效、证据仍为 `partial`/`candidate`、固定平台端点不可用和第三方权利条件变化是持续维护风险，不会被静默改写为已解决。

范围内包括离线检索、语料维护、公开来源核查、确定性归档及本地安装/恢复；范围外包括账号操作、视频下载、无人值守抓取、实时热度监控和第三方媒体再分发。运行资源保持为 CPython 3.10+ 标准库，无数据库、模型服务或项目第三方 Python 包。

[Skill 入口](SKILL.md) · [词条目录](references/catalog.md) · [历史导航](references/history-map.md) · [年份索引](references/history-index.md) · [来源](references/sources.json) · [用法](references/usage.md) · [安装](docs/INSTALL.md) · [维护](docs/MAINTENANCE.md) · [验证](docs/VERIFICATION.md) · [发行字节合同](docs/RELEASING.md) · [权利](CONTENT_RIGHTS.md) · [English](docs/README.en.md)

## 快速查询

将 `SKILL_DIR` 替换为本项目目录；路径含空格时保留引号。命令可从其他工作目录调用。

```bash
python3 "SKILL_DIR/scripts/atlas.py" search '爷青回'
python3 "SKILL_DIR/scripts/atlas.py" get '金坷垃'
python3 "SKILL_DIR/scripts/atlas.py" stats
```

`search` 支持分类、Markdown 输出和分页；默认排除 `candidate`，同时报告被排除的候选命中数。`get` 返回全部同名义项。未知词返回空结果，不生成近似出处；无结果退出 0，输入或语料错误退出 2。

没有命令执行能力时，先读[词条目录](references/catalog.md)，再按 ID 打开相应时期分片及[来源登记](references/sources.json)。目录缩写不能替代完整记录。

## 数据与证据

当前收录规模与分类计数见 `stats`、[覆盖与缺口](references/coverage.md) 和[语料摘要](references/corpus-summary.json)。资料截止日表示语料核查范围，不是软件发布日期。

| 状态 | 使用范围 |
| --- | --- |
| `verified` | 登记的有限命题有全文来源支持，仍按具体命题核对来源 |
| `partial` | 只陈述已有材料支持的部分，并保留来源、日期等缺口 |
| `candidate` | 作为研究线索保留，默认事实检索不采用 |

`full`、`snippet`、`metadata` 表示来源正文、摘要和元数据的读取范围。`era` 是历史索引分组，不能当作首创日期。查询结果保留风险、别名、证据命题与来源局限。

## 公开来源研究与更新

研究输出须写到本项目之外已存在的目录；联网返回只是线索或元数据，不自动生成词条或提高证据等级。

```bash
python3 "SKILL_DIR/scripts/research.py" search '金坷垃' --output /输出目录/search.json
python3 "SKILL_DIR/scripts/update.py" fingerprint
python3 "SKILL_DIR/scripts/update.py" preview --batch /输出目录/proposal.json
python3 "SKILL_DIR/scripts/update.py" apply --batch /输出目录/proposal.json --write
```

搜索和详情读取有固定主机、超时、响应体积和失败不重试边界。更新提案先在临时目录构建完整候选；冲突基线、重复 ID、悬空来源和弱证据升格会被拒绝。完整合同见[维护接口](references/refresh.md)和[安全边界](references/safety.md)。

## 本地工程入口

```bash
python3 scripts/build.py --check
python3 scripts/validate.py
python3 scripts/verify.py
python3 scripts/package.py build --output /已有目录/bilibili-meme-atlas.zip
python3 scripts/package.py verify /已有目录/bilibili-meme-atlas.zip
```

确定性归档、身份绑定的安装/升级/卸载与中断恢复见[安装指南](docs/INSTALL.md)。提供方中立的源码、说明、归档和发行回读字节核对见[发行字节合同](docs/RELEASING.md)。这些本地工具不执行远端发布。

[贡献规则](CONTRIBUTING.md) · [协作约定](CODE_OF_CONDUCT.md) · [安全](SECURITY.md) · [兼容性](docs/COMPATIBILITY.md) · [依赖](docs/DEPENDENCIES.md)

## 许可

[MIT](LICENSE) 适用于项目自有代码、原创文本、索引编排、封面和图标。外部视频、文章、短语、音乐、人物及商标仍适用各自的权利条件；项目许可不转授这些权利。详见[内容权利](CONTENT_RIGHTS.md)。
