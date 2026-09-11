#!/usr/bin/env bash
# =============================================================================
# 涨停回调低吸战法 —— 一键部署脚本（Linux / macOS）
#
# 用法：
#   ./deploy.sh                      # 一键部署（首次自动创建 .env）
#   ./deploy.sh --port 8080          # 指定对外端口
#   ./deploy.sh --https              # 额外启用 Caddy 自动 HTTPS（需交互填域名）
#   ./deploy.sh --mode real          # 生产推荐：只用真实行情，绝不降级为演示数据
#   ./deploy.sh --mode synthetic     # 强制使用内置演示数据（完全离线）
#   ./deploy.sh --universe 2000      # 真实模式扫描的股票数量（成交额前 N 只）
#   ./deploy.sh --interval 30        # 开盘期间的刷新间隔（秒）
#   ./deploy.sh --update             # 拉取最新代码并重建部署
#   ./deploy.sh --logs               # 查看实时日志
#   ./deploy.sh --status             # 查看运行状态与健康检查
#   ./deploy.sh --stop               # 停止服务（保留数据卷）
#   ./deploy.sh --down               # 停止并移除容器（保留数据卷）
#   ./deploy.sh --destroy            # 停止并删除容器 + 数据卷（危险）
#   ./deploy.sh --help
#
# 特性：
#   * 自动探测 docker compose / docker-compose
#   * 自动创建 .env 并生成随机 CORS 配置
#   * 自动放行 ufw 防火墙端口（如已安装 ufw）
#   * 部署后自动轮询健康检查，打印访问地址，并显示生效数据源
#   * 全程使用相对路径，不含任何绝对路径
# =============================================================================

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# 基础定义
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE=".env"
ENV_SAMPLE=".env.example"
COMPOSE_FILE="docker-compose.yml"

# 颜色（仅在 TTY 下生效）
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""
fi

log()   { printf '%s[信息]%s %s\n' "$C_BLUE"   "$C_RESET" "$*"; }
ok()    { printf '%s[成功]%s %s\n' "$C_GREEN"  "$C_RESET" "$*"; }
warn()  { printf '%s[警告]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
err()   { printf '%s[错误]%s %s\n' "$C_RED"    "$C_RESET" "$*" >&2; }
title() { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_RESET"; }

# ---------------------------------------------------------------------------
# 默认参数
# ---------------------------------------------------------------------------
OPT_PORT=""
OPT_MODE=""
OPT_UNIVERSE=""
OPT_INTERVAL=""
OPT_HTTPS=0
OPT_DOMAIN=""
OPT_EMAIL=""
ACTION="deploy"

usage() {
  sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --port)     OPT_PORT="${2:-}"; shift 2 ;;
    --mode)     OPT_MODE="${2:-}"; shift 2 ;;
    --universe) OPT_UNIVERSE="${2:-}"; shift 2 ;;
    --interval) OPT_INTERVAL="${2:-}"; shift 2 ;;
    --domain)   OPT_DOMAIN="${2:-}"; shift 2 ;;
    --email)    OPT_EMAIL="${2:-}"; shift 2 ;;
    --https)    OPT_HTTPS=1; shift ;;
    --update)   ACTION="update"; shift ;;
    --logs)     ACTION="logs"; shift ;;
    --status)   ACTION="status"; shift ;;
    --stop)     ACTION="stop"; shift ;;
    --down)     ACTION="down"; shift ;;
    --destroy)  ACTION="destroy"; shift ;;
    -h|--help)  usage ;;
    *) err "未知参数: $1（使用 --help 查看用法）"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# 依赖与环境探测
# ---------------------------------------------------------------------------
detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
  else
    err "未检测到 Docker Compose。请先安装 Docker Engine 24+ 或 docker-compose-plugin。"
    err "一键安装参考：curl -fsSL https://get.docker.com | sh"
    exit 1
  fi
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    err "未检测到 Docker。请先安装：curl -fsSL https://get.docker.com | sh"
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    err "Docker 守护进程未运行或无权限访问。"
    err "请执行：sudo systemctl start docker   （或将当前用户加入 docker 组后重新登录）"
    exit 1
  fi
  detect_compose
  ok "Docker 环境就绪：$(docker --version)"
}

