# 离线发行字节合同

## 范围

`scripts/release.py` 描述和比较本地源码、UTF-8 发行说明、可选确定性归档、严格校验和文件，以及调用者已经取得的发行元数据。通用清单不固定托管目标；可安装发行入口在运行时绑定 `owner/name` 仓库和完整源码修订，并核对标签实际解析到该修订。工具不读取凭据、不创建标签、不上传资产，也不发布。

版本使用三段数字格式，标签由工具确定为 `v<version>`。发行说明首行须为：

```text
# v<version> — <title>
```

首行使用 LF 结束，正文按完整 UTF-8 字节绑定。资料截止日不作为软件版本或发布日期。

## 清单

清单格式为 `bilibili-meme-atlas-release/v1`，顶层恰好包含：

- `format`、`version`、`title`；
- `source`：完整项目文件数和运行时目录身份；
- `notes`：发行说明字节数和运行时身份；
- `package`：`source_only`，或 `skill_zip` 的固定文件名、文件数、字节数和运行时身份。

`source_only` 不接受自定义资产，也不满足安装前置条件。`skill_zip` 的资产目录须恰好包含 `bilibili-meme-atlas-v<version>.zip` 和 `SHA256SUMS-v<version>.txt`。校验和文件只有一行：归档摘要、两个 ASCII 空格、归档文件名和单个 LF。首次发行和任何供安装的发行都必须使用 `skill_zip`。

## 生成与核对

先验证项目并在项目外准备说明和可选资产，再将 `describe` 的标准输出保存为清单：

```bash
python3 scripts/verify.py
python3 scripts/package.py build --output /已有目录/assets/bilibili-meme-atlas-vX.Y.Z.zip
python3 scripts/release.py describe \
  --notes /已有目录/notes.md \
  --assets-dir /已有目录/assets
```

随后可分别检查结构、重新核对全部本地字节，并生成提供方中立的发行对象：

```bash
python3 scripts/release.py lint --manifest /已有目录/release.json
python3 scripts/release.py check-source \
  --manifest /已有目录/release.json \
  --notes /已有目录/notes.md \
  --assets-dir /已有目录/assets
python3 scripts/release.py prepare-request \
  --manifest /已有目录/release.json \
  --notes /已有目录/notes.md \
  --assets-dir /已有目录/assets
```

`prepare-request` 输出标签、标题、正文、草稿/预发行布尔值和资产身份，不含提供方或仓库动作。调用方取得发行元数据和实际资产字节后，可在本地比较：

```bash
python3 scripts/release.py verify-release \
  --manifest /已有目录/release.json \
  --notes /已有目录/notes.md \
  --assets-dir /已有目录/assets \
  --metadata /已有目录/observed.json
```

回读元数据恰好包含 `tag`、`name`、`body`、`draft`、`prerelease` 和 `assets`。每个资产项必须恰好包含 `name`、实际 `size` 和带 `sha256:` 前缀的可信 `digest`；仅有名称或长度的回读不能证明发行字节一致。重复、额外、缺少或字节不同均拒绝。此入口只比较所给本地输入，不获取远端内容。

## 可安装发行门禁

仓库建立且准备发行时，从要发布的准确提交检出项目，运行完整验证并生成 `skill_zip` 清单。将实际仓库和完整源码修订作为运行时参数，不把具体提交值写入项目文件：

```bash
python3 scripts/release.py prepare-installable-request \
  --manifest /已有目录/release.json \
  --notes /已有目录/notes.md \
  --assets-dir /已有目录/assets \
  --repository OWNER/REPOSITORY \
  --source-revision SOURCE_REVISION
```

发布后，从同一仓库回读发行，将标签解析为最终提交，并取得可信的资产字节数和 SHA-256 摘要。可安装回读对象比通用对象多出 `repository` 和 `tag_target`：

```bash
python3 scripts/release.py verify-installable-release \
  --manifest /已有目录/release.json \
  --notes /已有目录/notes.md \
  --assets-dir /已有目录/assets \
  --metadata /已有目录/observed.json \
  --repository OWNER/REPOSITORY \
  --source-revision SOURCE_REVISION
```

只有该入口返回 `MATCH` 且 `installable: true` 后，该发行才满足安装前置条件。仓库不一致、标签指向其他提交、纯源码发行、资产缺摘要或任何字节差异都不得进入安装。
