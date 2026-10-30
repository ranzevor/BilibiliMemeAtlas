# 依赖

## 运行时

项目运行基线为 CPython 3.10+，只使用标准库。主要模块包括 `argparse`、`datetime`、`hashlib`、`json`、`pathlib`、`tempfile`、`unittest`、`urllib` 和 `zipfile`；POSIX 中断恢复额外使用标准库 `fcntl`。

项目没有第三方 Python 包、JavaScript 包、模型 SDK、数据库驱动或包管理器锁文件。查询不联网；`research.py` 和 `update.py discover` 仅访问代码中固定的公开 B站 HTTPS 端点，不使用账号凭据。

## 仓库验证

`.github/workflows/verify.yml` 在 GitHub 托管 Ubuntu 24.04 与 Windows Server 2022 上验证 CPython 3.10 和 3.13；另外两个工作流使用 Ubuntu 24.04。所有工作流在面向 `main` 的 PR 创建、重新打开或更新时自动运行，并保留手动触发入口和只读权限；矩阵作业先输出 `python -VV`。三个汇总作业使用空权限，仅在各自完整矩阵成功后通过；主分支必需检查使用稳定名称 `Project gate`、`Lifecycle gate` 和 `Release bytes gate`，执行步骤见[合并检查](VERIFICATION.md#合并检查)。`actions/checkout` 与 `actions/setup-python` 均固定到完整提交 SHA；工作流不安装第三方项目依赖，不读取发布凭据，也不执行标签、上传或发布。

`.github/dependabot.yml` 为 GitHub Actions 配置版本更新，每周一 09:00（Asia/Shanghai）安排检查，最多同时保留 5 个更新 PR。维护者合并更新前，核对完整提交 SHA 固定及 PR 自动验证结果。

## 静态资源

`assets/cover.webp` 和 `assets/icon.png` 随项目保存。README 的 Python 版本与许可徽章通过 Shields.io 加载。项目自有资源的许可见根目录 `LICENSE`；外部语料来源的权利边界见 `CONTENT_RIGHTS.md`。