# ---------------------------------------------------------------------------
# 读取 / 生成 .env
# ---------------------------------------------------------------------------
env_get() {
  # 读取 .env 中某个键的值（不存在则输出空）
  [ -f "$ENV_FILE" ] || { printf ''; return; }
  awk -F= -v k="$1" '$1==k { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE"
}

env_set() {
  # 幂等地设置 .env 中的键值
  local key="$1" val="$2"
  if [ ! -f "$ENV_FILE" ]; then : > "$ENV_FILE"; fi
  if grep -qE "^${key}=" "$ENV_FILE"; then
    # 使用 | 作为分隔符，避免值中出现 / 时出错
    sed -i.bak -E "s|^${key}=.*$|${key}=${val}|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

prepare_env() {
  if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_SAMPLE" ]; then
      cp "$ENV_SAMPLE" "$ENV_FILE"
      ok "已根据 $ENV_SAMPLE 生成 $ENV_FILE"
    else
      warn "$ENV_SAMPLE 不存在，将生成最小化 .env"
      cat > "$ENV_FILE" <<'EOF'
HOST_PORT=8000
APP_PORT=8000
DATA_SOURCE_MODE=auto
TZ=Asia/Shanghai
EOF
    fi
  else
    log "复用已存在的 $ENV_FILE"
  fi

  # 命令行覆盖
  [ -n "$OPT_PORT" ] && env_set HOST_PORT "$OPT_PORT" && log "对外端口设为 $OPT_PORT"
  [ -n "$OPT_MODE" ] && env_set DATA_SOURCE_MODE "$OPT_MODE" && log "数据源模式设为 $OPT_MODE"
  [ -n "$OPT_UNIVERSE" ] && env_set UNIVERSE_SIZE "$OPT_UNIVERSE" && log "股票池规模设为 $OPT_UNIVERSE"
  [ -n "$OPT_INTERVAL" ] && env_set REFRESH_INTERVAL_SECONDS "$OPT_INTERVAL" && log "开盘刷新间隔设为 ${OPT_INTERVAL}s"
  [ -n "$OPT_DOMAIN" ] && env_set DOMAIN "$OPT_DOMAIN"
  [ -n "$OPT_EMAIL" ] && env_set ACME_EMAIL "$OPT_EMAIL"

  # 端口合法性校验
  local hp; hp="$(env_get HOST_PORT)"; hp="${hp:-8000}"
  case "$hp" in
    ''|*[!0-9]*) err "HOST_PORT 必须为数字，当前为「$hp」"; exit 1 ;;
  esac
  if [ "$hp" -lt 1 ] || [ "$hp" -gt 65535 ]; then
    err "HOST_PORT 超出范围 1-65535，当前为 $hp"; exit 1
  fi

  # 股票池规模与刷新间隔校验
  local us; us="$(env_get UNIVERSE_SIZE)"; us="${us:-1000}"
  case "$us" in
    ''|*[!0-9]*) err "UNIVERSE_SIZE 必须为数字，当前为「$us」"; exit 1 ;;
  esac
  local ri; ri="$(env_get REFRESH_INTERVAL_SECONDS)"; ri="${ri:-30}"
  case "$ri" in
    ''|*[!0-9]*) err "REFRESH_INTERVAL_SECONDS 必须为数字，当前为「$ri」"; exit 1 ;;
  esac

  # 数据源模式校验
  local dm; dm="$(env_get DATA_SOURCE_MODE)"; dm="${dm:-auto}"
  case "$dm" in
    auto|real|eastmoney|tencent|ths|sina|synthetic) ;;
    *) err "DATA_SOURCE_MODE 只能为 auto / real / eastmoney / tencent / ths / sina / synthetic，当前为「$dm」"; exit 1 ;;
  esac

  HOST_PORT_RESOLVED="$hp"
  MODE_RESOLVED="$dm"
}

