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

### 4.1 国内网络加速（**必看**）

国内云服务器直连 Docker Hub 与 PyPI 会**极慢甚至构建失败**。实测数据（某国内云服务器）：

```
python:3.12-slim 基础镜像拉取      3845 秒（约 64 分钟）
Downloading numpy-2.5.3.whl (16.7MB)   18.4 kB/s → 耗时 18 分钟
Downloading pandas-3.0.5.whl (11.0MB)  253.6 kB/s → 读到 7.6MB 时超时
pip._vendor.urllib3.exceptions.ReadTimeoutError
  → failed to solve: process "pip install ..." did not complete successfully: exit code 2
```

**本项目已内置纯国内镜像默认值**（`Dockerfile` + `docker-compose.yml` 的 `build.args`），
一键脚本会自动传入，**不依赖任何境外源**，正常情况下无需任何额外配置：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `PIP_INDEX_URL` | `https://pypi.tuna.tsinghua.edu.cn/simple` | Python 包主源（清华 TUNA，实测响应 2.0s） |
| `PIP_EXTRA_INDEX_URL` | `https://pypi.mirrors.ustc.edu.cn/simple` | 备用源（中科大 USTC，同为国内，实测 1.4s） |
| `NPM_REGISTRY` | `https://registry.npmmirror.com` | npm 包源（阿里 npmmirror） |

> 实测效果：同一台机器上，纯国内镜像的 pip 阶段 **72.8 秒** 完成；
> 而直连官方源时 numpy 单独就要 18 分钟、pandas 直接读超时。

可替换的其它国内 PyPI 镜像（实测均可用）：

| 镜像 | 地址 |
|---|---|
| 清华 TUNA | `https://pypi.tuna.tsinghua.edu.cn/simple` |
| 中科大 USTC | `https://pypi.mirrors.ustc.edu.cn/simple/` |
| 阿里云 | `https://mirrors.aliyun.com/pypi/simple/` |
| 腾讯云 | `https://mirrors.cloud.tencent.com/pypi/simple/` |
| 华为云 | `https://repo.huaweicloud.com/repository/pypi/simple/` |

海外服务器请在 `.env` 中改回官方源：

```bash
PIP_INDEX_URL=https://pypi.org/simple
PIP_EXTRA_INDEX_URL=
NPM_REGISTRY=https://registry.npmjs.org
```

#### 加速 Docker Hub（建议在国内服务器上配置）

即使 pip/npm 走国内镜像，**基础镜像仍从 Docker Hub 拉取**（实测 `python:3.12-slim` 用了 64 分钟）。配置镜像加速：

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run",
    "https://dockerproxy.com"
  ],
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
```

> 可用镜像站会随时间变化，若上述地址失效请搜索当前可用的 Docker 镜像加速器。

#### 更稳妥的替代方案：本地构建后导入

网络实在不可靠时，在**有良好网络的机器**上构建镜像再导入服务器（同时避免服务器长时间占用）：

```bash
# ---------- 有网机器（架构需与服务器一致，如同一为 x86_64） ----------
docker build -t limit-up-pullback:latest .
docker save limit-up-pullback:latest | gzip > limit-up-pullback.tar.gz

# 传输
scp limit-up-pullback.tar.gz user@<服务器IP>:/opt/

