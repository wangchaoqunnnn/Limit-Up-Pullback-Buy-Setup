#!/usr/bin/env sh
# =============================================================================
# 涨停回调低吸战法 —— 部署自检脚本（只读，不改动任何配置）
#
# 用途：当浏览器访问出现「502 Bad Gateway」「请求超时（15 秒）」
#      「所有数据加载失败」时，在**服务器上**执行本脚本，
#      一次输出定位问题所需的全部信息：
#
#         ./diagnose.sh              # 自动读取同目录 .env
#         ./diagnose.sh --port 8000  # 手动指定对外端口
#
# 核心原理（为什么这份输出足以定位问题）：
#   **本项目后端从不返回 502**（它只返回 200/4xx/5xx JSON）。
#   502 Bad Gateway 只能由「前置反向代理」产生 —— 代理连不上后端时才会这样。
#   因此脚本会重点回答两个问题：
#     1) 对外端口到底是谁在监听？是 Docker 直接映射，还是 Nginx/宝塔在前？
#     2) 从「宿主机」和「容器内」分别访问后端，结果是否一致？
#   两者的差异即可判定故障发生在「容器/后端」还是「代理」。
# =============================================================================

set -u

# --------------------------------------------------------------------- 输出
if [ -t 1 ]; then
  C_RED=$(printf '\033[31m'); C_GRN=$(printf '\033[32m')
  C_YEL=$(printf '\033[33m'); C_CYN=$(printf '\033[36m'); C_OFF=$(printf '\033[0m')
else
  C_RED=''; C_GRN=''; C_YEL=''; C_CYN=''; C_OFF=''
fi
sec()  { printf '\n%s===== %s =====%s\n' "$C_CYN" "$1" "$C_OFF"; }
ok()   { printf '  %s[OK]%s   %s\n'   "$C_GRN" "$C_OFF" "$1"; }
bad()  { printf '  %s[!!]%s   %s\n'   "$C_RED" "$C_OFF" "$1"; }
warn() { printf '  %s[?]%s    %s\n'   "$C_YEL" "$C_OFF" "$1"; }
info() { printf '         %s\n' "$1"; }

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR" || exit 1

# --------------------------------------------------------------------- 参数
PORT=""
CONTAINER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --port)      PORT="${2:-}"; shift 2 ;;
    --container) CONTAINER="${2:-}"; shift 2 ;;
    -h|--help)   sed -n '2,25p' "$0"; exit 0 ;;
    *)           printf '未知参数：%s（可用 --port / --container）\n' "$1"; exit 2 ;;
  esac
done

env_get() {
  [ -f .env ] || return 0
  # 取最后一个匹配项，忽略注释行
  grep -E "^[[:space:]]*$1[[:space:]]*=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- \
    | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//'
}

[ -n "$PORT" ]      || PORT="$(env_get HOST_PORT)";      PORT="${PORT:-8000}"
[ -n "$CONTAINER" ] || CONTAINER="$(env_get CONTAINER_NAME)"; CONTAINER="${CONTAINER:-limit-up-app}"
APP_PORT="$(env_get APP_PORT)"; APP_PORT="${APP_PORT:-8000}"

printf '%s\n' "涨停回调低吸战法 —— 部署自检"
info "脚本目录    : $SCRIPT_DIR"
info "对外端口    : $PORT"
info "容器内端口  : $APP_PORT"
info "容器名      : $CONTAINER"
info "主机时间    : $(date '+%Y-%m-%d %H:%M:%S %Z')"

# --------------------------------------------------------------------- 1 环境
sec "1. 运行环境"
if command -v docker >/dev/null 2>&1; then
  ok "docker 已安装：$(docker --version 2>/dev/null)"
  if docker info >/dev/null 2>&1; then
    ok "docker 守护进程可用"
  else
    bad "docker 守护进程不可用（未启动，或当前用户无权限：需加入 docker 组或 sudo）"
  fi
else
  bad "未找到 docker 命令"
fi
if docker compose version >/dev/null 2>&1; then
  ok "docker compose（插件版）可用"
elif command -v docker-compose >/dev/null 2>&1; then
  ok "docker-compose（独立版）可用"
else
  warn "未检测到 docker compose"
fi

