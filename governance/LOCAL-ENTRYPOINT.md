# 本地提交与 PR 检查

## 本地提交消息校验

本地 `commit-msg` 钩子及其校验脚本安装在 Git 私有管理目录，不从待审分支的工作区执行。在仓库根目录使用 Git Bash，从可信主分支安装：

```sh
bash -e -o pipefail <<'SH'
safe_hooks="$(mktemp -d)"
trap 'rm -rf -- "$safe_hooks"' EXIT
git -c core.hooksPath="$safe_hooks" fetch origin main
MSYS_NO_PATHCONV=1 git show refs/remotes/origin/main:scripts/install-governance-hook.sh | bash -e
SH
```

首次安装先在空的 Git 私有外部临时目录下禁用已有钩子执行远端获取，然后仅从可信主分支读取安装器与校验器，完整准备钩子后才切换 `core.hooksPath`。读取失败、校验器为空或存在其他生效钩子时会停止，原有钩子配置保持不变。旧 `core.hooksPath=.githooks` 可迁移到 Git 私有目录；若旧位置存在自定义钩子，须先检查并处理其行为，安装器不会自动执行或覆盖工作区中的脚本。重复安装可更新由安装器管理的可信副本。

提交时自动校验中文 Conventional Commit 消息；`--no-verify` 可跳过本地检查，远端门禁不受影响。

## 创建 PR 前检查

`.github/scripts/governance_gate.cjs` 提供 `message-file`、`preflight-json` 和 `event-git` 模式。使用安装在 Git 私有目录的可信副本执行本地预检：创建包含真实 `pull_request`、`commits` 和 `files` 字段的 JSON，运行 `node "$(git rev-parse --path-format=absolute --git-path hooks)/governance_gate.cjs" preflight-json <文件路径>`。执行 PR 预检前，在 Git 私有钩子目录安装解析器：`npm install --prefix "$(git rev-parse --path-format=absolute --git-path hooks)" --no-save --no-package-lock --ignore-scripts --no-audit --no-fund markdown-it@15.0.3`。提交消息钩子不依赖该解析器。此外应核对已有 PR、实际 Diff、来源权利、授权与测试。

现有 `verify.yml` 在 PR 质量矩阵中执行 `event-git`，从 PR 事件及检出的 Git 合并候选读取 HEAD、提交记录、变更文件和元数据。不向校验脚本提供写入令牌，不增加工作流。本检查需要有效的 `Project gate` 状态来源。

元数据校验不代替代码、数据、来源和变更范围的独立审查。现有生命周期、发行字节验证与三项工作流约束保持不变。

## 合并前检查与 squash

依赖其他独立技术 PR 的变更，先将依赖单独合入 main，再在开发分支以符合实际变更的中文消息执行非快进同步。例如 `git merge --no-ff -m "chore(governance): 同步主分支已合并的治理依赖" origin/main`。随后将 PR 目标设为 main，核对剩余差异和完整提交列表，包括依赖的原提交与同步提交。

经仓库负责人批准后，通过已授权的 GitHub 身份执行合并检查和操作。

1. 读取当前 PR、完整分页的提交与文件、审查讨论及目标分支生效规则，核对三项必需检查的来源、对应提交和成功结果。
2. 将准确快照交给可信主分支的 `governance_gate.cjs preflight-json` 校验。用该快照的标题和正文生成最终 squash 消息，并直接调用同一校验器导出的 `commitErrors()` 校验完整原文。
3. 合并前重新读取 HEAD、基线、标题、正文、两侧分支和仓库，并重新核对共享 HEAD 的相关开放 PR。发生变化时，针对变化重新验证并完成独立审查。
4. 向合并接口明确传入已验证的 `commit_title`、`commit_message`、当前 HEAD 的 `sha` 和 `merge_method=squash`。
5. 读取实际合并结果，核对完整消息、父提交及授权署名。响应结果不明时先查询 PR 和提交，依据实际结果处理后续操作。接口参数见 [GitHub 合并接口](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)。
