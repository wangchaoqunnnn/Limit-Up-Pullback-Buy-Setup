"""容器健康检查脚本（**不依赖 curl**）。

为什么不用 curl：
原先镜像里用 ``apt-get install curl`` 提供健康检查命令，但那一步会去访问
Debian 软件源 —— 实测在部分国内服务器上 ``apt-get update`` 单个步骤就要
**673 秒**，且 ECONNRESET / DNS 抖动都会直接让整个镜像构建失败。
而 ``python:3.12-slim`` 本身已自带 ``ca-certificates`` 与系统 tzdata，
真正缺的只有 curl。因此这里用标准库自己实现健康检查，
**把最慢、最脆弱的那一层从构建流程里彻底去掉**。

用法（镜像内、宿主机、diagnose.sh 都可用同一个脚本）：

    python /app/backend/healthcheck.py

退出码 0 = 健康；非 0 = 不健康（供 Docker HEALTHCHECK 与监控使用）。
可选环境变量：``APP_PORT``（默认 8000）、``HEALTHCHECK_TIMEOUT``（秒，默认 4）。
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    port = os.environ.get("APP_PORT", "8000").strip() or "8000"
    timeout = float(os.environ.get("HEALTHCHECK_TIMEOUT", "4") or 4)
    url = f"http://127.0.0.1:{port}/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 固定本机地址
            body = resp.read().decode("utf-8", "ignore")
            status = int(getattr(resp, "status", 0) or 0)
    except urllib.error.HTTPError as exc:
        print(f"健康检查失败：HTTP {exc.code} {url}")
        return 1
    except Exception as exc:  # noqa: BLE001 - 任何异常都视为不健康
        print(f"健康检查失败：{type(exc).__name__}: {exc}（{url}）")
        return 1

    if not (200 <= status < 300):
        print(f"健康检查失败：HTTP {status} {url}")
        return 1
    # 输出响应体便于人工排查（截断，避免刷屏）
    print(body[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