# --------------------------------------------------------------------- 2 容器
sec "2. 容器状态（这是「后端是否活着」的直接证据）"
if docker info >/dev/null 2>&1; then
  running=$(docker ps --filter "name=^/${CONTAINER}$" --format '{{.Names}}' 2>/dev/null)
  all=$(docker ps -a --filter "name=^/${CONTAINER}$" --format '{{.Names}}' 2>/dev/null)
  if [ -n "$running" ]; then
    ok "容器 $CONTAINER 正在运行"
    docker ps --filter "name=^/${CONTAINER}$" \
      --format '  状态: {{.Status}}   镜像: {{.Image}}   端口: {{.Ports}}'
  elif [ -n "$all" ]; then
    bad "容器 $CONTAINER 存在但**未在运行** → 这就会导致代理返回 502"
    docker ps -a --filter "name=^/${CONTAINER}$" --format '  状态: {{.Status}}'
  else
    bad "未找到名为 $CONTAINER 的容器（是否根本没启动？）"
  fi

  # 重启次数与 OOM：反复重启 / 被内存杀手干掉，是最常见的「间歇性 502 + 超时」
  inspect=$(docker inspect "$CONTAINER" 2>/dev/null)
  if [ -n "$inspect" ]; then
    restarts=$(printf '%s' "$inspect" | grep -o '"RestartCount":[[:space:]]*[0-9]*' | head -n 1 | grep -o '[0-9]*$')
    oom=$(printf '%s' "$inspect" | grep -o '"OOMKilled":[[:space:]]*[a-z]*' | head -n 1 | awk '{print $2}')
    exitcode=$(printf '%s' "$inspect" | grep -o '"ExitCode":[[:space:]]*[0-9-]*' | head -n 1 | grep -o '[0-9-]*$')
    [ -n "$restarts" ] && info "重启次数    : $restarts $([ "${restarts:-0}" -gt 3 ] 2>/dev/null && printf '← 反复重启，说明启动后即崩溃（多为内存不足）')"
    if [ -n "$oom" ]; then
      if [ "$oom" = "true" ]; then
        bad "曾被 OOMKilled（内存不足被系统杀掉）→ 需要加 swap 或限制股票池规模"
      else
        info "OOMKilled   : false"
      fi
    fi
    [ -n "$exitcode" ] && info "上次退出码  : $exitcode"
    health=$(printf '%s' "$inspect" | grep -o '"Health":{[^}]*}' | head -n 1)
    [ -n "$health" ] && info "健康检查    : $health"
  fi

  sec "3. 容器日志末尾（找启动异常 / 源全部不可用）"
  docker logs --tail 40 "$CONTAINER" 2>&1 | sed 's/^/         /' || info "（无法读取日志）"
else
  warn "跳过容器检查（docker 不可用）"
fi

# --------------------------------------------------------------------- 4 端口
sec "4. 谁在监听对外端口 $PORT（关键：判断前面是否有 Nginx）"
# 注意：必须先把结果存进变量再判断。曾写成
#   ss -lntp | grep ... | sed ... || warn ...
# 这样的管道退出码取自**最后一个命令**（sed 恒为 0），
# 于是「端口无人监听」时告警永远不会打印 —— 是最容易骗过自己的写法。
port_line=""
if command -v ss >/dev/null 2>&1; then
  port_line=$(ss -lntp 2>/dev/null | grep -E ":${PORT}[[:space:]]" || true)
elif command -v netstat >/dev/null 2>&1; then
  port_line=$(netstat -lntp 2>/dev/null | grep -E ":${PORT}[[:space:]]" || true)
fi

if [ -n "$port_line" ]; then
  printf '%s\n' "$port_line" | sed 's/^/         /'
  case "$port_line" in
    *nginx*|*openresty*)
      bad "监听 $PORT 的是 Nginx/openresty → **前面确实有反向代理**，502 由它产生"
      info "请检查它的 proxy_pass 是否指向容器实际映射的宿主机端口（.env 的 HOST_PORT）" ;;
    *caddy*)
      bad "监听 $PORT 的是 Caddy → 前面有反向代理，检查它到后端的 upstream 配置" ;;
    *docker-proxy*)
      ok "监听 $PORT 的是 docker-proxy → Docker 直接映射，没有额外代理层（正常）" ;;
    *)
      info "监听进程见上：docker-proxy = 无代理；nginx/openresty/caddy = 有代理" ;;
  esac
