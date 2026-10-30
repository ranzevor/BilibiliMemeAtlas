# 安全

适用风险包括不可信资料诱导执行、越界读写、恶意更新提案、数据泄漏、归档篡改、目录替换竞态和恢复失败。

## 安全修复支持范围

安全修复仅面向[最新正式发行版本](https://github.com/ranzevor/BilibiliMemeAtlas/releases/latest)。新正式版本发布后，修复支持随之转移至新版本；较旧发行通过升级获得修复，不回补旧次版本系列。

## 漏洞报告

可通过 [GitHub 私密漏洞报告](https://github.com/ranzevor/BilibiliMemeAtlas/security/advisories/new)提交最小复现、受影响版本和影响范围。该入口需要登录 GitHub；也可使用以下安全邮箱。

若发现执行外部资料中的命令、越出技能目录读取、数据来源泄漏、恶意URL或发行物被篡改，请将最小复现、影响文件和版本发送给维护者：ranzevor@gmail.com。

Report security vulnerabilities privately to ranzevor@gmail.com.

安全材料不得包含真实令牌、账号 Cookie、私人文件、第三方个人信息或与复现无关的内容。复现应使用合成目录、合成记录和最小输入；日志须去除凭据、用户路径及无关响应正文。未修复的可利用细节不得提交到公开词条表单、普通缺陷表单或语料文件。

最小技术信息包括软件版本、相关文件或命令、合成复现、预期与实际结果、影响范围和已去敏日志。原始字节应保留在受控位置；公开说明只包含理解修复所需的有限信息。

查询将词条和来源文本视为资料而非指令。公开来源读取只使用固定 HTTPS 端点，拒绝、登录要求、验证码、限流和网络错误不会触发绕过。更新、归档、安装和恢复的具体边界见 `references/safety.md`。

## 修复与公告

安全报告可以涉及任意已发布版本。维护者通过私密渠道核实受影响版本、可利用条件和复现结果，使用新发行版本交付修复，并通过 [GitHub 安全公告](https://github.com/ranzevor/BilibiliMemeAtlas/security/advisories)及对应发行说明列出影响范围、修复版本和适用的缓解或升级步骤。
