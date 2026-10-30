# Skill 结构与数据合同

## 入口与界面

`SKILL.md` 的 frontmatter 只包含 `name` 和 `description`；名称固定为 `bilibili-meme-atlas`。正文负责工作方式和按需资料导航，不复制完整语料。

`agents/openai.yaml` 的 `interface` 恰好包含五个本项目字段：

1. `display_name`
2. `short_description`
3. `default_prompt`
4. `icon_small`
5. `icon_large`

五个字符串字段都使用双引号；两个图标路径固定为项目内的 `./assets/icon.png`，默认提示显式引用 `$bilibili-meme-atlas`。`policy` 只允许启用隐式调用。字段、文本和图标目标严格固定；不接受外部图标、路径穿越或未定义字段。`scripts/validate.py` 和核心测试共同检查这些约束。

## 语料结构

| 字段 | 语义 |
| --- | --- |
| `id` | 稳定记录身份；重命名或增加别名不更换 ID |
| `term` / `aliases` | 词名与同义写法；同名多义保留多个结果 |
| `era` | 历史导航分组，不能当作首创日期 |
| `origin` | 来源节点、日期精度与 B站传播角色 |
| `usage` | 使用场景、原创例句和具体误用边界 |
| `evidence` | 来源 ID、支持命题和说明 |
| `evidence_status` | 资料支持状态，不是事实概率 |
| `notes` | 尚缺材料、冲突与解释条件 |

机器字段见[词条结构](schema.json)与[来源结构](sources.schema.json)。`scripts/validate.py` 检查类型、跨记录引用、日期、时期、来源读取范围与资料状态；结构通过不替代对外部来源内容的阅读。

## 检索合同

名称使用 NFKC 与大小写归一；精确名称优先于正文提及。含两个或更多字符的名称可在句子中识别，单字词只允许直接查询，以减少普通句子误命中。关键词按常见分隔符切分并作词项匹配，不声称提供向量或语义搜索。

结果先按分类、时期和资料状态过滤，再排序和分页。默认隐藏 `candidate`，但报告其命中数；同名查询保留全部义项；未知查询返回空结果。JSON 保留完整记录与相关来源，Markdown 只用于阅读。

一次读取先捕获全部分片、来源与元数据字节，核对前后目录对象、文件集合和文件身份，再解析同一份快照。检测到活动或待恢复的更新、或捕获期间发生变化时，查询以退出状态 2 结束，不创建锁文件或自动重读。派生文件和年份索引复用同一快照。持有写入租约的更新与恢复使用内部读取状态；只读恢复状态查询另外绑定恢复记录的目录及内容身份。

## 公开数据接口

项目包含三个联网读取动作，均由固定 B站 HTTPS 主机和路径构造，不接受调用者提供任意 URL：

| 动作 | 入口 | 结果用途 |
| --- | --- | --- |
| 热门列表 | `scripts/update.py discover` | 最多三页视频线索 |
| 视频搜索 | `scripts/research.py search` | 最多一页标题、简介和元数据 |
| 视频详情 | `scripts/research.py inspect` | 单个合法 BV 号的元数据 |

三种动作共享无凭据、禁止重定向、单请求超时、响应体积上限和失败不重试边界。网络 JSON 按 UTF-8 严格解码，拒绝重复字段、`NaN`/`Infinity`、过深嵌套和超量节点。返回只可作为线索或元数据来源，不能自动进入语料。完整规则见[安全边界](safety.md)和[维护接口](refresh.md)。

## 增量更新合同

更新提案绑定当前语料输入的运行时指纹；替换操作绑定原记录的运行时指纹。程序检查 ID、同名义项、来源 URL、悬空引用、日期、资料状态和时期分片，在临时目录构建完整候选，再由 `preview` 或 `apply --write` 使用。

2027 年起按连续五年时期新增分片，既有时期与稳定 ID 不重排。`no_update` 不修改版本、截止日或文件元数据。故障与恢复边界见[安全边界](safety.md)。

## 运行依赖

运行和验证仅依赖 CPython 3.10+ 标准库。查询无网络依赖；联网研究依赖公开 B站端点的实际可用性，但端点不可用不会使本地检索失效。项目不依赖包管理器、模型 SDK、数据库、账号 Cookie 或令牌。

