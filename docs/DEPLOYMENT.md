# 部署文档 —— 涨停回调低吸战法选股平台

> 版本：v1.0.0
> 适用架构：单机容器化部署（Docker + Docker Compose）
> 目标环境：Linux 云服务器（Ubuntu 22.04 / Debian 12 / CentOS Stream 9 / AlmaLinux 9）、Windows Server / Windows 11（Docker Desktop）、macOS

---

## 目录

1. [部署架构总览](#1-部署架构总览)
2. [环境要求](#2-环境要求)
3. [一键部署（推荐）](#3-一键部署推荐)
4. [云服务器完整部署流程](#4-云服务器完整部署流程)
5. [手动 Docker 部署](#5-手动-docker-部署)
6. [HTTPS 与域名配置](#6-https-与域名配置)
7. [环境变量参考](#7-环境变量参考)
8. [数据持久化与备份](#8-数据持久化与备份)
9. [日常运维命令](#9-日常运维命令)
10. [更新与回滚](#10-更新与回滚)
11. [监控与日志](#11-监控与日志)
12. [离线/内网部署](#12-离线内网部署)
13. [本地开发环境](#13-本地开发环境)
14. [故障排查](#14-故障排查)
15. [安全加固建议](#15-安全加固建议)

---

## 1. 部署架构总览

本项目采用**单容器同源部署**：前端静态资源在构建阶段产出，由 FastAPI 后端统一提供 Web 服务，**无需额外的 Nginx**，避免跨域与多进程运维复杂度。

```
                    ┌──────────────────────────────────────────┐
   浏览器 / 移动端   │              云服务器 / 主机               │
        │           │                                          │
        │  HTTP(S)  │   ┌──────────────────────────────────┐   │
        └──────────►│   │   Docker 容器  limit-up-app       │   │
                    │   │                                  │   │
                    │   │  Uvicorn (FastAPI)  :8000        │   │
                    │   │    ├── /api/v1/*   REST 接口      │   │
                    │   │    └── /           SPA 静态资源    │   │
                    │   │                                  │   │
                    │   │  数据卷 app-data ──► /app/backend/data │
                    │   │    ├── cache/   行情缓存           │   │
                    │   │    ├── pool.json  低吸池           │   │
                    │   │    └── rules.json 策略参数         │   │
                    │   └──────────────────────────────────┘   │
                    │                                          │
                    │   (可选) Caddy 容器：自动 HTTPS 反代       │
                    └──────────────────────────────────────────┘
                                       │
                                       ▼ 可选出网
                          东方财富公开行情接口
                          （不可用时自动降级为合成演示数据）
```

**镜像构建采用多阶段构建**：

| 阶段 | 基础镜像 | 作用 |
|---|---|---|
| `frontend-builder` | `node:20-alpine` | `npm ci` + `npm run build` 产出 `dist/` |
| `runtime` | `python:3.12-slim` | 安装 Python 依赖，内置前端产物，暴露 8000 端口，非 root 用户运行 |

最终镜像**不包含** Node.js、前端源码与构建缓存，体积与攻击面都更小。

---

## 2. 环境要求

### 2.1 最低配置

| 项目 | 最低 | 推荐 |
|---|---|---|
| CPU | 1 核 | 2 核 |
| 内存 | 1 GB | 2 GB |
| 磁盘 | 5 GB 可用 | 20 GB 可用 |
| 操作系统 | 64 位 Linux / Windows 10+ / macOS 12+ | Ubuntu 22.04 LTS |
| Docker | Engine 20.10+ | Engine 24+ 或 Docker Desktop 4.x |
| Docker Compose | v2（`docker compose`）或 v1.29+（`docker-compose`） | v2 |

> 前端构建阶段（Node）峰值内存约 1 GB。若服务器内存仅 1 GB，请先临时挂载 swap，或采用[离线/内网部署](#12-离线内网部署)在本地构建镜像后导入。

### 2.2 一键安装 Docker（Linux）

官方脚本方式（最省事）：

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
# 将当前用户加入 docker 组，之后无需 sudo（需重新登录生效）
sudo usermod -aG docker "$USER"
```

国内服务器如遇网络缓慢，可使用阿里云镜像源：

```bash
curl -fsSL https://get.docker.com | sh -s docker --mirror Aliyun
```

### 2.3 放行端口

应用默认使用 **8000/TCP**。需要放行两处：

1. **云服务商安全组**（控制台操作）：入方向放行 `8000/tcp`（或你自定义的端口）。
2. **主机防火墙**：

```bash
# ufw（Ubuntu/Debian）
sudo ufw allow 8000/tcp

# firewalld（CentOS/RHEL/Alma）
sudo firewall-cmd --permanent --add-port=8000/tcp && sudo firewall-cmd --reload

# iptables
sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
```

`deploy.sh` 会在检测到 ufw / firewalld 处于启用状态时**自动尝试放行**，但**云安全组必须手动配置**（脚本无法代劳）。

---

## 3. 一键部署（推荐）

> 脚本具备幂等性：重复执行不会破坏已有数据；首次执行会自动从 `.env.example` 生成 `.env`。

### 3.1 Linux / macOS

```bash
# 进入项目根目录
cd Limit-Up-Pullback-Buy-Setup

# 赋予执行权限（仅首次需要）
chmod +x deploy.sh

# 一键部署
./deploy.sh
```

脚本会自动完成：检查 Docker 环境 → 生成 `.env` → 校验端口与数据源模式 → 尝试放行防火墙 → 构建镜像 → 启动容器 → 轮询健康检查 → 打印访问地址。

### 3.2 Windows（PowerShell）

```powershell
# 进入项目根目录后执行（先确保 Docker Desktop 已启动）
pwsh -File .\deploy.ps1
```

Windows PowerShell 5.1（系统自带，命令为 `powershell`）同样可用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy.ps1 -Status
```

若系统禁止执行脚本，可临时放行当前进程：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\deploy.ps1
```

Windows 下脚本会尝试通过 `New-NetFirewallRule` 自动创建入站放行规则（需管理员权限，失败仅告警不中断）。

> **注意**：`deploy.ps1` 以 **UTF-8 with BOM** 编码保存。Windows PowerShell 5.1 在读取无 BOM 的 UTF-8 文件时会按 ANSI 解码，导致中文文案乱码甚至语法解析错误。**请勿将本文件另存为「UTF-8 无 BOM」或其他编码。**

### 3.3 常用参数

| Linux/macOS | Windows | 说明 |
|---|---|---|
| `./deploy.sh --port 8080` | `-Port 8080` | 指定对外端口 |
| `./deploy.sh --mode synthetic` | `-Mode synthetic` | 强制内置演示数据（完全离线可用） |
| `./deploy.sh --mode eastmoney` | `-Mode eastmoney` | 强制使用东方财富真实行情（失败即报错） |
| `./deploy.sh --https` | `-Https` | 启用 Caddy 自动 HTTPS（交互输入域名） |
| `./deploy.sh --update` | `-Update` | 拉取最新代码并重建部署 |
| `./deploy.sh --status` | `-Status` | 查看容器状态与健康检查 |
| `./deploy.sh --logs` | `-Logs` | 实时跟踪日志 |
| `./deploy.sh --stop` | `-Stop` | 停止服务（保留数据卷） |
| `./deploy.sh --down` | `-Down` | 移除容器与网络（保留数据卷） |
| `./deploy.sh --destroy` | `-Destroy` | 彻底清理（含数据卷，需输入 `YES` 二次确认） |
| `./deploy.sh --help` | — | 查看帮助 |

### 3.4 部署成功后的输出示例

```
[成功] Docker 环境就绪：Docker version 27.5.1, build 9f9e405
[成功] 已根据 .env.example 生成 .env
[信息] 等待服务就绪：http://127.0.0.1:8000/api/v1/health
[成功] 服务健康检查通过（第 3 次探测）
{"status":"ok","version":"1.0.0","uptimeSeconds":6.2,"dataSource":"eastmoney", ...}
==============================================
[成功] 部署完成！
  本机访问    : http://127.0.0.1:8000
  局域网/公网 : http://203.0.113.10:8000
  数据源模式  : auto
  常用命令    : ./deploy.sh --logs | --status | --update | --stop
  完整部署文档: docs/DEPLOYMENT.md
==============================================
```

---

## 4. 云服务器完整部署流程

以 **Ubuntu 22.04 LTS** 为例，从零到可访问约 5 分钟。

```bash
# ---------- ① 连接服务器 ----------
ssh root@<你的服务器公网IP>

# ---------- ② 安装 Docker 与 Compose ----------
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# ---------- ③ 获取项目代码 ----------
# 方式 A：Git 克隆
git clone <你的仓库地址> Limit-Up-Pullback-Buy-Setup
# 方式 B：本地上传（在本地机器执行）
#   scp -r ./Limit-Up-Pullback-Buy-Setup root@<IP>:/opt/

cd Limit-Up-Pullback-Buy-Setup

# ---------- ④ 配置环境变量（可选，脚本会自动生成默认 .env） ----------
cp .env.example .env
vi .env        # 按需修改 HOST_PORT / DATA_SOURCE_MODE / UNIVERSE_SIZE

# ---------- ⑤ 一键部署 ----------
chmod +x deploy.sh
./deploy.sh

# ---------- ⑥ 验证 ----------
curl -s http://127.0.0.1:8000/api/v1/health
```

最后在**云服务商控制台 → 安全组**放行 `8000/tcp`，浏览器访问 `http://<公网IP>:8000` 即可。

### 4.1 国内网络加速（可选）

拉取基础镜像缓慢时，为 Docker 配置镜像加速：

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": ["https://docker.m.daocloud.io", "https://dockerproxy.com"],
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
```

### 4.2 以普通用户部署

```bash
sudo useradd -m -s /bin/bash deploy
sudo usermod -aG docker deploy
sudo -iu deploy
# 之后按第 ③~⑥ 步操作，无需 sudo
```

---

## 5. 手动 Docker 部署

不使用脚本时，直接使用 Docker Compose（在项目根目录执行）：

```bash
# 1) 准备环境变量
cp .env.example .env

# 2) 构建并启动
docker compose up -d --build

# 3) 查看状态与日志
docker compose ps
docker compose logs -f --tail=200

# 4) 健康检查
curl -s http://127.0.0.1:8000/api/v1/health
```

### 5.1 纯 Docker 命令（不使用 Compose）

```bash
# 构建镜像
docker build -t limit-up-pullback:latest .

# 运行容器
docker run -d \
  --name limit-up-app \
  --restart unless-stopped \
  -p 8000:8000 \
  -e DATA_SOURCE_MODE=auto \
  -e UNIVERSE_SIZE=300 \
  -e TZ=Asia/Shanghai \
  -v limit-up-data:/app/backend/data \
  limit-up-pullback:latest

# 查看日志 / 进入容器
docker logs -f --tail=200 limit-up-app
docker exec -it limit-up-app sh
```

### 5.2 端口映射说明

`.env` 中 `HOST_PORT` 与 `APP_PORT` 的关系为 `宿主机:HOST_PORT` → `容器:APP_PORT`。

```bash
# 让容器监听 8000，但只暴露在宿主机 127.0.0.1:8080（配合前置 Nginx 时常用）
# 修改 docker-compose.yml 的 ports 为：
#   - "127.0.0.1:${HOST_PORT}:${APP_PORT}"
docker compose up -d
```

---

## 6. HTTPS 与域名配置

### 6.1 自动 HTTPS（推荐，内置 Caddy）

前置条件：一个已解析到本机公网 IP 的域名（A 记录），且安全组放行 `80`、`443`。

```bash
./deploy.sh --https
# 脚本会提示输入域名与 ACME 邮箱，写入 .env 后以 https profile 启动 Caddy
```

或手动：

```bash
# 编辑 .env
#   DOMAIN=stock.example.com
#   ACME_EMAIL=you@example.com
docker compose --profile https up -d --build
```

Caddy 会自动申请并续期 Let's Encrypt 证书，HTTP 自动跳转 HTTPS，并附加 HSTS、`X-Content-Type-Options`、`X-Frame-Options` 等安全响应头。证书持久化在 `caddy-data` 数据卷中。

> **注意**：`.env` 中 `DOMAIN` 留空时，Caddy 只提供 80 端口的纯 HTTP 反向代理，不会申请证书。

### 6.2 使用已有 Nginx

若服务器已运行 Nginx，可只暴露内部端口，由宿主机 Nginx 反代：

```nginx
server {
    listen 443 ssl http2;
    server_name stock.example.com;

    ssl_certificate     /etc/letsencrypt/live/stock.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/stock.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;      # 全市场扫描可能较慢
        proxy_buffering    off;       # 便于流式响应
    }
}

server {
    listen 80;
    server_name stock.example.com;
    return 301 https://$host$request_uri;
}
```

后端已使用 `--proxy-headers --forwarded-allow-ips='*'` 启动，可正确识别 `X-Forwarded-*`，因此日志与限流中记录的是真实客户端 IP。

---

## 7. 环境变量参考

所有变量集中定义在项目根目录 `.env`（从 `.env.example` 复制）。**未在 `.env` 中声明的变量会使用镜像内置默认值。**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HOST_PORT` | `8000` | **宿主机**对外端口，云安全组需放行 |
| `APP_PORT` | `8000` | **容器内**应用监听端口，通常无需改动 |
| `IMAGE_NAME` | `limit-up-pullback` | 镜像名 |
| `IMAGE_TAG` | `latest` | 镜像标签，回滚时指定旧标签 |
| `CONTAINER_NAME` | `limit-up-app` | 容器名 |
| `DATA_SOURCE_MODE` | `auto` | `auto`：优先东方财富接口，失败自动降级为合成演示数据；`eastmoney`：强制真实数据，失败报错；`synthetic`：强制合成数据，完全离线 |
| `CACHE_TTL_SECONDS` | `300` | 行情缓存有效期（秒），降低外部接口压力 |
| `UNIVERSE_SIZE` | `300` | 合成演示数据的股票数量 |
| `HTTP_TIMEOUT` | `10` | 外部行情接口超时（秒） |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `TZ` | `Asia/Shanghai` | 容器时区（影响交易日期判定与日志时间） |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 跨域白名单，**仅本地 Vite 开发需要**；生产同源部署可留空 |
| `DOMAIN` | 空 | HTTPS 域名，留空则不启用证书 |
| `ACME_EMAIL` | 空 | Let's Encrypt 通知邮箱 |
| `HTTP_PORT` / `HTTPS_PORT` | `80` / `443` | Caddy 容器映射的宿主端口 |

修改 `.env` 后需重建容器使环境变量生效：

```bash
docker compose up -d --force-recreate
```

---

## 8. 数据持久化与备份

### 8.1 数据卷内容

| 路径（容器内） | 内容 | 丢失影响 |
|---|---|---|
| `/app/backend/data/cache/` | 行情缓存（可重建） | 无，下次请求自动重新拉取 |
| `/app/backend/data/rules.json` | 自定义策略参数 | 回退到默认参数 |
| `/app/backend/data/pool.json` | 自选低吸池 | **用户数据，建议备份** |

对应 Docker 具名卷 `app-data`（Compose 项目名前缀由目录名决定，可 `docker volume ls` 确认）。

### 8.2 备份

```bash
# 查看数据卷
docker volume ls | grep app-data

# 备份到当前目录（相对路径）
docker run --rm \
  -v "$(basename "$PWD")_app-data:/data:ro" \
  -v "$PWD:/backup" \
  alpine tar czf "/backup/backup-$(date +%Y%m%d-%H%M%S).tar.gz" -C /data .

# 或直接拷贝容器内数据
docker cp limit-up-app:/app/backend/data ./data-backup-$(date +%Y%m%d)
```

### 8.3 恢复

```bash
docker compose stop app
docker run --rm \
  -v "$(basename "$PWD")_app-data:/data" \
  -v "$PWD:/backup:ro" \
  alpine sh -c "rm -rf /data/* && tar xzf /backup/<备份文件>.tar.gz -C /data"
docker compose start app
```

### 8.4 定时备份（crontab）

```bash
# 每天 03:30 备份，保留最近 14 天
30 3 * * * cd /opt/Limit-Up-Pullback-Buy-Setup && docker cp limit-up-app:/app/backend/data ./backups/$(date +\%F) && find ./backups -type d -mtime +14 -exec rm -rf {} +
```

---

## 9. 日常运维命令

```bash
# ---- 状态与健康 ----
./deploy.sh --status                       # 脚本封装
docker compose ps                          # 容器状态
curl -s http://127.0.0.1:8000/api/v1/health | jq .

# ---- 日志 ----
./deploy.sh --logs                         # 实时跟踪
docker compose logs --tail=500 app         # 最近 500 行
docker compose logs --since 30m app        # 最近 30 分钟

# ---- 生命周期 ----
docker compose restart app                 # 重启应用
docker compose stop                        # 停止（保留容器与数据）
docker compose start                       # 启动
docker compose down                        # 移除容器与网络（保留数据卷）
docker compose down -v                     # 移除容器并删除数据卷（危险）

# ---- 资源占用 ----
docker stats --no-stream limit-up-app
docker system df                           # 磁盘占用总览
docker image prune -f                      # 清理悬空镜像
docker builder prune -f                    # 清理构建缓存
```

---

## 10. 更新与回滚

### 10.1 更新到新版本

```bash
cd /opt/Limit-Up-Pullback-Buy-Setup
./deploy.sh --update        # 等价于 git pull + 重新构建 + 滚动重启
```

手动方式：

```bash
git pull
docker compose build --no-cache app
docker compose up -d --remove-orphans
```

### 10.2 回滚

**方式一：按镜像标签回滚**

```bash
# 更新前先给当前版本打标签
docker tag limit-up-pullback:latest limit-up-pullback:v1.0.0

# 出问题时回滚
sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=v1.0.0/' .env
docker compose up -d --force-recreate
```

**方式二：按 Git 提交回滚**

```bash
git log --oneline -10
git checkout <上一个稳定提交>
docker compose up -d --build
```

> 数据卷 `app-data` 不随镜像变化，回滚不会丢失低吸池与策略参数。若新版本改动了数据结构，请先按第 8 节备份。

---

## 11. 监控与日志

### 11.1 容器健康检查

镜像内置 `HEALTHCHECK`，每 30 秒探测 `GET /api/v1/health`（该接口**不套统一响应信封**，专供探针）。三者任一为 `unhealthy` 时应介入排查。

```bash
docker inspect --format='{{.State.Health.Status}}' limit-up-app
docker inspect --format='{{json .State.Health}}' limit-up-app | jq .
```

### 11.2 日志轮转

已在 `docker-compose.yml` 中配置：单文件上限 10 MB，最多保留 3 个文件，无需额外配置 logrotate。

### 11.3 接入外部监控（可选）

```bash
# 简易存活探测（crontab，异常时输出到日志）
*/5 * * * * curl -fsS --max-time 5 http://127.0.0.1:8000/api/v1/health >/dev/null || echo "$(date) health check failed" >> ./health-fail.log
```

Prometheus 用户可另起 `node-exporter` / `cadvisor` 采集容器指标；应用侧可作为后续迭代接入 `/metrics` 端点。

---

## 12. 离线/内网部署

目标服务器无外网时，在**有网的机器**上构建镜像后导出传输。

```bash
# ---------- 有网机器（架构需与目标一致，如同为 x86_64） ----------
docker build -t limit-up-pullback:latest .
docker save limit-up-pullback:latest -o limit-up-pullback.tar
gzip limit-up-pullback.tar             # 可选压缩

# 传输
scp limit-up-pullback.tar.gz user@<目标IP>:/opt/

# ---------- 目标服务器 ----------
gunzip -c limit-up-pullback.tar.gz | docker load
docker images | grep limit-up-pullback

# 只启动容器，不再构建（只需 docker-compose.yml 与 .env）
docker compose up -d --no-build
```

**离线环境下的数据源配置**（服务器无法访问东方财富接口时）：

```bash
# .env
DATA_SOURCE_MODE=synthetic
UNIVERSE_SIZE=300
```

此时全部功能可用，界面会显著标注「演示数据」。

> 跨架构（如 Apple Silicon 构建 → x86 服务器部署）需指定平台：
> `docker buildx build --platform linux/amd64 -t limit-up-pullback:latest --load .`

---

## 13. 本地开发环境

### 13.1 后端

```bash
cd backend
py -m venv .venv                       # Windows；Linux/macOS 用 python3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Linux/macOS：

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

接口文档（FastAPI 自动生成）：`http://127.0.0.1:8000/docs`

### 13.2 前端

```bash
cd frontend
npm install
npm run dev            # 默认 http://127.0.0.1:5173
```

`vite.config.ts` 已将 `/api` 代理到 `VITE_API_PROXY_TARGET`（默认 `http://127.0.0.1:8000`），因此前端代码中永远只写相对路径 `/api/v1/...`。

### 13.3 测试

```bash
cd backend
.venv\Scripts\python -m pytest -q          # Windows
# python -m pytest -q                      # Linux/macOS

cd frontend
npm run build                              # 构建即类型检查
npx tsc --noEmit
```

---

## 14. 故障排查

### 14.1 快速自检清单

```bash
docker --version && docker compose version     # ① 环境
docker info >/dev/null && echo docker-ok       # ② 守护进程
docker compose ps                              # ③ 容器状态
docker compose logs --tail=100 app             # ④ 应用日志
curl -v http://127.0.0.1:8000/api/v1/health    # ⑤ 本机连通性
ss -lntp | grep 8000                           # ⑥ 端口监听
```

### 14.2 常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| `Cannot connect to the Docker daemon` | Docker 未启动或无权限 | `sudo systemctl start docker`；或 `sudo usermod -aG docker $USER` 后重新登录 |
| `port is already allocated` | 端口被占用 | `ss -lntp \| grep 8000` 找到占用进程；或 `./deploy.sh --port 8080` 换端口 |
| 本机 `curl` 正常，浏览器打不开 | 云安全组未放行 | 控制台入方向放行 `HOST_PORT/tcp` |
| 健康检查一直不通过 | 启动期数据抓取较慢 / 依赖缺失 | `./deploy.sh --logs` 查日志；将 `DATA_SOURCE_MODE` 设为 `synthetic` 排除外网因素 |
| 页面 404 / 只返回 JSON 提示 | 前端产物未构建进镜像 | 确认 `frontend/dist/index.html` 存在；`docker compose build --no-cache app` |
| 数据源显示 `synthetic` | 外网不可达或接口超时 | 检查服务器出网与 DNS；调大 `HTTP_TIMEOUT`；或接受演示数据 |
| 内存不足 OOM 被杀 | 前端构建阶段峰值内存高 | 临时加 swap；或采用[离线部署](#12-离线内网部署)在本地构建镜像 |
| 扫描很慢 / 超时 | 首次全市场抓取无缓存 | 等待首轮完成（后续走缓存）；调大 `CACHE_TTL_SECONDS`；或减小 `UNIVERSE_SIZE` |
| 时区错误、交易日不对 | 容器时区非东八区 | `.env` 设 `TZ=Asia/Shanghai` 后 `docker compose up -d --force-recreate` |
| 镜像构建卡在 `npm ci` | npm 源缓慢 | 在 `frontend/.npmrc` 配置国内源（如 `registry=https://registry.npmmirror.com`） |
| 修改 `.env` 不生效 | 环境变量在容器创建时注入 | `docker compose up -d --force-recreate` |

### 14.3 彻底重置（数据会丢失）

```bash
docker compose down -v --remove-orphans
docker compose build --no-cache
docker compose up -d
```

---

## 15. 安全加固建议

1. **不要将端口直接暴露到公网**：优先使用反向代理 + HTTPS（第 6 节），或仅绑定 `127.0.0.1`。
2. **保持镜像更新**：`docker compose build --pull` 拉取最新基础镜像，及时获得安全补丁。
3. **非 root 运行**：镜像已内置 `appuser` 并以非 root 启动；不要用 `--privileged` 运行。
4. **最小暴露**：仅在 `docker-compose.yml` 中暴露必要端口；Caddy 与 app 通过内部网络 `app-net` 通信，app 端口无需对外发布。
5. **密钥管理**：`.env` 已在 `.gitignore` 中；生产环境建议改用 Docker Secrets 或云厂商密钥管理服务，禁止把密钥提交进仓库。
6. **访问控制**：如需公开上线，建议在反向代理层增加 Basic Auth / OAuth2 Proxy / IP 白名单；应用本身不提供用户体系。
7. **备份常态化**：至少每日备份 `pool.json` 与 `rules.json`（第 8.4 节）。
8. **日志脱敏**：`LOG_LEVEL` 生产建议 `INFO`，排查完毕后及时调回，避免 `DEBUG` 泄露请求细节。
9. **合规声明**：本系统仅用于技术研究与学习，**不构成任何投资建议**；请勿据此进行实盘交易决策。

---

## 附录 A：部署检查清单

部署完成后逐项确认：

- [ ] `docker compose ps` 中 `app` 状态为 `Up (healthy)`
- [ ] `curl http://127.0.0.1:<端口>/api/v1/health` 返回 `"status":"ok"`
- [ ] 浏览器访问 `http://<服务器IP>:<端口>` 能看到首页仪表盘
- [ ] `/signals` 页面有筛选结果（非空列表）
- [ ] 云安全组已放行对应端口
- [ ] `.env` 中 `TZ=Asia/Shanghai`、`DATA_SOURCE_MODE` 符合预期
- [ ] 已在 `/settings` 页面确认数据源标识（真实 / 演示数据）
- [ ] 已配置 `app-data` 数据卷备份策略
- [ ] 如启用 HTTPS：证书签发成功且 HTTP 自动跳转 HTTPS
- [ ] 已阅读并知悉免责声明

### A.1 本次交付的实际验证结果

以下为本项目在交付前于 Docker 容器中**实测通过**的记录，可作为验收基线。

#### A.1.1 真实行情模式（v1.1，容器内实测）

环境：容器内可访问腾讯/新浪行情接口，东方财富域名被网络阻断（用于验证故障转移）。

| 验证项 | 实测结果 |
|---|---|
| 健康检查 | `{"status":"ok","version":"1.1.0","dataSource":"tencent","universeSize":800}`，启动后 6 秒就绪 |
| **多源故障转移** | 东方财富 TLS 层被阻断 → **自动切腾讯**取日线、取股票池；`sina` 待命。**未降级为演示数据** |
| 真实行情正确性 | 上证指数 3888.11(-1.18%)、深证成指 13471.26(-1.08%)、创业板指 3322.04(-0.49%)；当日涨停 22 家、跌停 15 家 |
| 行业分类补充 | 腾讯排行榜不返回行业，已用腾讯板块接口补齐 **938/1000 只**（32 个行业），信号五恢复区分度 |
| 股票池获取 | 800 只（成交额榜），来源 `tencent`，**1.5 秒** |
| 日线增量缓存 | SQLite，800 只 / 199300 根，最新 2026-09-11 |
| 首轮全量拉取 | 约 **42 秒**（800 只 × 250 根，16 并发持续打满） |
| 缓存命中后刷新 | `/market/overview` **4.6 秒**；`/signals` **0.08 秒**；`/settings` 0.35 秒；`/sources` 0.19 秒 |
| 回测 | minScore=75 → 4.7 秒；minScore=60 → 4.4 秒，26 笔样本 / 胜率 73.1% / 盈亏比 6.67 |
| 市场时钟 | `closed / 已收盘`，`shouldPoll=false`，`nextOpenAt=2026-09-14T09:30:00+08:00`（周五收盘→下周一） |
| 容器卫生 | 非 root（`appuser`）、`healthy`、内存约 279 MB |
| 全部路由 | 8 个路由零控制台错误 |

> **注意「可低吸=0 是正确结果」**：实测当日沪深主要指数全线下跌 1%+、全市场跌停 15 家，
> 属于典型退潮日。战法要求「五信号全部共振且评分 ≥75」才输出可低吸，
> 此时正确地给出空仓结论（对应原文纪律「无信号坚决空仓」），并非缺陷。

#### A.1.2 合成演示模式（离线兜底，v1.0 起持续验证）

| 验证项 | 实测结果 |
|---|---|
| 镜像构建 | `docker build` 成功（Node 构建前端 → Python 运行时），镜像 335 MB |
| 容器启动 | 约 4～6 秒后 `/api/v1/health` 返回 `status=ok` |
| 容器健康检查 | `docker inspect --format '{{.State.Health.Status}}'` → `healthy` |
| 非 root 运行 | `docker exec <容器> whoami` → `appuser` |
| 内存占用 | 约 110 MB（合成模式）/ 279 MB（真实模式，含行情缓存） |
| SPA 深层路由 | `/`、`/signals`、`/stocks`、`/stock/:code`、`/backtest`、`/pool`、`/rules`、`/settings` 全部正常，**控制台零错误、零页面异常**，无整页横向滚动 |
| 全部接口 | 17 个接口 + 错误路径全部符合契约（重复加入低吸池 409、删除不存在条目 404、查询不存在任务 404） |
| 接口耗时 | `/signals` 0.03 s、`/stocks` 0.03 s、`/market/overview` 0.43 s、`/backtest` 0.60 s |
| 数据持久化 | `POST /pool` → `docker restart` → 条目保留；`PUT /rules` → 重启 → 参数保留 |
| 后端测试 | `python -m pytest -q` → **111 passed** |
| 前端类型与构建 | `npx tsc --noEmit` 零错误 / `npm run build` 成功（约 394 KB） |
| 绝对地址扫描 | 前端 `src` 与后端 `app` 全量正则扫描 → 无匹配 |
| 部署脚本语法 | `shellcheck deploy.sh` / `bash -n deploy.sh` → 均无告警 |
| PowerShell 脚本 | `powershell -File deploy.ps1 -Status` → 正常输出中文状态（UTF-8 with BOM） |

> `/stock/:code` 这类多级路由要求前端以**根路径**提供静态资源
> （`vite.config.ts` 中 `base: '/'`）。若改成相对路径 `base: './'`，
> 深层路由下的资源会被解析为 `/stock/assets/...` 并命中 SPA 回退返回 HTML，
> 浏览器将因 MIME 类型不符拒绝执行脚本，页面直接白屏。**请勿修改该配置。**


## 附录 B：端口与文件速查

| 项目 | 值 |
|---|---|
| 应用默认端口 | `8000` |
| 容器内工作目录 | `/app/backend` |
| SPA 静态资源 | `/app/frontend/dist` |
| 数据目录（数据卷） | `/app/backend/data` |
| 健康检查端点 | `GET /api/v1/health` |
| 接口文档（开发态） | `GET /docs` |
| 核心配置模板 | `.env.example` |
| 一键部署脚本 | `deploy.sh` / `deploy.ps1` |
| 编排文件 | `docker-compose.yml` |
| 镜像定义 | `Dockerfile` |
| 反代配置 | `deploy/Caddyfile` |
