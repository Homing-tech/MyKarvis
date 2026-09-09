# 个人 AI 管家部署 SOP（可复用手册）

> 以「企业微信自建应用 ×N + 云服务器 + 自研网关 + DeepSeek」为模板。
> 2026-09-08 首次完整跑通（3 个助手全绿）。换项目时只需替换第 9 节「复用改动清单」里的字段。

---

## 0. 一页速览

| 项 | 本次落定值 |
|---|---|
| 入口 | 企业微信自建应用 ×3（微信内直接用，无需装企微 App） |
| 助手 | 阿龙管家 life / 埼玉教练 fitness / 阿尼亚督导 mood |
| 服务器 | 腾讯云轻量 49.235.107.213（4C4G40G，Docker CE） |
| 端口 | 9000 |
| 回调 | `http://49.235.107.213:9000/wecom/{life\|fitness\|mood}` |
| 大脑 | 自研 FastAPI 网关 + DeepSeek（`deepseek-chat`） |
| 代码 | GitHub `Homing-tech/MyKarvis`（main） |
| 安装目录 | `/opt/karvis`（`.env` 权限 600，不入库） |

**一句话原理**：每个助手 = 一个企微自建应用 = 一条独立回调路径 = 一套独立 Token/AESKey。发到哪个应用就是哪个助手，**零命令语法**。

---

## 1. 关键决策（为什么这么做）

| 决策 | 结论 | 依据 |
|---|---|---|
| 微信入口 | 企微自建应用，而非公众号 | 公众号强制 80/443 + 备案域名；企微**允许任意端口** |
| 域名/备案 | **不需要** | 自建应用回调可直接 `http://公网IP:端口/path`（上游《回调URL配置指南》方案 B） |
| 大脑实现 | 自研轻量内核，不用 Pi | 4G 内存跑不动常驻 Pi 子进程，FastAPI 直接编排大模型即可 |
| 记忆存储 | SQLite + FTS5 | 上千 md 文件检索会慢；结构化记录 + 每日 md 双写 |
| 超时处理 | 先回 `success`，再异步处理并主动推送 | 企微要求 5 秒内响应，大模型必然超时 |

---

## 2. 前置清单

**账号侧**
- [ ] 企业微信组织（个人可注册，无需营业执照）→ `work.weixin.qq.com`
- [ ] DeepSeek API Key（35 字符）→ `platform.deepseek.com/api_keys`

**服务器侧**
- [ ] 公网 IP（用 `curl -4 ifconfig.me` 查，**不要用 `ip addr`**，那是内网 IP）
- [ ] 防火墙放行目标端口（腾讯云：控制台 → 防火墙 → 添加规则 TCP 9000）
- [ ] Docker CE + docker compose
- [ ] 磁盘剩余 ≥ 3G（`df -h /`）

**凭证总表（每个应用 4 项，缺一该助手返回 404「未配置」）**

| 变量 | 来源 | 校验 |
|---|---|---|
| `WECOM_CORP_ID` | 我的企业 → 企业信息 | `ww` 开头 |
| `WECOM_{KEY}_AGENT_ID` | 应用详情页 | 纯数字 |
| `WECOM_{KEY}_SECRET` | 应用详情 → Secret 查看 | **只展示一次**；复制常带前导 tab，需 strip |
| `WECOM_{KEY}_TOKEN` | 接收消息 → 随机获取 | 3–32 位，可反复查看 |
| `WECOM_{KEY}_AESKEY` | 接收消息 → 随机获取 | **必须 43 位**，可反复查看 |

`{KEY}` = `LIFE` / `FITNESS` / `MOOD`，与内部 ID `life/fitness/mood` 对应（见 `gateway/config.py`）。

---

## 3. 第一步：企微侧配置（浏览器）