# ---------------------------------------------------------------------------
# 放行防火墙端口
# ---------------------------------------------------------------------------
open_firewall() {
  local port="$1"
  if command -v ufw >/dev/null 2>&1; then
    if ufw status 2>/dev/null | grep -q "Status: active"; then
      if ufw status 2>/dev/null | grep -qE "^${port}(/tcp)?\s+ALLOW"; then
        log "ufw 已放行端口 ${port}"
      else
        log "尝试放行 ufw 端口 ${port} ..."
        if sudo ufw allow "${port}/tcp" >/dev/null 2>&1; then
          ok "已放行 ufw ${port}/tcp"
        else
          warn "ufw 放行失败，请手动执行：sudo ufw allow ${port}/tcp"
        fi
      fi
    fi
  fi
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    log "尝试放行 firewalld 端口 ${port} ..."
    if sudo firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null 2>&1 \
       && sudo firewall-cmd --reload >/dev/null 2>&1; then
      ok "已放行 firewalld ${port}/tcp"
    else
      warn "firewalld 放行失败，请手动执行：sudo firewall-cmd --permanent --add-port=${port}/tcp && sudo firewall-cmd --reload"
    fi
  fi
  warn "若为云服务器（阿里云/腾讯云/华为云等），请在控制台安全组中放行 ${port}/tcp 入方向。"
}

# ---------------------------------------------------------------------------
# 健康检查轮询
# ---------------------------------------------------------------------------
wait_healthy() {
  local port="$1"
  local tries="${2:-60}"
  local url="http://127.0.0.1:${port}/api/v1/health"
  local i=1
  log "等待服务就绪：${url}"
  while [ "$i" -le "$tries" ]; do
    if command -v curl >/dev/null 2>&1; then
      if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
        ok "服务健康检查通过（第 ${i} 次探测）"
        curl -fsS --max-time 3 "$url" || true
        printf '\n'
        return 0
      fi
    else
      # 无 curl 时用 compose health 状态兜底
      if "${COMPOSE_CMD[@]}" ps --format json 2>/dev/null | grep -q '"Health":"healthy"'; then
        ok "容器健康状态为 healthy"
        return 0
      fi
    fi
    sleep 2
    i=$((i + 1))
  done
  warn "健康检查在预期时间内未通过，请执行 ./deploy.sh --logs 查看日志。"
  return 1
}

print_access() {
  local port="$1" ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -z "${ip:-}" ] && ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
  [ -z "${ip:-}" ] && ip="<服务器公网IP>"
  title "=============================================="
  ok "部署完成！"
  echo "  本机访问    : http://127.0.0.1:${port}"
  echo "  局域网/公网 : http://${ip}:${port}"
  if [ -n "$(env_get DOMAIN)" ]; then
    echo "  域名访问    : https://$(env_get DOMAIN)"
  fi
  echo
  echo "  数据源模式  : ${MODE_RESOLVED}"

  # 读取实际生效的数据源，让用户立刻知道拿到的是真实行情还是演示数据
  local active
  active="$(curl -fsS --max-time 5 "http://127.0.0.1:${port}/api/v1/health" 2>/dev/null \
            | sed -n 's/.*"dataSource"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  if [ -n "${active:-}" ]; then
    case "$active" in
      synthetic)
        warn "生效数据源  : synthetic（内置演示数据）"
        warn "  说明：全部真实行情源当前不可达，已自动降级。"
        warn "  排查：docker compose exec app curl -sI https://qt.gtimg.cn/q=sh600519"
        ;;
      *)
        ok "生效数据源  : ${active}（真实行情）"
        ;;
    esac
  fi

  echo
  echo "  刷新策略    : 开盘期间每 $(env_get REFRESH_INTERVAL_SECONDS 2>/dev/null || echo 30) 秒自动刷新"
  echo "  股票池规模  : $(env_get UNIVERSE_SIZE 2>/dev/null || echo 1000) 只"
  echo "  常用命令    : ./deploy.sh --logs | --status | --update | --stop"
  echo "  完整部署文档: docs/DEPLOYMENT.md"
  title "=============================================="
}

