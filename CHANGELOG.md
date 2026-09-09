# 变更记录（CHANGELOG）

> 所有版本变更都必须记在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。
>
> **一次发布 = 一个 Git tag（`vX.Y.Z`）+ 一条本文件记录 + 服务器 `/opt/karvis/VERSION`。**
> 三者缺一，这个版本就等于没被记录。

## 版本号怎么定

| 类型 | 什么时候用 | 例子 |
|---|---|---|
| **major** `X.0.0` | 架构变更、不兼容旧数据/旧配置 | 换数据库、换框架、回调协议变更 |
| **minor** `0.X.0` | 新增能力，向后兼容 | 新增一个助手、新增一类记录 |
| **patch** `0.0.X` | 修 bug、调人格文案、改文档 | 修脚本误退出、改 AGENTS.md |

`0.x` 阶段（MVP 后持续迭代期）：新功能走 minor，修修补补走 patch。

---

## [Unreleased]

开发中、尚未发布的改动（发布时由 `deploy/release.sh` 自动转成版本条目）。

---

## [0.2.0] - 2026-09-10

- 埼玉教练从记事本升级为教练：训练档案（data 层，顺带一问补全）+ PR/1RM 追踪（Epley）+ 渐进超负荷建议 + 简写解析

---

## [0.1.0] - 2026-09-09 · MVP

首个可用版本：三个助手全链路跑通（企微 → 网关 → DeepSeek → 主动推送）。

### 新增

- **企微网关**：自实现 WXBizMsgCrypt 加解密，`/wecom/{life|fitness|mood}` 三条独立回调；先回 `success` 再异步处理，规避企微 5 秒超时
- **三个助手**
  - 阿龙管家 `life`：收集箱、待办、随手记
  - 埼玉教练 `fitness`：训练记录 + 次天感受闭环
  - 阿尼亚督导 `mood`：CBT 五步情绪梳理 + 周日复盘
- **记忆层**：SQLite + FTS5，结构化记录 + `data/memory/{agent}/YYYY-MM-DD.md` 双写（人可直接读）
- **人格系统**：`assistants/{agent}/AGENTS.md`，改行为只改 Markdown，不动代码
- **定时任务框架**：`gateway/scheduler.py` 已实现，默认关闭（`ENABLE_SCHEDULER=0`）
- **部署体系**：Docker + `deploy/setup_from_git.sh`（Git 拉取，镜像站失败自动降级 tarball 兜底）
- **运维脚本**：`deploy/update.sh`、`deploy/create_env.sh`
- **文档**：`deploy/部署SOP_可复用手册.md`（含 14 条排错表）

### 已知边界（不在本版本内）

- 数据备份未启用（见 BACKLOG B1）
- 定时推送未启用（需填 `KARVIS_OWNER_USERID`）
- 无图片/语音等多模态输入

### 部署信息

| 项 | 值 |
|---|---|
| 服务器 | 腾讯云轻量 `49.235.107.213`，端口 `9000` |
| 安装目录 | `/opt/karvis` |
| 仓库 | `github.com/Homing-tech/MyKarvis`，tag `v0.1.0` |
