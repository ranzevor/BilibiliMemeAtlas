# 公开来源发现与增量更新

本接口只用于用户明确要求的知识库维护。热门视频、搜索结果、标题、简介和播放统计都是线索，不能直接生成新词条或提高现有条目的资料状态。

## 顺序

1. **取得当前基线。** 运行 `python3 scripts/update.py fingerprint`，后续提案只对该语料版本有效。
2. **读取并选择研究队列。** 先使用 `scripts/history.py` 或[研究队列索引](history-index.md#研究队列)定位已有证据缺口，依当前队列顺序选择研究对象。用户明确指定词条、主题或时间范围时，在相应范围内先查队列和已收录记录，不把未调查的热门内容当成已有队列项。
3. **针对对象发现来源。** 按选中对象使用 `scripts/research.py search` / `inspect` 读取公开元数据；确需补充全新线索时才用 `scripts/update.py discover` 的有界热门列表。拒绝、限流、登录要求或验证码出现时停止该入口，不绕过。
4. **识别梗而不是视频。** 从独立用例判断表达、二创模板或素材族是否形成稳定语义；区分原素材、再次翻红、跨平台导入和新传播。
5. **查重与溯源。** 使用 `atlas.py search --include-candidates`、`get`、`sources.json` 及内容页查重。标题或元数据只登记为 `metadata`；搜索摘要登记为 `snippet`；只有实际读到相关正文才登记为 `full`。
6. **形成提案。** 复用已有来源 ID，记录来源支持的命题和具体局限。不要复制长篇原文、长台词、歌词、私人资料或账号凭据。
7. **预览后应用。** 先运行 `preview` 并核对增改对象、时期和变更文件；只有当前任务允许修改知识库时才运行 `apply --write`。完成后运行 `scripts/verify.py`。

## 命令

```bash
python3 scripts/update.py fingerprint
python3 scripts/history.py --output OUTPUT_DIR/history.json
python3 scripts/update.py discover --pages 1 --output OUTPUT_DIR/popular.json
python3 scripts/research.py search '金坷垃' --output OUTPUT_DIR/search.json
python3 scripts/research.py inspect BV1kt411a7oz --output OUTPUT_DIR/inspect.json
python3 scripts/update.py inspect '哈基米'
python3 scripts/update.py inspect source-id --kind source
python3 scripts/update.py preview --batch OUTPUT_DIR/proposal.json
python3 scripts/update.py apply --batch OUTPUT_DIR/proposal.json --write
python3 scripts/verify.py
```

输出父目录必须已存在，输出文件必须尚不存在。`fingerprint`、`inspect` 和 `preview` 只读；`apply` 只变更本技能的正式语料、来源、元数据和确定性派生文件。

## 提案格式

下面只展示字段结构，不是可入库的真实词条。`base_manifest_sha256` 填写 `fingerprint` 的运行时结果；替换记录的 `before_sha256` 填写 `inspect` 返回的运行时结果。

```json
{
  "format": "bilibili-meme-atlas-update/v1",
  "base_manifest_sha256": "FINGERPRINT_FROM_CURRENT_CORPUS",
  "checked_at": "YYYY-MM-DD",
  "sources": [
    {
      "action": "add",
      "record": {
        "id": "source-id",
        "title": "内容页标题",
        "url": "https://具体内容页",
        "publisher": "实际发布方",
        "source_type": "primary",
        "access_level": "snippet",
        "checked_at": "YYYY-MM-DD",
        "supports": "该来源实际支持的命题",
        "limitations": "仅摘要可用，未读取原片"
      }
    }
  ],
  "entries": [
    {
      "action": "add",
      "record": {
        "id": "stable-entry-id",
        "term": "待核词语",
        "aliases": [],
        "category": "community",
        "era": "2021-2026",
        "meaning": "有依据的有限含义",
        "origin": {
          "summary": "只描述实际确认的源流",
          "date": null,
          "precision": "unknown",
          "bilibili_role": "uncertain"
        },
        "usage": {
          "contexts": ["已观察到的语境"],
          "example": "原创短例句",
          "avoid": ["具体容易误用的情形"]
        },
        "lifecycle": "volatile",
        "sensitivity": [],
        "relations": [],
        "evidence_status": "candidate",
        "evidence": [
          {
            "source_id": "source-id",
            "claims": ["meaning"],
            "note": "仅摘要说明，仍需核原视频"
          }
        ],
        "notes": "仍需补充的独立来源或用例"
      }
    }
  ]
}
```

字段和取值见[词条结构](schema.json)与[来源结构](sources.schema.json)。`origin.date` 不能填检索日期，`era` 也不能替代起源年份。

对已有记录使用 `action: "replace"`，提供完整记录、原 ID 和 `before_sha256`。来源 URL 改变时登记新来源 ID，并保留仍被引用的历史来源。同词或别名确有新义项时，新增操作须通过 `distinct_sense_of` 列出全部已有冲突 ID，并在内容中说明差异。

## 准入条件

- `candidate` 可保存有价值但不足以支持稳定释义的线索；默认事实检索不采用。
- `partial` 至少需要 `full` 或 `snippet` 来源明确支持 `meaning`，并在 `notes` 写出缺口。
- `verified` 需要已读取正文分别支持 `meaning`、`origin`、`platform_usage`，填写日期时还须支持 `date`。全文可读不等于来源中的每句话都可靠。
- 每批最多 25 个词条、60 个来源，输入与网络响应均有体积上限。热门列表最多三页，单请求有超时且不无限重试。
- 重复 ID、重复来源 URL、悬空来源、同名义项未声明、来源日期晚于提案日期、提案或资料截止日晚于当前 UTC 日期、错误时期和弱证据升格均拒绝。
- 空提案和语义相同的替换返回 `no_update`，不改变版本、资料截止日或文件元数据。

## 写入与恢复

`preview` 在隔离临时目录生成完整语料和派生文件。`apply` 与安装、升级、卸载和恢复共享目标父目录的写入租约，写入前再次比较全部参考文件；失败或可捕获中断会尝试恢复原内容。持续介质错误可能保留 `.atlas-update.lock/journal.json`，此时停止其他写入，先只读查看状态：

```bash
python3 scripts/update.py recovery-info
python3 scripts/update.py recover --write --acknowledge-stopped-writers
python3 scripts/build.py --check
python3 scripts/verify.py
```

恢复只接受可识别记录；若文件既不等于原内容也不等于拟写内容，则拒绝覆盖未知修改。若状态为 `committed_cleanup_pending`，必须确认完整更新及派生内容回读正确，再使用 `recover --write --acknowledge-stopped-writers --finalize-committed`，不得将已提交状态当作普通回滚。活动锁原子退役后的 `cleanup_pending` 目录不再阻塞新写入。该机制不是断电级多文件事务，详情见[安全边界](safety.md)。

## 平台资料

- [B站开放平台文档](https://open.bilibili.com/doc)说明正式开发者接口；本项目不申请账号权限。
- [公开热门列表端点的社区文档](https://github.com/pskdje/bilibili-API-collect/blob/main/docs/video_ranking/popular.md)说明可尝试的免登录读取端点。
- 原发布页、创作者公开说明和有日期的同时期报道按实际读取范围逐条登记；项目不复制未获许可的媒体素材。