else
  if command -v ss >/dev/null 2>&1 || command -v netstat >/dev/null 2>&1; then
    bad "没有任何进程监听 $PORT → 端口映射错误或容器已停止，代理会因此返回 502"
    info "请核对 .env 的 HOST_PORT 与 docker port $CONTAINER 的实际映射"
  else
    warn "系统无 ss / netstat，改用 /proc 检查（结论仅供参考）"
    if grep -qE ":$(printf '%04X' "$PORT")" /proc/net/tcp /proc/net/tcp6 2>/dev/null; then
      info "端口 $PORT 有监听（无法确定进程，安装 iproute2 后可精确判断）"
    else
      warn "端口 $PORT 似乎无人监听"
    fi
  fi
fi

# --------------------------------------------------------------------- 5 访问
sec "5. 从宿主机访问后端"
HOST_URL="http://127.0.0.1:${PORT}/api/v1/health"
if command -v curl >/dev/null 2>&1; then
  host_code=$(curl -s -o /tmp/_diag_host_body -w '%{http_code}' --max-time 12 "$HOST_URL" 2>/dev/null)
  info "GET $HOST_URL"
  info "HTTP 状态码 : ${host_code:-（无响应）}"
  info "响应体      : $(head -c 300 /tmp/_diag_host_body 2>/dev/null)"
  # 响应头里的 Server 字段是判断「是否被代理」的铁证：
  # 直达应用会显示 uvicorn（或空），被代理则显示 nginx/openresty/caddy
  server_hdr=$(curl -s -D - -o /dev/null --max-time 12 "$HOST_URL" 2>/dev/null | grep -i '^server:' | head -n 1 | tr -d '\r')
  if [ -n "$server_hdr" ]; then
    info "响应头      : $server_hdr"
    case "$server_hdr" in
      *nginx*|*openresty*|*Caddy*|*caddy*)
        bad "该响应由代理产生，不是应用本身 → 502 应到代理层排查" ;;
    esac
  fi
  rm -f /tmp/_diag_host_body
elif command -v wget >/dev/null 2>&1; then
  warn "无 curl，改用 wget"
  info "GET $HOST_URL"
  info "响应体      : $(wget -q -O - -T 12 "$HOST_URL" 2>/dev/null | head -c 300)"
else
  warn "无 curl / wget，跳过（服务器上通常都有 curl）"
fi

sec "6. 从容器内部访问后端（绕开一切代理，直连应用）"
if docker info >/dev/null 2>&1 && [ -n "${running:-}" ]; then
  # 镜像里**没有 curl**（为摆脱 Linux 软件源依赖而刻意移除），
  # 因此优先用镜像自带的 healthcheck.py 脚本；镜像较旧时才退回 curl。
  body=""
  code=""
  if docker exec "$CONTAINER" python healthcheck.py >/tmp/_diag_body 2>/dev/null; then
    code="200"
    body=$(cat /tmp/_diag_body 2>/dev/null)
  else
    code=$(docker exec "$CONTAINER" curl -s -o /dev/null -w '%{http_code}' \
           --max-time 12 "http://127.0.0.1:${APP_PORT}/api/v1/health" 2>/dev/null) || code=""
    if [ -n "$code" ]; then
      body=$(docker exec "$CONTAINER" curl -s --max-time 12 \
             "http://127.0.0.1:${APP_PORT}/api/v1/health" 2>/dev/null)
    fi
  fi
  rm -f /tmp/_diag_body
  if [ "$code" = "200" ]; then
    ok "容器内 /api/v1/health = 200 → 后端本身健康"
    info "若第 5 步失败而本步成功，则问题 100% 在「宿主机端口映射或前置代理」，不在应用。"
    info "容器内响应体：${body}"
  else
    bad "容器内 /api/v1/health 返回「${code:-无响应}」→ 后端未就绪或已崩溃"
    info "请查看第 3 步日志；常见原因：内存不足被杀（看第 7、8 步）、上游数据源全部不可达。"
  fi
else
  warn "容器未运行，跳过"
fi

