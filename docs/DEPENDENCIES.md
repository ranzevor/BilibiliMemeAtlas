# 依赖

## 运行时

项目运行基线为 CPython 3.10+，只使用标准库。主要模块包括 `argparse`、`datetime`、`hashlib`、`json`、`pathlib`、`tempfile`、`unittest`、`urllib` 和 `zipfile`；POSIX 中断恢复额外使用标准库 `fcntl`。

项目没有第三方 Python 包、JavaScript 包、模型 SDK、数据库驱动或包管理器锁文件。查询不联网；`research.py` 和 `update.py discover` 仅访问代码中固定的公开 B站 HTTPS 端点，不使用账号凭据。

## 仓库验证

`.github/workflows/` 中的手动只读工作流使用 GitHub 托管 Ubuntu runner，固定 `actions/checkout` 与 `actions/setup-python` 动作版本，并显式在 CPython 3.10 和 3.13 上运行检查；每个作业先输出 `python -VV` 供日志确认实际解释器。工作流不安装第三方项目依赖，不读取发布凭据，也不执行标签、上传或发布。

## 静态资源

`assets/cover.webp` 和 `assets/icon.png` 随项目保存。README 的 Python 版本与许可徽章通过 Shields.io 加载。项目自有资源的许可见根目录 `LICENSE`；外部语料来源的权利边界见 `CONTENT_RIGHTS.md`。