> 上游参考：[企微注册与应用创建](https://github.com/sameencai/KarvisForYou/blob/main/docs/%E4%BC%81%E5%BE%AE%E6%B3%A8%E5%86%8C%E4%B8%8E%E5%BA%94%E7%94%A8%E5%88%9B%E5%BB%BA.md) ｜ [企微应用配置指南](https://github.com/sameencai/KarvisForYou/blob/main/docs/%E4%BC%81%E5%BE%AE%E5%BA%94%E7%94%A8%E9%85%8D%E7%BD%AE%E6%8C%87%E5%8D%97.md)

1. **注册企业微信** → 记下 **企业 ID（CorpID）**
2. **应用管理 → 自建 → 创建应用**，逐个建（本次 3 个）
   - ⚠️ **可见范围必须把自己加进去**，否则应用不出现在你的企微/微信里
3. 每个应用详情页记下 **AgentId**；点 **Secret → 查看**（扫码验证）→ 立即保存
4. **接收消息 → 设置 API 接收 → 随机获取** → 得到 **Token** 与 **EncodingAESKey**
5. **URL 先空着不要点保存**（见第 6 节：顺序错了必然失败）
6. **我的企业 → 微信插件 → 邀请关注二维码**，微信扫码 → 之后在微信里直接和助手聊天，可置顶

---

## 4. 第二步：服务器侧准备

```bash
# 查公网 IP
curl -4 ifconfig.me
# 磁盘
df -h /
# 端口是否被占（旧服务/旧容器）
ss -lntp | grep 9000
```

**端口被占的正确处理**：
```bash
docker ps -a | grep 9000        # 找到映射该端口的容器
docker stop <容器名> && docker rm <容器名>   # ✅ 正确
# ❌ 不要 kill docker-proxy：会被 dockerd 自动拉起，端口永远释放不掉
```

---

## 5. 第三步：代码部署（Git + tarball 兜底）

> **硬约束**：`.env` 永不入库（`.gitignore` 锁死，`deploy/create_env.sh` 同样 ignore）。

```bash
# 本地：仓库准备（防 CRLF 导致服务器报 bad interpreter）
git init -b main && git config core.autocrlf=input
git remote add origin <仓库地址> && git push -u origin main

# 服务器：一条命令部署（脚本自动备份旧项目 → 停旧服务 → 拉代码 → 构建 → 自检）
curl -fsSL <raw/脚本URL>/setup_from_git.sh -o /tmp/s.sh \
  && sudo bash /tmp/s.sh <仓库地址> main
```

**脚本下载务必先验真**（镜像站缓存会导致拿到残缺旧版）：
```bash
wc -c /tmp/s.sh     # 与仓库实际字节数比对
bash -n /tmp/s.sh   # 语法检查
```

**已知坑**：GitHub 镜像站（ghfast / gh-proxy / ghproxy）**只转发 GET，不转发 git 的 upload-pack POST**，`git clone` 必然全部失败 → 脚本内已加 **tarball GET 兜底**（下载 `main.tar.gz` 解包）。日志里看到前几个源失败属预期，**不要中断**。
*已挂的镜像：gh.llkk.cc、gitclone.com（勿用）。*

### 5.1 代码迭代：本地改动 → Git → 服务器

> 这是「改完代码 / 改完人格」后的标准回路。人格改动只涉及 `assistants/*/AGENTS.md`，也要走同一条路。

```bash
# ① 本地提交（在 karvis/ 目录下）
git status --short                 # 确认没有 .env / data/ 被列进来
git check-ignore -v .env           # 期望输出 .gitignore 规则，证明已忽略
git add -A && git commit -m "feat/fix/docs: <一句话说明>"
git push origin main
```

**提交信息约定**（与本次仓库保持一致，便于日后回溯）：

| 前缀 | 用途 |
|---|---|
| `feat:` | 新能力（如新增一个助手） |
| `fix:` | 修 bug（如脚本 `set -e` 误退出） |
| `docs:` | 文档/人格文案（如改 `AGENTS.md`、补 SOP） |

```bash
# ② 服务器拉取（不动 .env 与 data，脚本已内置）
sudo bash /opt/karvis/deploy/update.sh

# ③ 若改的是人格/配置而非代码，通常再重建一次即可
cd /opt/karvis && docker compose up -d --force-recreate
```

**分支策略**：单人项目直接推 `main` 即可；若想先灰度，另开分支并把部署命令的第二个参数换成分支名（`setup_from_git.sh <仓库> <分支>`），验证通过再合回 `main`。

**私有仓库**：脚本支持 `GITHUB_TOKEN`，在服务器执行前导出即可（tarball 兜底路径会用）：
```bash
export GITHUB_TOKEN=<你的token>; sudo -E bash /opt/karvis/deploy/setup_from_git.sh <仓库> main
```

### 5.2 回滚

```bash
# 方案 A（推荐）：revert 一个提交，历史干净且可追溯
git revert <坏提交hash> && git push origin main
sudo bash /opt/karvis/deploy/update.sh

# 方案 B：临时回到某个历史版本（tarball 模式无 .git 时用）
curl -fsSL https://ghfast.top/https://github.com/<用户>/<仓库>/archive/<commit>.tar.gz -o /tmp/old.tar.gz
# 解包覆盖到 /opt/karvis 后 force-recreate
```

### 5.3 Git 安全清单（每次 push 前过一遍）

- [ ] `.gitignore` 含 `.env`、`data/`、`*.db`（`git check-ignore -v .env` 验证）
- [ ] `git status` 里没有密钥、没有聊天记录导出
- [ ] 文档里不贴真实 Secret / AESKey（只写变量名与长度规则）
- [ ] **若误提交过密钥：立刻去平台作废重建**，仅删文件无效——历史 commit 里仍然存在

---

## 6. 第四步：凭证写入（顺序关键）

**正确顺序：企微生成 Token/AESKey → 写入服务器 `.env` → 重建容器 → 回企微点保存回调。**
反过来的话，保存时企微会立刻用 Token 验签 URL，而服务端还没配置 → 404 → 保存失败。

```bash
# 模板：用 sed 覆盖（一整行，避免多行粘贴问题）
sudo sed -i -e 's/^WECOM_FITNESS_AGENT_ID=.*/WECOM_FITNESS_AGENT_ID=<值>/' \
  -e 's/^WECOM_FITNESS_SECRET=.*/WECOM_FITNESS_SECRET=<值>/' \
  -e 's/^WECOM_FITNESS_TOKEN=.*/WECOM_FITNESS_TOKEN=<值>/' \
  -e 's/^WECOM_FITNESS_AESKEY=.*/WECOM_FITNESS_AESKEY=<值>/' /opt/karvis/.env

sudo chmod 600 /opt/karvis/.env
grep -c '^WECOM_.*=.\+' /opt/karvis/.env   # 应为 1 + 4×应用数
```

**改 `.env` 后必须重建容器**（`restart` 不会重读 env_file）：
```bash
cd /opt/karvis && docker compose up -d --force-recreate
```

**敏感值优先用终端交互输入**，不要在聊天窗口来回粘贴（见坑 #4）：
```bash
read -s -p "paste: " K; echo; echo "LEN=${#K}"
sudo sed -i "s|^DEEPSEEK_API_KEY=.*|DEEPSEEK_API_KEY=$K|" /opt/karvis/.env
```

---

## 7. 第五步：回调 URL 与可信 IP

> 上游参考：[回调URL配置指南](https://github.com/sameencai/KarvisForYou/blob/main/docs/%E5%9B%9E%E8%B0%83URL%E9%85%8D%E7%BD%AE%E6%8C%87%E5%8D%97.md)

每个应用：接收消息 → 设置 API 接收

| 应用 | URL |
|---|---|
| 阿龙管家 | `http://49.235.107.213:9000/wecom/life` |
| 埼玉教练 | `http://49.235.107.213:9000/wecom/fitness` |
| 阿尼亚督导 | `http://49.235.107.213:9000/wecom/mood` |

- URL 末尾路径**不能少**（填 IP 或填错路径 = 保存失败/收不到消息）
- **企业可信 IP（必做）**：每个应用都填服务器公网 IP，多个用 `;` 分隔
- 漏配的表现：能收到消息，但助手不回复（日志 `60020 not allow to access from your ip`）

---

## 8. 第六步：验证清单

```bash
# 1) 公网可达
curl -s http://<公网IP>:9000/health          # 期望 ok；返回体含各助手 "ready": true

# 2) 容器内直测大模型（绕开企微，只打印状态码）
docker exec karvis python -c "import os,httpx;k=os.environ['DEEPSEEK_API_KEY'];r=httpx.post('https://api.deepseek.com/chat/completions',headers={'Authorization':'Bearer '+k,'Content-Type':'application/json'},json={'model':'deepseek-chat','messages':[{'role':'user','content':'hi'}],'max_tokens':5},timeout=30);print('LEN',len(k),'CODE',r.status_code)"
# 期望：LEN 35 CODE 200

# 3) 看日志
docker logs karvis --tail 50

# 4) 企微里给每个助手各发一句话
```

- [ ] 防火墙端口已放行（外部 curl /health 通）
- [ ] 三个助手 `ready: true`
- [ ] 大模型 `CODE 200`
- [ ] 每个应用可信 IP 已填
- [ ] 三个应用都能收发

---

## 9. 复用改动清单（换项目时只改这些）

| 改动项 | 位置 |
|---|---|
| 助手数量/名称 | `gateway/config.py` 的 `_AGENT_META` + `.env` 对应变量组 |
| 助手人格/行为 | `assistants/{life,fitness,mood}/AGENTS.md`（**改 Markdown 即可，不用改代码**） |
| 端口 | `.env`、`Dockerfile`、`docker-compose.yml`、`install.sh`、`config.py` 全量替换 |
| 定时推送 | `gateway/scheduler.py` + `.env` 的 `ENABLE_SCHEDULER=1` 与 `KARVIS_OWNER_USERID` |
| 仓库/服务器 | `deploy/setup_from_git.sh`、`update.sh` 的仓库地址与安装目录 |

---

## 10. 排错表（本次实战踩过的坑，按命中率排序）

| # | 症状 | 真因 | 解法 |
|---|---|---|---|
| 1 | 助手回「DeepSeek API Key 无效（401）」 | Key 残缺：仅 `sk-`+20 位（标准 35 位） | 新建 Key；**先 `echo ${#K}` 断言 35**；终端 `read -s` 交互写入 |
| 2 | 改了 `.env` 不生效 | `docker restart` 不重读 env_file | `docker compose up -d --force-recreate` |
| 3 | 能收消息不回复 | 企业可信 IP 未配 | 每个应用填服务器公网 IP（日志 `60020` 即此因；60020 反而证明 Secret 有效） |
| 4 | 聊天里粘的 Key 总是不对 | 长串密钥在 IM/终端往返中被截断或被平台改写 | 服务器终端 `read -s` 交互输入 + 长度断言；或从本地文件用脚本正则取值写入 |
| 5 | 回调保存失败「URL 验证失败」 | 服务端未配置该应用 Token/AESKey，或容器未重建 | 按第 6 节顺序：写完 .env → 重建 → 再保存 |
| 6 | 网页终端多行粘贴卡在 PS2 | 反斜杠续行带 CR | **一律单行命令**（分号/`&&` 连接） |
| 7 | heredoc 写文件「假成功」 | 粘贴丢失，`No such file` | 改用单行 `printf '%s\n' ... > file`，写完 `grep -n \| cat -A` 验真 |
| 8 | 脚本大小与仓库不符（缓存旧版） | 镜像站缓存 | 重下 + `wc -c` 比对 + `bash -n` |
| 9 | git clone 所有镜像全失败 | 镜像只转发 GET，不转发 upload-pack POST | 走 tarball 兜底（脚本已内置，日志里的失败属预期） |
| 10 | 脚本正常运行到一半静默退出 | `set -euo pipefail` 下 `[ cond ] && cmd` 作独立语句，条件假时退出 | 一律改 `if` |
| 11 | 端口释放不掉 | 直接 kill 了 docker-proxy | stop + rm 容器 |
| 12 | 服务器上 `.env` 被清空 | rsync `--delete` 未排除 | 加 `--exclude '.env'` |
| 13 | 服务器脚本 `bad interpreter` | CRLF 换行 | 本地 `core.autocrlf=input`，脚本保 LF |
| 14 | 应用在自己企微里找不到 | 可见范围没加自己 / 微信未关注企业 | 加可见范围 + 微信插件扫码 |

---

## 11. 日常运维

```bash
# 更新代码（不动 .env 与 data）
sudo bash /opt/karvis/deploy/update.sh

# 重启/重建
cd /opt/karvis && docker compose up -d --force-recreate

# 日志
docker logs karvis --tail 100 -f

# 数据（SQLite + 每日 md）
ls -l /opt/karvis/data
```

### 11.1 版本管理与回滚（长期迭代必配）

一次发布 = **Git tag + CHANGELOG 记录 + 服务器 VERSION + 数据快照** 四件套。

```bash
# 本地发布（版本号 + CHANGELOG + tag + push，一条命令）
bash deploy/release.sh patch "说明这次改了什么"

# 服务器：拉代码 + 记录版本（发布脚本会打印这两行）
cd /opt/karvis && sudo bash deploy/update.sh
cd /opt/karvis && sudo bash deploy/record_release.sh v0.1.1

# 查线上实际版本（version/commit 是发布是否生效的依据）
curl -s http://127.0.0.1:9000/health

# 回滚到某个历史版本（数据不动，回滚前自动快照）
cd /opt/karvis && sudo bash deploy/rollback.sh v0.1.0
cd /opt/karvis && sudo bash deploy/rollback.sh --list     # 看历史发布记录

# 数据备份（本地快照 7 天；配了 COS 凭证则同时上云）
cd /opt/karvis && sudo bash deploy/backup.sh
cd /opt/karvis && sudo bash deploy/backup.sh --install-cron   # 每日 03:00
```

完整规范见 `docs/项目管理规范.md`；需求看板见 `BACKLOG.md`。

**安全红线**：`.env` 不入库、不进聊天截图；权限 600；废弃的 API Key 及时在平台删除；备份包**不含** `.env`（密钥不出服务器）。

---

## 附：本次部署时间线（2026-09-08）

| 时间 | 事件 |
|---|---|
| 19:30 | 需求对齐：微信端 + 多助手 + 云服务器；锁定企微自建应用 ×3 |
| 20:45 | P0 代码交付，本地加解密回环 / mock 回调 / 自检全过 |
| 21:10 | 改走 Git 部署；`.env` 永不入库 |
| 21:40 | 网页终端实战：单行命令、单行 printf 写 `.env`、清理旧容器释放 9000 |
| 21:55 | git clone 四源全失败 → 加 tarball 兜底 |
| 22:13 | 部署成功，`/health` 公网 200 |
| 22:25 | 定位 401 真因：Key 只有 20 位（残缺） |
| 22:35 | 换新 Key（35 位）→ 阿龙管家跑通 |
| 22:58 | fitness / mood 凭证补齐，三助手全绿 |

**上游文档索引**
- [企微注册与应用创建](https://github.com/sameencai/KarvisForYou/blob/main/docs/%E4%BC%81%E5%BE%AE%E6%B3%A8%E5%86%8C%E4%B8%8E%E5%BA%94%E7%94%A8%E5%88%9B%E5%BB%BA.md)
- [回调URL配置指南](https://github.com/sameencai/KarvisForYou/blob/main/docs/%E5%9B%9E%E8%B0%83URL%E9%85%8D%E7%BD%AE%E6%8C%87%E5%8D%97.md)
- [企微应用配置指南](https://github.com/sameencai/KarvisForYou/blob/main/docs/%E4%BC%81%E5%BE%AE%E5%BA%94%E7%94%A8%E9%85%8D%E7%BD%AE%E6%8C%87%E5%8D%97.md)
