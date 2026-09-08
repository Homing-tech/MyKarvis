# 停掉旧版 Karvis（Flask）操作指南

> 目的：释放 TCP 9000 端口给新网关。
> 原则：**先确认运行方式 → 先备份 → 再停 → 最后验证端口释放**。全程可逆，随时能拉起来。
> 适用机器：腾讯云轻量 `49.235.107.213`（上海，4C/4G/40G，已装 Docker CE）

---

## 0. 当前已知状态（2026-09-08 实测）

| 项 | 值 |
|---|---|
| 服务响应 | `GET http://49.235.107.213:9000/` → 200 `Karvis is alive` |
| 技术栈 | Werkzeug 3.1.6 / Python 3.11.15（Flask） |
| 已运行 | 约 174 天 |
| 已有路由 | `/wework`（200）、`/health`（200） |
| `/health` 返回 | `deepseek_key: true`、`wework_token: false`、`disk_free_gb: 19.4`、`scheduler: true`、`active_users: 1` |
| 其他端口 | 80 → 502（疑似有 nginx 但后端挂了）；9001/9002/8000/8080/5000/9100 均无响应 |

**读出来的两件事**：
1. `deepseek_key: true` → 旧项目里已经配着一个可用的 DeepSeek Key，**可以复用**，不用重新申请。
2. `wework_token: false` → 旧项目的企微回调凭证没配（或没生效），说明它的企微链路大概率没真正跑通，停掉损失不大。

---

## 1. 第一步：搞清楚它到底是怎么跑起来的

在服务器上依次执行，**三个都要跑**，不同方式表现不同：

```bash
# ① 是不是 Docker 容器
docker ps -a --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}" | grep -i -E "karvis|9000"

# ② 是不是 systemd 服务
systemctl list-units --type=service --all | grep -i karvis
systemctl status karvis 2>/dev/null | head -20

# ③ 是不是裸进程 / nohup / screen
ps aux | grep -i -E "python|gunicorn|flask" | grep -v grep
ss -lntp | grep 9000
```

### 怎么判断

| 你看到的现象 | 运行方式 | 跳到 |
|---|---|---|
| `docker ps` 里有名字含 karvis 的容器 | Docker（compose 或 run） | §2-A |
| `systemctl status karvis` 有输出且 active | systemd | §2-B |
| `ps aux` 里有 python 进程，`docker ps` 和 systemctl 都没有 | 裸进程 / nohup / screen | §2-C |
| 三个都没抓到，但端口还是占着 | 可能端口被复用或进程名不含关键字 | 直接看 `ss -lntp \| grep 9000` 拿 PID，`kill <PID>` |

> 补充：`ss -lntp | grep 9000` 会直接告诉你占用端口的进程 PID 和程序名，这是最准的一招。

---

## 2. 第二步：按运行方式停掉

### A. Docker / Docker Compose

```bash
# 找到 compose 目录（通常在 /opt/karvis 或 ~/karvis 或 /root/my-karvis）
find / -maxdepth 4 -name "docker-compose.y*ml" -not -path "*/node_modules/*" 2>/dev/null

# 进入该目录后
cd <那个目录>
docker compose down          # 停容器 + 删容器（不删数据卷，安全）

# 如果是 docker run 起的（没有 compose 文件）
docker stop <容器名> && docker rm <容器名>
```

**禁止开机自启**（不然后面重启机器它又冒出来抢端口）：

```bash
# compose 方式：确认没有 restart: always，或确认容器不再被拉起
docker ps -a --format "{{.Names}} {{.Status}}"
# 若容器已 down 且不会被拉起即可；compose 不会自动开机启动，除非配了 systemd 去 up
```

### B. systemd 服务

```bash
sudo systemctl stop karvis          # 停
sudo systemctl disable karvis       # 关开机自启
sudo systemctl status karvis        # 确认为 inactive (dead)

# 如果想彻底删掉服务（可选，保留也能随时 start）
sudo rm -f /etc/systemd/system/karvis.service && sudo systemctl daemon-reload
```

### C. 裸进程 / nohup / screen

```bash
PID=$(ss -lntp | grep ':9000' | grep -oP 'pid=\K[0-9]+' | head -1)
echo "占用 9000 的 PID = $PID"
ps -p $PID -o pid,cmd --no-headers      # 确认一眼，别杀错

kill $PID                                # 优雅退出
sleep 2
kill -9 $PID 2>/dev/null                 # 还在就强杀
```

如果是 `screen` 里跑的：

```bash
screen -ls                    # 列出会话
screen -r <会话名>            # 进去后 Ctrl+C 停掉，再 Ctrl+A D 退出
```

---

## 3. 第三步：验证端口已释放（必做）

```bash
ss -lntp | grep 9000 || echo "✅ 9000 已释放"
curl -s -m 3 -o /dev/null -w "%{http_code}\n" http://127.0.0.1:9000/   # 期望 000
```

- 输出 `✅ 9000 已释放` + `000` → 成功，可以部署新网关。
- 还有输出 → 回到 §1 重查，别硬上，新旧抢端口会启动失败。

---

## 4. 备份（建议做，成本几乎为零）

停服务**前**执行，把旧数据和配置留一份：

```bash
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p /root/karvis_backup
tar -czf /root/karvis_backup/karvis_old_$TS.tar.gz \
    --exclude='*/__pycache__/*' \
    --exclude='*/.git/*' \
    /opt/karvis 2>/dev/null || tar -czf /root/karvis_backup/karvis_old_$TS.tar.gz /root/my-karvis 2>/dev/null

ls -lh /root/karvis_backup/
```

**重点保住这两个东西**（新项目要用）：

```bash
# ① DeepSeek Key —— 直接复用，省得重新申请
grep -r "DEEPSEEK" /opt/karvis/.env 2>/dev/null || grep -r "DEEPSEEK" /root/my-karvis/.env 2>/dev/null

# ② 旧数据（如果里面有历史记录，之后可以导进新库）
ls -la /opt/karvis/data 2>/dev/null || ls -la /root/my-karvis/data 2>/dev/null
```

---

## 5. 回滚（万一后悔）

| 原运行方式 | 回滚命令 |
|---|---|
| Docker compose | `cd <目录> && docker compose up -d` |
| Docker run | 用原来的 `docker run` 命令（备份里有 command 记录：`docker inspect <容器名> --format '{{.Config.Cmd}}'`） |
| systemd | `sudo systemctl enable --now karvis` |
| 裸进程 | 重新执行原来的启动命令（备份包里有） |

数据没动过，所以**回滚是零损失的**——`docker compose down` 和 `systemctl stop` 都不删数据卷和文件。

---

## 6. 释放端口后，立刻可以做的事

```bash
# 确认磁盘还够（之前只剩 19.4G，Docker 构建需要空间）
df -h /
docker system df          # 看看有没有垃圾镜像可以清
docker image prune -a     # 可选：清理不用的镜像，能腾出不少空间
```

---

## 附：一句话速查

```bash
ss -lntp | grep 9000                      # 谁占了 9000
docker ps -a | grep -i karvis             # 是不是容器
systemctl status karvis                   # 是不是 systemd
grep -r DEEPSEEK /opt/karvis/.env         # 挖出可复用的 Key
```