# ---------- 目标服务器 ----------
gunzip -c /opt/limit-up-pullback.tar.gz | docker load
docker compose up -d --no-build      # 直接用已导入的镜像，不再构建
```

**arm64 服务器注意**：若在有网机器上构建，必须指定目标架构，否则导入后无法运行：

```bash
docker buildx build --platform linux/amd64 -t limit-up-pullback:latest --load .
```

#### 若构建仍然失败

`exit code 2` 只是 pip 的退出码，**真正原因在它上面的日志里**。定位方法：

```bash
# 只看 pip 阶段，保留完整输出
docker build --progress=plain --no-cache -t limit-up-pullback:test . 2>&1 | tee build.log
# 然后查关键行
grep -nE 'ERROR|ReadTimeout|Could not find|No matching distribution|Downloading (numpy|pandas)' build.log | tail -40
```

常见原因与对策：

| 日志特征 | 原因 | 对策 |
|---|---|---|
| `ReadTimeoutError ... files.pythonhosted.org` | PyPI 网络超时 | 已默认走国内镜像；确认 `.env` 未被改成官方源 |
| `Could not find a version that satisfies` | Python 版本不匹配 | 本项目要求 Python 3.12（镜像已固定） |
| `No matching distribution ... for cp312` | 平台无对应 wheel | 检查是否跨架构（见上方 arm64 说明） |
| `/bin/sh: gcc: not found` | 需源码编译但无编译器 | 极少数情况；可 `apt-get install build-essential python3-dev` |

## 4.2 以普通用户部署

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
| `DATA_SOURCE_MODE` | `auto` | `auto`：按 `DATA_SOURCE_ORDER` 依次尝试真实源，全部不可用才降级为演示数据；`real`：绝不降级，全部真实源失败即报错（HTTP 503，生产推荐）；`eastmoney`/`tencent`/`ths`/`sina`/`yahoo`：强制单一源；`synthetic`：强制合成数据，完全离线 |
| `CACHE_TTL_SECONDS` | `300` | 行情缓存有效期（秒），降低外部接口压力 |
| `KLINE_MEMORY_CACHE_SECONDS` | `900` | **进程内**日线读穿缓存有效期（秒），`0` = 关闭。它只代替 SQLite 读取，不参与新鲜度判定，因此不影响开盘期间 30 秒刷新的语义。全市场约占用 150MB 内存；内存紧张的机器可设为 `0`，代价是每次请求都要重建日线（慢很多） |
| `KLINE_MEMORY_CACHE_MAX_CODES` | `8000` | 进程内缓存最多持有的股票数，超出即整体重建 |
| `WARMUP_ON_STARTUP` | `true` | 启动后后台预热全市场日线（首轮约 3 分钟）。不阻塞服务启动；设为 `false` 可关闭 |
| `WARMUP_WAIT_MIN_CODES` | `500` | 请求股票数达到该值时先等待预热完成，避免与预热重复取同一批数据；单只股票请求不受影响 |
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

> **更省事的做法**：直接运行 `./diagnose.sh`，它把上面六步连同内存、OOM 记录、
> 「对外端口到底是谁在监听」一起检查完，并在末尾给出结论。
> 遇到「502」「全站加载失败」请直接看 [14.3](#143-502--请求超时--全站加载失败)。

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
| `./diagnose.sh: No such file or directory` | 服务器上的克隆不是最新代码（旧提交里没有该脚本） | `git fetch origin && git pull --ff-only`；或被拒时 `git checkout origin/main -- diagnose.sh` |
| `./diagnose.sh: Permission denied` | 可执行位丢失（从 Windows 拷贝等） | 用 `sh diagnose.sh`，或 `chmod +x diagnose.sh` |

### 14.3 502 / 请求超时 / 全站加载失败

部署完成后打开页面，如果出现「数据加载失败」「请求超时（15 秒）」或「服务器异常（HTTP 502）」，
**先在服务器上运行自检脚本**，它会一次性打印定位问题所需的全部信息：

```bash
cd <项目目录>
sh diagnose.sh                # 用 sh 调用最稳妥（不依赖可执行位）
sh diagnose.sh --port 8080    # 端口不是默认值时手动指定
```

> **若提示 `./diagnose.sh: No such file or directory`**：这不是文件损坏，
> 而是**你服务器上的代码不是最新的**（旧克隆停在较早的提交，那时还没有这个脚本）。
> 执行下面的命令即可取到最新代码：
>
> ```bash
> git fetch origin
> git log --oneline -1 origin/main     # 确认远端最新提交
> git pull --ff-only                   # 若本地无改动
> # 本地有改动导致 pull 被拒时，只取这一个文件（无需处理分支）：
> git checkout origin/main -- diagnose.sh
> chmod +x diagnose.sh
> ```
>
> 若用 `./diagnose.sh` 提示 `Permission denied`，同样用 `sh diagnose.sh` 即可绕过。

#### 14.3.1 先分清「502」和「超时」——性质完全不同

| 现象 | 含义 | 问题出在哪 |
|---|---|---|
| **HTTP 502 Bad Gateway** | 前置代理**连不上后端**（连接被拒/无法解析） | **代理层到后端之间**，不是应用代码 |
| **HTTP 504 / 请求超时** | 后端在处理，但太久没返回 | 后端太慢（多为全市场首轮预热未完成） |
| **HTTP 503** | 后端正常响应，但真实数据源全不可用 | `DATA_SOURCE_MODE=real` 时的预期行为 |

> **关键事实：本项目后端从不返回 502。** 它只会返回 200 / 4xx 及 500（内部错误）、503（数据源不可用）。
> 因此**只要看到 502，就一定有一层反向代理（Nginx / 宝塔 / Caddy / 云厂商网关）在你和容器之间**，
> 而它没能把请求转发到容器。这是本项目采用「单容器同源部署」时最容易被忽略的一环：
> 镜像内部没有 Nginx，对外端口本应由 Docker 直接映射。

#### 14.3.2 三种典型情况与处理

**情况 D：首屏很慢或大面积超时（后端正常时的性能问题）**

全市场（约 5500 只）的日线重建与首轮取数都很重。项目已针对此做了三层处理，
若你改动过相关配置或机器特别小，可按下面的数字自查：

| 环节 | 实测（全市场 5561 只） | 处理 |
|---|---|---|
| 首轮从上游取全部日线 | **约 195 秒**（一次性） | 已内置**启动后台预热**（`WARMUP_ON_STARTUP`），部署完成后稍等再访问 |
| 从 SQLite 重建全部日线 | **43.94 秒**（同步执行时会占死事件循环） | 已改为线程池执行 + **进程内读穿缓存**（`KLINE_MEMORY_CACHE_SECONDS`） |
| 批量写回 SQLite | 数十秒（早期是 5561 次事务提交） | 已改为**单事务批量写入** |
| 预热完成后的接口 | `/market/overview` 2.1s、`/stocks` 0.10s、`/signals` 0.01s、`/settings` 0.33s | — |

判断预热是否完成：

```bash
curl -s http://127.0.0.1:8000/api/v1/health | grep -o '"scanReady":[a-z]*'
```

`scanReady:false` = 预热进行中，此时重接口慢属正常。前端对重接口已放宽到 180 秒超时。

> 内存提示：全市场 + 进程内缓存满负荷时，容器实测占用约 **560MB**。
> 1GB 内存的机器建议把 `UNIVERSE_SIZE` 设为 1500~3000，或把
> `KLINE_MEMORY_CACHE_SECONDS=0` 关掉内存缓存（以速度换内存）。

**情况 A：容器没在运行（代理自然返回 502）**

```bash
docker ps -a | grep limit-up-app          # 看 STATUS 是否为 Up
docker logs --tail 100 limit-up-app       # 看启动报错
```

若 `STATUS` 显示 `Restarting` 或退出码非 0，多为**内存不足被系统杀掉**（全市场约 5500 只，
峰值内存数百 MB，小规格云主机容易触发）。处理：

```bash
# 1) 加 swap（最有效，成本最低）
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 2) 或调小股票池规模后重启（先跑起来，再逐步放大）
sed -i 's/^UNIVERSE_SIZE=.*/UNIVERSE_SIZE=1500/' .env
./deploy.sh
```

确认是否真被 OOM：`dmesg | grep -i 'killed process'`（需 sudo）。

**情况 B：端口被另一层服务占用（对外看到的其实不是本容器）**

```bash
ss -lntp | grep ':8000'      # 看监听进程是 docker-proxy 还是 nginx/openresty
curl -sI http://127.0.0.1:8000/api/v1/health | grep -i '^server:'
```

- 显示 `docker-proxy` / `server: uvicorn` → 直达容器，正常；
- 显示 `nginx` / `openresty` → **前面确实有代理**，502 由它产生。检查它的 `proxy_pass`
  是否指向容器实际暴露的宿主机端口（即 `.env` 里的 `HOST_PORT`），而不是写死的 `8000`：

```bash
grep -n 'proxy_pass' /etc/nginx/conf.d/*.conf /etc/nginx/sites-enabled/* 2>/dev/null
docker port limit-up-app        # 核对容器真正映射出的宿主机端口
```

宝塔面板用户：在「网站 → 反向代理」里把目标 URL 改成 `http://127.0.0.1:<HOST_PORT>`。

**情况 C：后端正常，只是首轮行情还没预热完**

全市场首次取数需要**数分钟**（本地实测 8 分钟，云主机更久），期间重接口必然很慢。
判断方法：

```bash
curl -s http://127.0.0.1:8000/api/v1/health
# "scanReady": false  → 预热中，属正常
# "scanReady": true   → 已就绪，此时仍失败才是真故障
# "memoryMB": 350     → 可据此判断是否接近内存上限
# "sources": {"usable": ["tencent","ths","sina"], "allSkipped": false}
```

前端已按接口重量分级设置超时（重接口 180 秒），因此**预热期不再是「全站加载失败」**，
耐心等待首轮完成即可。想加速可先缩小股票池：

```bash
sed -i 's/^UNIVERSE_SIZE=.*/UNIVERSE_SIZE=1000/' .env && ./deploy.sh
```

#### 14.3.3 一键收集排障信息

```bash
{
  echo "== 容器 ==";   docker ps -a --filter name=limit-up-app
  echo "== 端口 ==";   ss -lntp | grep ':8000'
  echo "== 容器内 =="; docker exec limit-up-app curl -s http://127.0.0.1:8000/api/v1/health
  echo "== 宿主 ==";   curl -s http://127.0.0.1:8000/api/v1/health
  echo "== 日志 ==";   docker logs --tail 80 limit-up-app 2>&1
  echo "== 内存 ==";   free -h
} 2>&1 | tee diagnose-$(date +%Y%m%d%H%M).log
```

`./diagnose.sh` 已把上述检查与自动结论整合在一起，推荐直接用它。

---

### 14.4 彻底重置（数据会丢失）

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