# ---------------------------------------------------------------------------
# 各动作实现
# ---------------------------------------------------------------------------
ensure_env_https() {
  local domain; domain="$(env_get DOMAIN)"
  if [ -z "$domain" ]; then
    echo
    warn "启用 HTTPS 需要域名。请先在 DNS 将该域名 A 记录解析到本服务器公网 IP。"
    printf '请输入域名（例如 stock.example.com，直接回车跳过）: '
    read -r domain || true
    [ -n "$domain" ] && env_set DOMAIN "$domain"
  fi
  local email; email="$(env_get ACME_EMAIL)"
  if [ -n "$domain" ] && [ -z "$email" ]; then
    printf '请输入用于 Let'"'"'s Encrypt 通知的邮箱: '
    read -r email || true
    [ -n "$email" ] && env_set ACME_EMAIL "$email"
  fi
  if [ -z "$domain" ]; then
    warn "未提供域名，HTTPS 代理将退化为纯 HTTP 反向代理。"
  fi
}

build_and_up() {
  local profile_args=()
  if [ "$OPT_HTTPS" -eq 1 ]; then
    ensure_env_https
    if [ -n "$(env_get DOMAIN)" ]; then
      profile_args=(--profile https)
      open_firewall 80
      open_firewall 443
      log "已启用 Caddy 反向代理 + 自动 HTTPS"
    else
      warn "跳过 HTTPS：未配置域名"
    fi
  fi

  # 首次部署时放开 HOST_PORT 防火墙；HTTPS 模式下 80/443 已单独处理
  if [ "$OPT_HTTPS" -eq 0 ]; then
    open_firewall "$HOST_PORT_RESOLVED"
  fi

  title ">>> 构建镜像与启动容器"
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" "${profile_args[@]}" up -d --build --remove-orphans

  title ">>> 等待服务启动"
  if wait_healthy "$HOST_PORT_RESOLVED"; then
    print_access "$HOST_PORT_RESOLVED"
  else
    warn "服务可能仍在初始化。查看日志：./deploy.sh --logs"
    exit 1
  fi
}

action_deploy() {
  title ">>> 检查运行环境"
  require_docker
  prepare_env
  build_and_up
}

action_update() {
  title ">>> 更新部署"
  require_docker
  prepare_env
  if [ -d ".git" ] && command -v git >/dev/null 2>&1; then
    log "拉取最新代码 ..."
    git pull --ff-only || warn "git pull 失败，跳过（继续使用当前代码）"
  else
    log "非 Git 仓库，跳过代码拉取"
  fi
  build_and_up
}

action_logs() {
  require_docker
  prepare_env
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" logs -f --tail=200
}

action_status() {
  require_docker
  prepare_env
  title ">>> 容器状态"
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" ps
  title ">>> 健康检查"
  local port="$HOST_PORT_RESOLVED"
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 5 "http://127.0.0.1:${port}/api/v1/health"; then
      printf '\n'; ok "应用健康"
    else
      printf '\n'; warn "应用未响应"
    fi
  else
    warn "未安装 curl，跳过 HTTP 健康检查"
  fi
  title ">>> 资源占用"
  docker stats --no-stream "$(env_get CONTAINER_NAME || true)" 2>/dev/null || \
    docker stats --no-stream 2>/dev/null | head -5 || true
}

action_stop() {
  require_docker
  prepare_env
  log "停止服务（数据卷保留）"
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" stop
  ok "已停止。重新启动：./deploy.sh"
}

action_down() {
  require_docker
  prepare_env
  log "移除容器与网络（数据卷保留）"
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" down --remove-orphans
  ok "已移除。数据仍保存在 Docker 卷中。"
}

action_destroy() {
  require_docker
  prepare_env
  warn "该操作将删除容器、网络以及【全部数据卷】（行情缓存/策略参数/低吸池）！"
  printf '请输入 YES 确认: '
  read -r ans || true
  if [ "$ans" != "YES" ]; then warn "已取消"; exit 0; fi
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --profile https down -v --remove-orphans
  ok "已彻底清理。"
}

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
main() {
  case "$ACTION" in
    deploy)  action_deploy ;;
    update)  action_update ;;
    logs)    action_logs ;;
    status)  action_status ;;
    stop)    action_stop ;;
    down)    action_down ;;
    destroy) action_destroy ;;
    *) err "未知动作: $ACTION"; exit 1 ;;
  esac
}

main "$@"
