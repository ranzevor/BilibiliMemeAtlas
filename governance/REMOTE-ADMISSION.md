# 远端校验与受控 squash

## 执行入口

.github/scripts/trusted_admission.cjs 由独立 GitHub App 执行。部署副本固定到经审查的完整提交，连同 governance_gate.cjs 和 Markdown 依赖放在独立、只读的运行目录；贡献分支和 PR 工作区不能写入该目录。程序通过 GitHub API 读取 PR、提交消息与差异，不检出或执行贡献分支中的脚本。

运行环境为 Node.js 22 与 markdown-it 15.0.3。在部署副本中安装解析器：

    npm install --prefix .github --no-save --no-package-lock --ignore-scripts --no-audit --no-fund markdown-it@15.0.3

生产部署绑定 ranzevor/BilibiliMemeAtlas，独立验收部署绑定专用验收仓库；仓库权限为 Checks 读写、Contents 读写和 Pull requests 只读。App 不授予 Administration 权限。私钥存放在运行目录之外的受限文件中，不能进入仓库、PR 作业、缓存或制品。

运行进程读取以下环境变量：

| 变量 | 内容 |
| --- | --- |
| GOVERNANCE_APP_ID | 独立 App 的应用 ID |
| GOVERNANCE_INSTALLATION_ID | 该 App 在仓库所有者账号下的安装 ID |
| GOVERNANCE_PRIVATE_KEY_FILE | RSA 私钥文件的绝对路径 |
| GOVERNANCE_RULES_FILE | 管理员读取并保存的当前完整规则版本文件 |

安装令牌按部署绑定的单个仓库 ID 签发并自动更新。程序核对仓库和 App 的不可变身份；检查写入的来源必须是配置的 App。

## 持续校验

在独立运行目录启动常驻进程：

    node .github/scripts/trusted_admission.cjs watch

进程每分钟读取开放 PR，重新校验发生变化的提交与元数据。它不依赖 PR 编辑事件，因此由仓库令牌产生而没有触发 Actions 的编辑也会被读取。合并候选尚未计算完成、冲突、临时 API 故障和校验期间变化会以一分钟至五分钟的有限退避重新检查；状态恢复后即使元数据未变也能恢复校验。确定性违规保持失败，不能通过重试获得放行。

没有变化的结果使用缓存，每五分钟读取 App 实际发布的最新检查。检查缺失、被替换、状态或结果变化、完成时间无效时重新执行完整校验；正常缓存不重复发布。App 的检查在完成六天后重新校验并发布，为 GitHub 的七天必需检查有效期预留时间。该策略只刷新 Trusted metadata gate，三项 Actions 质量检查仍须真实运行。GitHub 的当前 HEAD、测试合并提交和检查时效规则见[必需检查故障排查](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)。

单次检查入口：

    node .github/scripts/trusted_admission.cjs check PR_NUMBER

Trusted metadata gate 绑定当前 HEAD。同一 HEAD 对应多个受支持的开放 PR 时，全部 PR 均须通过，防止一个目标分支的成功覆盖另一个 PR 的失败。程序核对完整分页、提交与文件数量、开放列表与详情的共同元数据、PR 前后快照和待写入的 squash 消息。列表与详情的标题、正文、分支、仓库或更新时间不一致时，该组检查失败并重新读取。

## 分支规则

以下目标与三项工作流的 PR 目标范围一致：

- main
- release/v0.1.5-queue-item-*
- fix/v0.1.5-duplicate-source-reference
- fix/v0.1.5-duplicate-source-reference-rebuild
- feat/governance-pr-metadata-gate

使用已注册的独立 App ID 生成两项 Ruleset 的 REST 请求正文：

    node .github/scripts/trusted_admission.cjs plan APP_ID

第一项“受支持目标分支保护”要求 PR、squash、阻断线程解决、禁止删除与强推，以及基线更新后的全部必需检查。Project gate、Lifecycle gate、Release bytes gate 仍绑定 GitHub Actions；Trusted metadata gate 绑定独立 App。该规则集没有 bypass actor。新分支创建时不要求已经存在检查，创建后的更新仍受保护。

第二项“受控 squash 合并”限制受支持分支的更新，仅允许该独立 App 绕过这一项更新限制。该 App 无法绕过第一项的质量规则。已有主分支和发行标签规则继续保留。

启用顺序：

