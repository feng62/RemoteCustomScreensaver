"""远控连接状态检测器（基于实测 UU远程 GameViewerServer 指纹优化，兼容 RDP 及常见第三方远控）。"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Set

import psutil

from win_api import is_rdp_session, is_window_class_visible

UU_SERVER_PROCESSES = {
    "gameviewerserver.exe",
    "mumuremotebackend.exe",
    "mumuremoteservice.exe",
    "uuremote.exe",
}


def _is_loopback_or_any(ip: str) -> bool:
    if not ip:
        return True
    return ip.startswith("127.") or ip in ("::1", "0.0.0.0", "::")


def _is_loopback(ip: str) -> bool:
    if not ip:
        return True
    return ip.startswith("127.") or ip == "::1"


@dataclass
class DetectionResult:
    active: bool
    reason: str
    details: List[str]


class RemoteSessionDetector:
    """
    通过系统级连接表 (psutil.net_connections) 检测远控推流状态：
    - 无需管理员权限即可读取高权限服务（GameViewerServer.exe / MuMuRemoteBackend.exe）的套接字状态。
    - 针对 UU远程 (GameViewerServer.exe) 实测指纹：
        待机基线：至多 1 个 0.0.0.0 UDP 待机端口 + 1 条 443 心跳 TCP 连接。
        远控活跃：出现 >=2 个 UDP 端口（或绑定具体网卡 IP 的 WebRTC/ICE UDP 端口），
                  或出现非 80/443 端口的外部 ESTABLISHED TCP，或 >=3 条外部 ESTABLISHED TCP。
    - 针对其他第三方远控：要求至少 2 条外部 ESTABLISHED TCP 或非回环 UDP 推流端口。
    """

    def __init__(self, remote_processes: Iterable[str], mode: str = "privacy_window") -> None:
        self.remote_processes: Set[str] = {p.lower() for p in remote_processes}
        self.mode: str = str(mode or "privacy_window").strip().lower()

    def update_processes(self, remote_processes: Iterable[str]) -> None:
        self.remote_processes = {p.lower() for p in remote_processes}

    def update_mode(self, mode: str) -> None:
        self.mode = str(mode or "privacy_window").strip().lower()

    def _find_target_pids(self) -> Dict[int, str]:
        pid_map: Dict[int, str] = {}
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name in self.remote_processes:
                    pid_map[int(proc.info["pid"])] = proc.info["name"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return pid_map

    def check(self) -> DetectionResult:
        if is_window_class_visible("GameViewerPrivateWindowClass"):
            return DetectionResult(
                True,
                "检测到 UU远程 隐私屏窗口活跃 (GameViewerPrivateWindowClass)",
                ["WindowClass=GameViewerPrivateWindowClass"],
            )

        if self.mode == "privacy_window":
            return DetectionResult(False, "UU远程 隐私屏未开启", [])

        if is_rdp_session():
            return DetectionResult(True, "Windows RDP 会话活跃", ["SM_REMOTESESSION != 0"])

        pid_map = self._find_target_pids()
        if not pid_map:
            return DetectionResult(False, "未发现远控进程", [])

        try:
            all_conns = psutil.net_connections(kind="inet")
        except Exception as exc:
            return DetectionResult(False, f"读取网络连接失败: {exc}", [])

        # 按进程归类连接特征
        uu_stats: Dict[str, Dict[str, List[str]]] = {}
        other_hits: Dict[str, List[str]] = {}

        for conn in all_conns:
            pid = conn.pid
            if pid not in pid_map:
                continue

            proc_name = pid_map[pid]
            proc_lower = proc_name.lower()

            if proc_lower in UU_SERVER_PROCESSES:
                stat = uu_stats.setdefault(
                    proc_name,
                    {"udp_all": [], "udp_nic": [], "tcp_ext": [], "tcp_data": []},
                )
                if conn.type == socket.SOCK_DGRAM:
                    lip = conn.laddr.ip if conn.laddr else ""
                    lport = conn.laddr.port if conn.laddr else 0
                    if lport >= 1024 and not _is_loopback(lip):
                        desc = f"UDP {lip}:{lport}"
                        stat["udp_all"].append(desc)
                        if not _is_loopback_or_any(lip):
                            stat["udp_nic"].append(desc)
                elif conn.type == socket.SOCK_STREAM and conn.status == psutil.CONN_ESTABLISHED:
                    if conn.raddr and not _is_loopback_or_any(conn.raddr.ip):
                        desc = f"TCP {conn.laddr.ip}:{conn.laddr.port} -> {conn.raddr.ip}:{conn.raddr.port}"
                        stat["tcp_ext"].append(desc)
                        if conn.raddr.port not in (80, 443):
                            stat["tcp_data"].append(desc)
            else:
                if conn.type == socket.SOCK_STREAM and conn.status == psutil.CONN_ESTABLISHED:
                    if conn.raddr and not _is_loopback_or_any(conn.raddr.ip):
                        other_hits.setdefault(proc_name, []).append(
                            f"TCP {conn.laddr.ip}:{conn.laddr.port} -> {conn.raddr.ip}:{conn.raddr.port}"
                        )
                elif conn.type == socket.SOCK_DGRAM:
                    if conn.raddr and not _is_loopback_or_any(conn.raddr.ip):
                        other_hits.setdefault(proc_name, []).append(
                            f"UDP -> {conn.raddr.ip}:{conn.raddr.port}"
                        )

        # 1) 优先判定 UU远程 服务端特征
        for proc_name, stat in uu_stats.items():
            if stat["udp_nic"]:
                return DetectionResult(
                    True,
                    f"检测到 UU远程 ({proc_name}) 活跃推流（网卡 UDP={len(stat['udp_nic'])}，TCP={len(stat['tcp_ext'])}）",
                    (stat["udp_nic"] + stat["tcp_data"] + stat["tcp_ext"])[:8],
                )
            if len(stat["udp_all"]) >= 2:
                return DetectionResult(
                    True,
                    f"检测到 UU远程 ({proc_name}) 活跃推流（UDP={len(stat['udp_all'])}）",
                    stat["udp_all"][:8],
                )
            # 注意：不再按 443 HTTPS 外部 TCP 数量判定，因为 UU远程 断开后 443 长连接会残留数十秒才进入 CLOSE_WAIT
            if stat["tcp_data"]:
                return DetectionResult(
                    True,
                    f"检测到 UU远程 ({proc_name}) 活跃数据通道（非443 TCP={len(stat['tcp_data'])}）",
                    stat["tcp_data"][:8],
                )

        # 2) 其他第三方远控软件
        for proc_name, hits in other_hits.items():
            if len(hits) >= 2:
                return DetectionResult(
                    True,
                    f"检测到 {proc_name} 活跃远控传输 ({len(hits)} 条)",
                    hits[:8],
                )

        return DetectionResult(False, "远控进程待机中", [])


def run_diagnostics(remote_processes: Iterable[str], duration_sec: int = 12) -> None:
    """命令行诊断工具：打印当前远控进程与全部网络套接字，便于核对连接/空闲特征。"""
    detector = RemoteSessionDetector(remote_processes, mode="remote_session")
    print(f"=== 远控状态实时诊断 (持续 {duration_sec} 秒，按 Ctrl+C 结束) ===")
    start = time.time()
    try:
        while time.time() - start < duration_sec:
            pid_map = detector._find_target_pids()
            res = detector.check()
            ts = time.strftime("%H:%M:%S")
            print(f"\n[{ts}] 判定: {'[活跃]' if res.active else '[待机]'} - {res.reason}")
            print(f"  匹配进程: {pid_map if pid_map else '无'}")
            try:
                conns = [c for c in psutil.net_connections(kind="inet") if c.pid in pid_map]
                for c in conns:
                    proto = "TCP" if c.type == socket.SOCK_STREAM else "UDP"
                    st = c.status if c.status else "-"
                    laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
                    raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
                    print(f"    PID={c.pid} ({pid_map[c.pid]}) {proto} {st} {laddr} -> {raddr}")
            except Exception as exc:
                print(f"    读取连接失败: {exc}")
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    print("\n=== 诊断结束 ===")