sec "7. 主机资源（内存不足会导致反复重启与全站超时）"
if [ -r /proc/meminfo ]; then
  mem_total=$(awk '/^MemTotal:/{printf "%.2f", $2/1048576}' /proc/meminfo)
  mem_avail=$(awk '/^MemAvailable:/{printf "%.2f", $2/1048576}' /proc/meminfo)
  swap_total=$(awk '/^SwapTotal:/{printf "%.2f", $2/1048576}' /proc/meminfo)
  info "内存总量    : ${mem_total} GB"
  info "可用内存    : ${mem_avail} GB"
  info "Swap        : ${swap_total} GB"
  if awk -v a="$mem_avail" 'BEGIN{ if (a+0 < 0.3) exit 1 }'; then
    ok "可用内存充足"
  else
    bad "可用内存不足 300MB → 全市场扫描（约 5500 只）极易触发 OOM，建议加 swap 或把 UNIVERSE_SIZE 调小"
  fi
  if awk -v s="$swap_total" 'BEGIN{ if (s+0 < 0.5) exit 1 }'; then
    ok "已配置 swap"
  else
    warn "未配置 swap：内存峰值时会被直接杀掉而不是变慢，建议加 2GB swap"
  fi
  if docker info >/dev/null 2>&1; then
    docker stats --no-stream --format '  容器资源: {{.Name}}  CPU {{.CPUPerc}}  内存 {{.MemUsage}} ({{.MemPerc}})' 2>/dev/null | grep "$CONTAINER" || true
  fi
else
  warn "无法读取 /proc/meminfo"
fi

sec "8. 内核 OOM 记录（决定性证据）"
if command -v dmesg >/dev/null 2>&1; then
  oom_lines=$(dmesg 2>/dev/null | grep -iE 'killed process|out of memory|oom-kill' | tail -n 6)
  if [ -n "$oom_lines" ]; then
    bad "发现 OOM 杀进程记录（需确认被杀的是不是本项目）："
    printf '%s\n' "$oom_lines" | sed 's/^/         /'
    if printf '%s' "$oom_lines" | grep -qiE 'python|uvicorn|limit-up'; then
      bad "其中**包含 python/uvicorn 进程** → 就是本项目被内存不足杀掉：请加 swap 或调小 UNIVERSE_SIZE"
    else
      info "以上记录中的进程名均不是 python/uvicorn（如 java、mysql 多为其他应用）"
      info "→ 与本项目无关，但说明该主机整体内存紧张，仍需为部署预留余量"
    fi
  else
    ok "无 OOM 记录（若权限不足则结果不可信，可试 sudo dmesg | grep -i oom）"
  fi
else
  warn "无 dmesg 命令"
fi

# --------------------------------------------------------------------- 结论
sec "结论速查"
cat <<'EOF'
  把上面的结果对照下表即可定位：

  ┌────────────────────────────────────────────┬──────────────────────────────────────┐
  │ 现象                                       │ 原因与处理                            │
  ├────────────────────────────────────────────┼──────────────────────────────────────┤
  │ 步骤6=200，步骤5失败或 502                  │ 宿主机端口映射错误，或前置 Nginx／宝塔 │
  │                                            │ 反代的后端地址、端口写错 → 改代理配置  │
  │ 步骤4 显示 nginx/openresty 在监听该端口     │ 确实有代理层：502 由它发出，检查它的   │
  │                                            │ proxy_pass 是否指向 127.0.0.1:<HOST_PORT>│
  │ 步骤2 容器未运行 / 反复重启 / OOMKilled     │ 内存不足 → 加 swap、调小 UNIVERSE_SIZE │
  │ 步骤6 也不是 200                            │ 后端自身故障，看步骤3日志              │
  │ 步骤5、6 都 200，仅个别页面失败             │ 后端健康，属「重接口超时」：全市场首次  │
  │                                            │ 取数需数分钟，等预热完成后再试         │
  └────────────────────────────────────────────┴──────────────────────────────────────┘

  关键提醒：**本项目后端不会返回 502**。只要看到 502，就说明请求
  先经过了另一层服务（Nginx / 宝塔 / Caddy / 云厂商网关），
  问题在那一层到后端之间，而不是应用代码里。
EOF
