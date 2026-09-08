# 用 Git 部署与后续迭代

> 结论：**可行，而且比传压缩包更好** —— 后面每次改代码，服务器一条 `update.sh` 就完成，不用再上传文件。
> 唯一的硬约束：**`.env` 永远不进 git**（凭证泄露风险）。已在 `.gitignore` 里锁死，服务器本地生成。

---

## 一、为什么可行，以及它解决什么问题

| | 传压缩包（当前） | Git 拉取（推荐） |
|---|---|---|
| 首次上手 | 简单，1 分钟 | 需要建仓库 + 配一次 SSH/HTTPS |
| 改一个提示词 | 重新打包 → 上传 → 解压 → 重启（4 步） | `git push` + 服务器 `update.sh`（2 步） |
| 版本回溯 | 靠备份目录 | `git log` / `git revert` 精确到每次改动 |
| 换机器 | 重新打包 | `git clone` 一行 |

**建议：首次部署仍用 `karvis-deploy.tar.gz` 起量（因为要带 `.env`），之后一律走 git 迭代。** 两条路都留着，脚本已分别备好（详见第四节）。

---

## 二、本次选定：GitHub

**仓库**：`https://github.com/Homing-tech/MyKarvis.git`　**默认分支**：`main`

| 平台 | 服务器拉取速度 | 说明 |
|---|---|---|
| **Gitee（码云）** | 快、稳定 | 国内直连，私有仓库免费 |
| **腾讯云 CODING DevOps** | 最快 | 和轻量服务器同机房生态，自带 CI |
| **GitHub**（本次选择） | 时快时慢 | 国内轻量机拉取偶尔超时，已加镜像回退 |

### GitHub 的两个前提（务必照做）

**① 仓库必须设为 Private**

代码目录虽已把 `.env` 排除，但 `.env.example` 之外的东西仍可能带个人信息（`assistants/_shared/profile.md` 里有你的作息与沟通偏好）。设私有路径：
`Settings → Danger Zone → Change repository visibility → Private`

**② 服务器拉取走镜像回退（已内置）**

国内轻量直连 `github.com` 失败率不低，所以 `setup_from_git.sh` / `update.sh` 都做了候选源逐个试：

```
1. https://github.com/Homing-tech/MyKarvis.git            ← 原地址
2. https://ghfast.top/https://github.com/...git           ← 镜像 1
3. https://gh-proxy.com/https://github.com/...git         ← 镜像 2
4. https://gh.llkk.cc/https://github.com/...git           ← 镜像 3
```

每个候选 120 秒超时，谁先成功就用谁，并 `git remote set-url` 记住它——**以后 `update.sh` 直接用能通的那个，不再每次重试**。

> 如果四个都失败（极端情况），兜底方案仍是传压缩包：见 `deploy/部署操作手册_网页终端版.md`。

---

## 三、已经在本地做过的事

- `karvis/` 已 `git init` 并提交首版（37 个文件）
- `core.autocrlf=input` —— 保证脚本推到 Linux 是 LF 换行（CRLF 会让 shell 脚本报 `bad interpreter`）
- `.gitignore` 已排除：`.env`、`deploy/create_env.sh`（含真实凭证）、`data/`、`logs/`、`__pycache__/`
- 已验证：**没有任何凭证文件被跟踪**

---

## 四、完整流程

### 方案 A：Git 全流程（推荐长期用）

**本地（已完成 remote 配置，只差推送）**

```bash
cd "C:\Users\homingliang\WorkBuddy\2026-09-08-19-28-56\karvis"

# 首次推送（会弹出浏览器让你登录 GitHub，登录一次后本机永久记住）
git push -u origin main
```

> 本机已装 Git Credential Manager 2.9.0，push 时会自动弹浏览器授权，不用手工填 token。
> 若弹窗没出来，改用 Personal Access Token：
> GitHub → `Settings → Developer settings → Personal access tokens → Tokens (classic)`
> → 勾 `repo` → 生成后，`git push` 时用户名填 GitHub 账号、**密码填这个 token**。

**服务器（一次）** —— 网页终端粘贴：

```bash
# 1) 生成 .env（两种方式任选）
#   A1：把 deploy/create_env.sh 上传到服务器后执行
sudo bash /tmp/karvis_new/deploy/create_env.sh
#   A2：或手工 sudo vi /opt/karvis/.env，按 .env.example 填

# 2) 首次拉取 + 部署（自动试镜像、备份、停旧服务、构建、自检）
sudo bash /tmp/karvis_new/deploy/setup_from_git.sh \
  https://github.com/Homing-tech/MyKarvis.git main
```

> 仓库是私有的话，用 SSH 更省心（免每次输密码）：
> 服务器执行 `ssh-keygen -t ed25519 -N "" -f ~/.ssh/karvis_deploy && cat ~/.ssh/karvis_deploy.pub`
> 把输出粘到 GitHub 仓库 `Settings → Deploy keys → Add deploy key`（勾 Allow write 与否都行，只读够用），
> 然后用 `git@github.com:Homing-tech/MyKarvis.git` 作为地址即可——脚本会自动生成它的镜像候选。
>
> 若坚持 HTTPS + 私有仓库：密码处填 Personal Access Token（同上）。

**以后每次改代码（本地）**

```bash
cd karvis && git add -A && git commit -m "改了什么" && git push
```

**服务器一条命令更新**

```bash
sudo bash /opt/karvis/deploy/update.sh
```

它做四件事：拉最新代码 → 重建容器 → `/health` 自检 → 打印最近 15 行日志。**不动 `.env`、不动 `data/`（你的健身/情绪/事项记录都在那）。**

---

### 方案 B：先用压缩包起量，之后切 git（零风险起步）

1. 按《部署操作手册_网页终端版.md》用 `karvis-deploy.tar.gz` 跑通（`.env` 随包带上，省事）
2. 之后想走 git 了，在服务器执行一次：

```bash
cd /opt/karvis
git init -b main
git remote add origin https://github.com/Homing-tech/MyKarvis.git
git fetch origin && git reset --hard origin/main   # 让服务器目录变成 git 工作区
```

> 注意：`git reset --hard` 不会删 `.env` 和 `data/`（都在 `.gitignore` 里），安全。

3. 之后就用 `sudo bash /opt/karvis/deploy/update.sh` 迭代

---

## 五、安全红线（务必遵守）

1. **`.env` 和 `deploy/create_env.sh` 永不提交** —— 已在 `.gitignore`，但你自己 `git add -f` 会绕过，别这么干。
2. 仓库设**私有**（企微 CorpID、Secret、DeepSeek Key 全是敏感信息）。
3. 万一误提交过凭证：立刻去企微后台**重置 Secret / AESKey**，然后 `git filter-repo` 清历史 —— 改历史不如直接换凭证快。
4. 服务器上 `.env` 权限 600，只有 root 能读（`create_env.sh` 已自动设置）。

---

## 六、常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 服务器 `git clone` 超时 | GitHub 国内偶发 | 脚本已内置 3 个镜像自动回退；都失败就临时用压缩包部署 |
| 脚本报 `bad interpreter` | 文件变 CRLF 了 | 已用 `autocrlf=input` 防住；真出现就 `sed -i 's/\r$//' xxx.sh` |
| `update.sh` 后助手行为没变 | 浏览器/企微缓存，或改的是 `AGENTS.md` 但没 push | 确认 `git log` 最新，且容器已重建 |
| push 被拒 | 本地没配 remote 或分支名不一致 | `git remote -v` 检查；分支统一 `main` |
