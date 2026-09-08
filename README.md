# Karvis · 微信端个人管家

三个企业微信自建应用 = 三个独立助手。发到哪个应用就是哪个助手，**零命令语法**。

| 应用显示名 | 标识 | 回调路径 | 干什么 |
|---|---|---|---|
| 阿龙管家 | `life` | `/wecom/life` | 收集箱、待办、随手记，定期提醒 |
| 埼玉教练 | `fitness` | `/wecom/fitness` | 训练记录 + 次天感受闭环 |
| 阿尼亚督导 | `mood` | `/wecom/mood` | CBT 五步情绪梳理 + 周复盘 |

```
企业微信 ←→ 回调 /wecom/{agent}
              ↓ 验签解密（WXBizMsgCrypt）
          FastAPI 网关（立刻返回 success）
              ↓ 后台异步
          Agent（人格 MD + 会话历史）
              ↓
          DeepSeek API + SQLite
              ↓
          主动推送（message/send）
```

---

## 一、现在的状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 网关 + 企微回调 + DeepSeek，能收能回 | **代码就绪，待部署** |
| P1 | 三个助手的记录能力（存 / 查 / 回填） | 代码已包含 |
| P2 | 分析反馈：次天闭环、CBT 五步、自动分类 | 人格文件已定义 |
| P3 | 定时推送（每日待办 / 周整理 / 情绪周复盘） | 代码已包含，开关关闭 |
| P4 | 备份 COS、监控、可选 HTTPS | 未开始 |

**开工顺序：先用「阿龙管家」跑通全链路，验证无误后把另外两个应用的 Secret/Token/AESKey 填进 `.env` 重启即可**（代码已支持三个）。

---

## 二、你需要提供的东西