## 完整项目与本地归档

`scripts/validate.py` 对项目文件边界、Skill 入口、五个界面字段、语料结构和跨记录引用作离线检查；`scripts/build.py --check` 核对确定性派生文件；`scripts/verify.py` 汇总结构、派生物和行为测试。源码发布边界由当前项目文件清单约束；归档构建同时检查安全成员路径、普通文件类型和体积限制。安装与目录生命周期按可信归档和目录身份核对成员及字节。

`scripts/package.py build` 将完整项目写成成员排序、时间字段和普通文件权限固定的 ZIP；输出必须位于项目目录之外。`verify` 把已有归档的成员名、类型、长度和字节与当前项目逐项比较。两次构建输入相同时输出字节相同；命令返回的摘要是当次运行结果，项目内不保存固定摘要。

```bash
python3 scripts/verify.py
python3 scripts/package.py build --output /已有目录/bilibili-meme-atlas.zip
python3 scripts/package.py verify /已有目录/bilibili-meme-atlas.zip
```

## 本地目录生命周期

`scripts/install.py` 只处理调用者明确给出的本地归档和已存在父目录。安装先核对归档摘要、成员边界、数量和体积，再在同一父目录暂存并逐成员核对名称和字节；升级还须绑定当前目录的运行时身份。替换成功后，旧目录保留为同父目录的隐藏备份。卸载把准确匹配的目录移出固定名称，但不删除副本。

```bash
python3 scripts/install.py install /已有目录/bilibili-meme-atlas.zip --sha256 ARCHIVE_DIGEST --parent /已有父目录
python3 scripts/install.py identity /已有父目录/bilibili-meme-atlas
python3 scripts/install.py install /已有目录/new.zip --sha256 NEW_DIGEST --parent /已有父目录 --expected-current CURRENT_IDENTITY
python3 scripts/install.py uninstall --parent /已有父目录 --expected-current CURRENT_IDENTITY
```

安装、升级、卸载、知识更新和两类恢复共用目标父目录的写入租约；目标移动、回读和返回结果前都不释放。每类操作另保留可解释日志。结束时先原子退役活动锁名，再尽力物理清理；已提交待清理与待回滚状态可区分并重复恢复。`recovery-info` 只读分类目录或更新记录；POSIX 上的目录 `recover` 要求调用者绑定记录、备份身份并确认其他写入者已停止，且绝不覆盖已存在目标。恢复成功不删除备份、失败目录或暂存内容。

## 离线发行字节合同

`scripts/release.py` 处理项目自含的 `bilibili-meme-atlas-release/v1` 清单。清单绑定版本、标题、完整源码身份、UTF-8 发行说明和可选确定性归档；`source_only` 不带自定义资产且不可作为安装前置，`skill_zip` 恰好包含版本归档与严格单行校验和文件。通用清单没有固定仓库字段；可安装发行对象在运行时绑定仓库和完整源码修订。

`describe` 从本地字节生成清单；`lint` 检查清单；`check-source` 重新核对源码、说明与资产；`prepare-request` 生成提供方中立的标签、正文和资产对象；`prepare-installable-request` 额外绑定仓库和源码修订。`verify-release` 要求每个回读资产都有实际字节数和可信 SHA-256 摘要；`verify-installable-release` 还比较仓库和标签解析后的提交。所有动作只读本地输入并向标准输出写 JSON，不连接托管平台，也不创建标签、上传或发布。

```bash
python3 scripts/release.py describe --notes /已有目录/notes.md --assets-dir /已有目录/assets
python3 scripts/release.py lint --manifest /已有目录/release.json
python3 scripts/release.py check-source --manifest /已有目录/release.json --notes /已有目录/notes.md --assets-dir /已有目录/assets
python3 scripts/release.py prepare-request --manifest /已有目录/release.json --notes /已有目录/notes.md --assets-dir /已有目录/assets
python3 scripts/release.py verify-release --manifest /已有目录/release.json --notes /已有目录/notes.md --assets-dir /已有目录/assets --metadata /已有目录/observed.json
```
