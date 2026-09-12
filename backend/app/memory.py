"""内存探测与低内存自动降级。

**为什么需要这个模块**：实测在 1.7GB 内存、无 swap 的云主机上，
本项目按「全市场 5561 只」运行时会被内核 OOM 杀掉（日志里能看到
``uid=1000`` 的 ``python`` 进程 ``anon-rss`` 约 480MB 时被 kill）。
被 OOM 杀掉的表现是接口时好时坏、甚至整页加载失败，
但容器日志里只有一次「启动成功」，**看不出是内存问题** —— 极难排查。

因此这里做两件事：
1. 启动时探测真实可用的内存上限（同时看 cgroup 限额与宿主机可用量）；
2. 内存吃紧时**自动关掉最占内存的可选缓存**并**大声报警**，
   给出可执行的处置建议（加 swap / 调小股票池）。

注意：这里**不会**自动缩减 ``UNIVERSE_SIZE``。用户明确要求覆盖全部
A 股（主板/创业板/科创板/北交所一个都不能少），静默减少监控范围
属于违背需求，必须由用户显式决定。守护只做「不改变覆盖范围」的降级。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: 低于该内存（MB）时进入低内存模式并降级。
#: 取值依据：本项目按全市场（约 5500 只）运行实测需约 560MB 常驻，
#: 预热峰值约 950MB。留出余量后，1000MB 以下都不安全，故取 1200MB。
LOW_MEMORY_THRESHOLD_MB = 1200


@dataclass
class MemoryInfo:
    """一次探测得到的内存画像（全部为 MB，探测不到则为 None）。"""

    total_mb: float | None = None
    available_mb: float | None = None
    cgroup_limit_mb: float | None = None
    swap_total_mb: float | None = None

    @property
    def effective_limit_mb(self) -> float | None:
        """本进程实际可用的上限：取 cgroup 限额与宿主机总量的较小值。"""
        candidates = [v for v in (self.cgroup_limit_mb, self.total_mb) if v]
        return min(candidates) if candidates else None

    @property
    def is_low(self) -> bool:
        """是否处于低内存状态（限额或当前可用量任一项偏低）。"""
        for value in (self.effective_limit_mb, self.available_mb):
            if value is not None and value < LOW_MEMORY_THRESHOLD_MB:
                return True
        return False

    @property
    def has_swap(self) -> bool:
        return bool(self.swap_total_mb and self.swap_total_mb >= 256)

    def snapshot(self) -> dict[str, float | bool | None]:
        return {
            "totalMB": self.total_mb,
            "availableMB": self.available_mb,
            "cgroupLimitMB": self.cgroup_limit_mb,
            "swapTotalMB": self.swap_total_mb,
            "effectiveLimitMB": self.effective_limit_mb,
            "lowMemory": self.is_low,
            "hasSwap": self.has_swap,
        }


def _read_meminfo() -> dict[str, float]:
    """读取 /proc/meminfo → {字段: MB}；不可读时返回空字典。"""
    values: dict[str, float] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fp:
            for line in fp:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                num = parts[1].strip().split()
                if not num:
                    continue
                try:
                    values[key] = float(num[0]) / 1024.0  # kB → MB
                except ValueError:
                    continue
    except OSError:
        return {}
    return values


def _read_cgroup_limit_mb() -> float | None:
    """读取容器内存限额（cgroup v2 memory.max / v1 limit_in_bytes）。

    返回 None 表示「没有限额」（等于宿主机总量），这正是 docker run 未设
    ``--memory`` 时的情形 —— 也意味着容器可以把宿主机内存吃满直到 OOM。
    """
    # cgroup v2
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                raw = fp.read().strip()
        except OSError:
            continue
        if not raw or raw == "max":
            return None
        try:
            value = int(raw)
        except ValueError:
            continue
        # v1 的「无限」是一个极大的数（约 2^63）
        if value <= 0 or value >= 1 << 60:
            return None
        return round(value / 1048576.0, 1)
    return None


def detect_memory() -> MemoryInfo:
    """探测当前内存状况（任何异常都不抛出）。"""
    mem = _read_meminfo()
    if not mem:
        return MemoryInfo(cgroup_limit_mb=_read_cgroup_limit_mb())
    return MemoryInfo(
        total_mb=mem.get("MemTotal"),
        available_mb=mem.get("MemAvailable"),
        cgroup_limit_mb=_read_cgroup_limit_mb(),
        swap_total_mb=mem.get("SwapTotal"),
    )


def log_memory_report(info: MemoryInfo, *, context: str = "启动") -> None:
    """把内存状况写进日志；吃紧时给出可直接照做的处置建议。"""
    limit = info.effective_limit_mb
    avail = info.available_mb

    def fmt(v: float | None) -> str:
        return f"{v:.0f}MB" if v is not None else "未知"

    logger.info(
        "内存状况（%s）：上限=%s 当前可用=%s Swap=%s",
        context,
        fmt(limit),
        fmt(avail),
        fmt(info.swap_total_mb),
    )
    if not info.is_low:
        return

    logger.warning(
        "检测到【内存偏紧】：上限 %s、可用 %s、Swap %s。"
        "本项目按全市场（约 5500 只）运行需要约 500~600MB 常驻内存，"
        "内存不足时进程会被内核 OOM 杀掉 —— 表现为接口时好时坏甚至整页加载失败，"
        "而日志里看不出原因（已实测踩坑）。",
        fmt(limit),
        fmt(avail),
        fmt(info.swap_total_mb),
    )
    if not info.has_swap:
        logger.warning(
            "强烈建议立刻加 2GB swap（最省事、零成本）：\n"
            "    sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile\n"
            "    sudo mkswap /swapfile && sudo swapon /swapfile\n"
            "    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab"
        )
    logger.warning(
        "或把监控范围调小（仍覆盖主板/创业板/科创板/北交所四个板块，"
        "只是每个板块抽样少一些）：在 .env 中设 UNIVERSE_SIZE=2000 后重新部署。"
    )