| 项 | 状态 |
|---|---|
| 企业微信 CorpID | ✅ `wwdab8ed1464222a76` |
| 阿龙管家 AgentId/Secret/Token/AESKey | ✅ 已写入 `.env` |
| 埼玉教练 AgentId/Secret/Token/AESKey | ⬜ 待补 |
| 阿尼亚督导 AgentId/Secret/Token/AESKey | ⬜ 待补 |
| DeepSeek API Key | ⬜ 待补（[platform.deepseek.com](https://platform.deepseek.com)） |
| 服务器公网 IP / 端口 | ⬜ 部署时定（默认 `9000`） |
| 你的企微 UserID（定时推送用） | ⬜ 可选 |

---

## 三、部署步骤

### 1. 服务器准备

```bash
# 腾讯云轻量（Ubuntu/Debian），装 Docker
curl -fsSL https://get.docker.com | bash
systemctl enable --now docker

# 放行端口（也要在腾讯云控制台的防火墙里放行 TCP 9000）
ufw allow 9000/tcp
```

### 2. 上传代码

```bash
mkdir -p /opt/karvis
# 方式一：本机 scp
scp -r karvis/* root@<服务器IP>:/opt/karvis/
# 方式二：服务器 git clone（推荐，后续更新方便）
```

### 3. 填 `.env`

`.env` 已含阿龙管家的真实凭证，补两处：

```bash
DEEPSEEK_API_KEY=sk-xxxx
KARVIS_OWNER_USERID=你的企微UserID   # 定时推送用，暂不开可留空
```

上传到 `/opt/karvis/.env`，`chmod 600`。

### 4. 启动

```bash
cd /opt/karvis
bash deploy/install.sh
# 等价的手动命令：docker compose up -d --build
```

### 5. 自检（不依赖企微）

```bash
# 验证 DeepSeek + 三个助手是否真的能思考
docker compose exec karvis python tools/selftest.py

# 验证企微凭证是否正确（这一步最常失败）
docker compose exec karvis python tools/check_wecom.py
```

### 6. 企业微信后台配置（每个应用都要做）

**① 企业可信 IP**（漏了 = 能收消息但不回复）
我的企业 → 企业信息 → 页面底部「企业可信 IP」→ 填服务器公网 IP

**② 接收消息服务器配置**
应用管理 → 对应应用 → 「接收消息」→ 设置 API 接收：

| 应用 | URL | Token | EncodingAESKey |
|---|---|---|---|
| 阿龙管家 | `http://<IP>:9000/wecom/life` | `.env` 的 `WECOM_LIFE_TOKEN` | `WECOM_LIFE_AESKEY` |
| 埼玉教练 | `http://<IP>:9000/wecom/fitness` | `WECOM_FITNESS_TOKEN` | `WECOM_FITNESS_AESKEY` |
| 阿尼亚督导 | `http://<IP>:9000/wecom/mood` | `WECOM_MOOD_TOKEN` | `WECOM_MOOD_AESKEY` |

**③ 可见范围**：确保你本人在这个应用的可见成员内，否则发消息收不到回复。

点「保存」时企微会 GET 校验 URL，日志出现 `回调校验通过` 即成功。

### 7. 验证

在应用里发一句话。日志：

```bash
docker compose logs -f karvis
```

应看到 `收到 [life] from=... type=text`。

### 8. 本地联调（不部署服务器也能验证）

在这台机器上就能跑通除「服务器公网可达」以外的全部链路：

```bash
# 1) 起服务
python -m uvicorn gateway.main:app --host 127.0.0.1 --port 9000

# 2) 验证凭证与加解密（不联网）
python tools/test_crypto.py life

# 3) 模拟企微回调 URL 校验
python tools/mock_callback.py life --verify

# 4) 模拟企微发一条消息
python tools/mock_callback.py life "记一下明天要交周报"
```

第 4 步返回 `200 success` 即说明**验签、解密、路由、异步任务**全通。
此时助手会尝试把回复推送到企微，本机联调大概率报 `60020 not allow to access from your ip`——
这是正常的，说明 Secret 有效、只是 IP 不在可信列表；部署到服务器并配可信 IP 后即可正常推送。

把 `mock_callback.py` 的第三参数换成服务器地址，就能对线上服务做同样的验证：

```bash
python tools/mock_callback.py life "你好" http://49.235.107.213:9000
```

---

## 四、排错表

| 现象 | 原因 | 处理 |
|---|---|---|
| 保存回调 URL 时报「签名验证失败」 | Token 与 `.env` 不一致 | 核对 `WECOM_<NAME>_TOKEN` |
| 能收到消息但不回复 | **企业可信 IP 未配**，或应用可见范围不含你 | 配可信 IP；检查可见范围 |
| 日志报 `获取 access_token 失败` | Secret 错，或 IP 不可信 | 核对 Secret；配可信 IP |
| 日志报 `发送消息失败: 60020` | IP 不在可信列表 | 同上 |
| 回复「暂时无法思考」 | DeepSeek Key 未配/失效/余额不足 | 填 `DEEPSEEK_API_KEY` |
| 无日志产生 | 防火墙未放行 9000；或回调 URL 填错路径 | 检查路径 `/wecom/<标识>` |
| 助手回复很慢 | 每次两条 LLM 调用（抽取 + 生成） | 正常，约 5–15 秒 |
| `60020 ... from ip: 118.x.x.x`（宽带 IP） | 本机联调，IP 不在可信列表 | 正常。Secret 是对的，换到服务器并配可信 IP 即可 |
| `40001` / 获取 token 失败 | Secret 填错 | 核对 `WECOM_<NAME>_SECRET` |

---

## 五、目录说明

```
karvis/
├── gateway/               # 服务代码
│   ├── main.py            # 回调入口与路由
│   ├── wxcrypt.py         # 企微加解密
│   ├── wecom_api.py       # access_token 缓存 + 主动推送
│   ├── db.py              # SQLite + FTS5 + Markdown 记忆
│   ├── llm.py             # DeepSeek 封装
│   ├── scheduler.py       # 定时推送（P3）
│   └── agents/            # 三个助手逻辑
├── assistants/            # 人格文件（改这里就能调助手行为，不用改代码）
│   ├── _shared/profile.md
│   ├── life/AGENTS.md
│   ├── fitness/AGENTS.md
│   └── mood/AGENTS.md
├── tools/                 # 自检脚本
├── deploy/                # 部署脚本与 systemd
└── data/                  # 数据库与记忆（备份这个目录）
```

**改助手行为**：直接编辑 `assistants/<标识>/AGENTS.md`，重启容器生效。

---

## 六、备份

```bash
# 每天凌晨打包到本地（临时方案）
0 3 * * * cd /opt/karvis && tar czf /opt/karvis/backup/karvis_$(date +\%F).tar.gz data/

# 上 COS（推荐，异地容灾）：安装 coscli 后
# coscli cp /opt/karvis/backup/ cos://<bucket>/karvis/ -r
```

---

## 七、安全须知

- `.env` 含企业微信密钥，**绝不入库**，权限 600。
- 回调走 HTTP + 裸 IP：消息体已用 EncodingAESKey 加密，但如需 HTTPS，后续加域名 + Caddy 即可（需备案）。
- 健身与情绪数据为 L3 私密，代码层面与 life 助手隔离，不进共享层。