1. 审查并固定部署版本，安装 App，启动持续校验，核对真实检查的 App 来源。
2. 为研究队列准备与受信基线一致的三项工作流；现有缺少检查的 PR 应补齐检查，不能填造成功状态。
3. 在独立验收仓库验证相同规则和执行实现，包括错误消息、错误来源、检查缺失、未解决线程和合法输入。验收部署的仓库身份须固定到验收仓库。
4. 经仓库负责人授权，创建两项规则集并读取实际生效规则；保留原规则。每个受支持目标分支均须能取得完整检查。
5. 验证普通账号及其他集成不能更新目标分支，App 可提交合规 squash，但质量检查失败或线程未解决时仍被拒绝。

个人账号仓库不能直接使用仅面向 Enterprise 组织的提交元数据限制与必需工作流规则。这里通过独立 App 的固定实现和分支更新限制约束最终消息。适用条件见 GitHub [可用规则](https://docs.github.com/en/enterprise-cloud@latest/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#metadata-restrictions)；检查来源和分支保护见 [Rulesets 规则](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)及 [Rules REST API](https://docs.github.com/en/rest/repos/rules)。

## 经批准的合并

GitHub 仅向具有规则写入权限的主体返回旁路名单，普通 App 的响应会省略该字段。启用或修改规则后，由仓库管理员在独立运行目录读取完整版本：

    node .github/scripts/trusted_admission.cjs audit-rules APP_ID RULES_FILE

该命令从 GOVERNANCE_RULES_AUDIT_TOKEN 读取管理员令牌，只执行读取请求。文件绑定仓库、App、规则内容、旁路名单和 GitHub 更新时间，存放在贡献分支之外的受限目录。合并进程通过 GOVERNANCE_RULES_FILE 读取此文件，并与 GitHub 当前规则内容、更新时间和 App 实际旁路状态逐项比对。文件缺失、规则变化或读回字段异常时拒绝合并，重新核对后才能更新版本文件；App 本身不取得 Administration 权限。字段可见范围见 [Get a repository ruleset](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset)。

watch 与 check 只写入检查结果。负责人批准具体 PR 后，在独立运行目录执行：

    node .github/scripts/trusted_admission.cjs merge PR_NUMBER

程序先重新检查同一 HEAD 的全部 PR，再读取目标分支实际生效的两项规则，验证质量门禁和更新来源。随后重新读取共享 HEAD 的完整开放集合，逐项重新验证全部 PR，再次核对规则和最终集合。新增、关闭、重开、改目标、改标题或正文、HEAD 或基线变化都会拒绝沿用此前结果。只有可观察快照一致时，才向 GitHub 明确发送已校验的 commit_title、commit_message、HEAD SHA 与 squash 方法，不接受另行覆盖消息。

GitHub 的合并请求通过 SHA 参数锁定目标 HEAD；其他 PR 元数据、共享 HEAD 集合和目标基线仍可在末次读取后变化。合并前比较已读取的快照，合并后读取实际提交，核对提交身份、父提交及完整消息。

独立准入实现必须以 main 为最终合并目标，合并入口拒绝中间治理分支。存在未合并依赖时，先让依赖经独立验收和批准后单独 squash 进入 main，再在开发分支上用合规提交消息同步主分支：

    git fetch origin
    git merge --no-ff -m "chore(governance): 同步主分支已合并的治理依赖" origin/main

非快进同步提交与其他新增提交适用相同的消息规则；须明确指定符合实际变更的消息，不能沿用 Git 默认的 `Merge branch ...` 或 `Merge remote-tracking branch ...` 标题。随后核对相对 main 的剩余差异并将 PR 目标改为 main。依赖经 squash 后，其原提交仍可能出现在开发分支的提交列表中，必须与同步提交一起完整校验。重新检查最终 squash 消息、完成独立验收并获批准后再单独 squash，使各技术对象分别形成可回退提交。

GitHub 的合并接口继续执行原生质量检查和线程要求。合并响应丢失后只查询结果，不盲目重试。成功后读取真实提交对象，核对消息、父提交与返回的提交身份；仅在全部一致后报告合并成功。接口参数见 [Merge a pull request](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)。

停止常驻进程或撤销 App 会使新的校验无法完成；分支规则继续阻止其他主体更新。恢复时重新启动经审查的部署副本并执行当前 PR 检查，不通过增加人工 bypass 恢复。
