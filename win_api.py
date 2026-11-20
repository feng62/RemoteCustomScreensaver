"""Windows 底层 API 封装：DPI 感知、多显示器枚举、防捕获窗口属性、全局热键、单实例锁。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shcore = getattr(ctypes.windll, "shcore", None)

# --- Display Affinity ---
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011

# --- Window Styles ---
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

LWA_ALPHA = 0x00000002

# --- SetWindowPos ---
HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
GW_HWNDPREV = 3

# --- GetAncestor ---
GA_ROOT = 2

# --- System Metrics ---
SM_REMOTESESSION = 0x1000

# --- Hotkey Modifiers ---
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

# --- DPI Awareness ---
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

# --- Pointer-safe GetWindowLong / SetWindowLong ---
if ctypes.sizeof(ctypes.c_void_p) == 8:
    _GetWindowLongPtr = user32.GetWindowLongPtrW
    _SetWindowLongPtr = user32.SetWindowLongPtrW
    _LONG_PTR = ctypes.c_longlong
else:
    _GetWindowLongPtr = user32.GetWindowLongW
    _SetWindowLongPtr = user32.SetWindowLongW
    _LONG_PTR = ctypes.c_long

_GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWindowLongPtr.restype = _LONG_PTR
_SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, _LONG_PTR]
_SetWindowLongPtr.restype = _LONG_PTR

user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL

user32.SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND,
    wintypes.COLORREF,
    wintypes.BYTE,
    wintypes.DWORD,
]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL

user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL

user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND

user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumChildWindows.argtypes = [wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL

user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND

user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND

user32.LockWorkStation.argtypes = []
user32.LockWorkStation.restype = wintypes.BOOL

user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
user32.SetPropW.restype = wintypes.BOOL

user32.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.RemovePropW.restype = wintypes.HANDLE

kernel32.GetCurrentProcessId.restype = wintypes.DWORD

_GetWindowBand = getattr(user32, "GetWindowBand", None)
if _GetWindowBand is not None:
    _GetWindowBand.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _GetWindowBand.restype = wintypes.BOOL

kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GetLastError.restype = wintypes.DWORD

ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183


@dataclass(frozen=True)
class MonitorRect:
    x: int
    y: int
    width: int
    height: int
    is_primary: bool


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


MONITORINFOF_PRIMARY = 1


def enable_dpi_awareness() -> None:
    """开启 Per-Monitor V2 高分屏 DPI 感知，确保多屏与缩放环境下全屏坐标精确。"""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return
    except Exception:
        pass

    try:
        if shcore is not None:
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            if shcore.SetProcessDpiAwareness(2) == 0:
                return
    except Exception:
        pass

    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def get_monitors(all_monitors: bool = True) -> List[MonitorRect]:
    """枚举当前所有物理显示器的像素坐标区域。"""
    monitors: List[MonitorRect] = []

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def _callback(hmonitor, _hdc, _lprect, _lparam):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append(
                MonitorRect(
                    x=r.left,
                    y=r.top,
                    width=r.right - r.left,
                    height=r.bottom - r.top,
                    is_primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
        return True

    user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_callback), 0)

    if not monitors:
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        monitors.append(MonitorRect(0, 0, w, h, True))

    if not all_monitors:
        primary = [m for m in monitors if m.is_primary]
        return primary[:1] if primary else monitors[:1]

    # 主屏排在最前
    monitors.sort(key=lambda m: (not m.is_primary, m.x, m.y))
    return monitors


def resolve_top_hwnd(widget_hwnd: int) -> int:
    """获取 Tk 窗口对应的真正顶层 Win32 HWND。"""
    root_hwnd = user32.GetAncestor(widget_hwnd, GA_ROOT)
    return int(root_hwnd) if root_hwnd else int(widget_hwnd)


def get_window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """返回窗口实际的 Win32 物理像素坐标 (x, y, width, height)。"""
    rc = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return int(rc.left), int(rc.top), int(rc.right - rc.left), int(rc.bottom - rc.top)
    return 0, 0, 0, 0


def get_window_exstyle(hwnd: int) -> int:
    """返回窗口当前的 GWL_EXSTYLE。"""
    return int(_GetWindowLongPtr(hwnd, GWL_EXSTYLE))


def _mark_children_transparent(hwnd: int) -> None:
    """为所有子窗口补充 WS_EX_TRANSPARENT，避免子控件拦截鼠标命中测试。"""
    def _cb(child_hwnd: int, _lp: int) -> bool:
        try:
            ex = _GetWindowLongPtr(child_hwnd, GWL_EXSTYLE)
            if not (ex & WS_EX_TRANSPARENT):
                _SetWindowLongPtr(child_hwnd, GWL_EXSTYLE, ex | WS_EX_TRANSPARENT)
        except Exception:
            pass
        return True

    user32.EnumChildWindows(hwnd, _WNDENUMPROC(_cb), 0)


def describe_hwnd(hwnd: int) -> str:
    """返回指定窗口的类名、进程名与 Band 信息，便于日志诊断。"""
    if not hwnd:
        return "None"
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    cls = buf.value
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    pname = f"PID:{pid.value}"
    try:
        import psutil
        pname = psutil.Process(int(pid.value)).name()
    except Exception:
        pass
    band = get_window_band(hwnd)
    ex = get_window_exstyle(hwnd)
    return f"{hex(hwnd)}({pname}/{cls}/Band={band}/ex={hex(ex)})"


def check_point_passes_through(hwnd: int, x: int, y: int) -> Tuple[bool, int]:
    """检查屏幕坐标 (x, y) 的命中窗口是否已穿透 hwnd（返回 (是否穿透, 命中的顶层hwnd)）。"""
    hit = user32.WindowFromPoint(wintypes.POINT(x, y))
    hit_root = resolve_top_hwnd(hit) if hit else 0
    return (hit_root != hwnd), hit_root


def mark_non_rude_window(hwnd: int) -> bool:
    """
    设置 Win32 NonRudeHWND 窗口属性，通知 Windows Shell (explorer.exe)
    不要将此全屏遮罩窗口当作全屏游戏/视频，避免系统自动隐藏或压低任务栏 (Shell_TrayWnd)。
    """
    if not hwnd:
        return False
    try:
        return bool(user32.SetPropW(hwnd, "NonRudeHWND", wintypes.HANDLE(1)))
    except Exception:
        return False


def clear_non_rude_window(hwnd: int) -> None:
    if not hwnd:
        return
    try:
        user32.RemovePropW(hwnd, "NonRudeHWND")
    except Exception:
        pass


def ensure_taskbars_topmost() -> List[str]:
    """
    确保主副屏任务栏 (Shell_TrayWnd / Shell_SecondaryTrayWnd) 在 Band=1 中保持置顶可见：
    - 物理显示器上：本脚本遮罩位于 Band=2，依然完全盖住任务栏；
    - 远控画面中：遮罩被 WDA_EXCLUDEFROMCAPTURE 剔除，任务栏不会因全屏检测被压到窗口后面而消失。
    """
    found: List[Tuple[str, int, bool, Tuple[int, int, int, int]]] = []
    seen = set()
    for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        hwnd = user32.FindWindowExW(None, None, cls, None)
        steps = 0
        while hwnd and steps < 16:
            if hwnd not in seen:
                seen.add(hwnd)
                vis = bool(user32.IsWindowVisible(hwnd))
                rc = get_window_rect(hwnd)
                found.append((cls, int(hwnd), vis, rc))
            hwnd = user32.FindWindowExW(None, hwnd, cls, None)
            steps += 1

    info_list: List[str] = []
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    for cls, hwnd, vis, rc in found:
        if vis:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        info_list.append(f"{cls}({hex(hwnd)},vis={vis},rect={rc})")
    return info_list


def apply_overlay_styles(
    hwnd: int,
    exclude_from_capture: bool = True,
    rect: Optional[Tuple[int, int, int, int]] = None,
) -> Tuple[bool, int]:
    """
    为遮罩窗口设置：
    - NonRudeHWND 属性：防止 Windows Shell 触发全屏模式把任务栏隐藏/压低
    - WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
      （仅对顶层 HWND 设置 WS_EX_TRANSPARENT，切勿给非分层子控件设 WS_EX_TRANSPARENT，否则会导致图片控件重绘被黑色背景擦除！）
    - 使用 LWA_ALPHA=254（视觉与 255 完全一致，避免部分 Win11/DWM 优化路径忽略分层穿透）
    - 通过 SWP_FRAMECHANGED 立即刷新扩展样式缓存（确保鼠标点击完全穿透）
    - 若传入 rect=(x, y, w, h)，直接用 Win32 SetWindowPos 精确放置到目标物理显示器（兼容负坐标多屏）
    - SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)：物理屏可见，远控采集完全不可见
    """
    mark_non_rude_window(hwnd)
    ex_style = _GetWindowLongPtr(hwnd, GWL_EXSTYLE)
    new_style = (
        ex_style
        | WS_EX_LAYERED
        | WS_EX_TRANSPARENT
        | WS_EX_NOACTIVATE
        | WS_EX_TOPMOST
        | WS_EX_TOOLWINDOW
    )
    _SetWindowLongPtr(hwnd, GWL_EXSTYLE, new_style)
    user32.SetLayeredWindowAttributes(hwnd, 0, 254, LWA_ALPHA)

    affinity = WDA_EXCLUDEFROMCAPTURE if exclude_from_capture else WDA_NONE
    ctypes.set_last_error(0)
    ok = bool(user32.SetWindowDisplayAffinity(hwnd, affinity))
    err = ctypes.get_last_error()

    if rect is not None:
        x, y, w, h = rect
        flags = SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, flags)
    else:
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
    return ok, err


def has_visible_window_above(hwnd: int) -> bool:
    """检查当前同 Z-Band 中是否存在排在 hwnd 上方的其他进程可见窗口。"""
    my_pid = int(kernel32.GetCurrentProcessId())
    my_band = get_window_band(hwnd)
    prev = user32.GetWindow(hwnd, GW_HWNDPREV)
    steps = 0
    while prev and steps < 64:
        if user32.IsWindowVisible(prev):
            # 忽略更高 Band 的系统窗口，避免每 100ms 无意义地反复调用 SetWindowPos
            if my_band > 0:
                prev_band = get_window_band(prev)
                if prev_band > 0 and prev_band != my_band:
                    prev = user32.GetWindow(prev, GW_HWNDPREV)
                    steps += 1
                    continue
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(prev, ctypes.byref(pid))
            if int(pid.value) != my_pid:
                return True
        prev = user32.GetWindow(prev, GW_HWNDPREV)
        steps += 1
    return False


def is_window_class_visible(class_name: str) -> bool:
    """检查指定窗口类名是否存在任意可见顶层窗口（如多屏下的 GameViewerPrivateWindowClass）。"""
    hwnd = user32.FindWindowExW(None, None, class_name, None)
    steps = 0
    while hwnd and steps < 32:
        if user32.IsWindowVisible(hwnd):
            return True
        hwnd = user32.FindWindowExW(None, hwnd, class_name, None)
        steps += 1
    return False


def lock_workstation() -> bool:
    """锁定当前 Windows 会话（等同于按下 Win+L）。"""
    try:
        return bool(user32.LockWorkStation())
    except Exception:
        return False


def enforce_topmost(hwnd: int, force: bool = False) -> None:
    """将窗口强制保持在 Z-Order 最顶层，同时不抢占键盘焦点并保持鼠标穿透样式。"""
    required_ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
    cur_ex = _GetWindowLongPtr(hwnd, GWL_EXSTYLE)
    style_changed = (cur_ex & required_ex) != required_ex
    if style_changed:
        _SetWindowLongPtr(hwnd, GWL_EXSTYLE, cur_ex | required_ex | WS_EX_TOPMOST)
        user32.SetLayeredWindowAttributes(hwnd, 0, 254, LWA_ALPHA)

    if not force and not style_changed and not has_visible_window_above(hwnd):
        return
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
    if style_changed:
        flags |= SWP_FRAMECHANGED
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)


def get_window_band(hwnd: int) -> int:
    """获取窗口的 Windows DWM Z-Order Band（1=DESKTOP, 2=UIACCESS 等）。"""
    if _GetWindowBand is None:
        return -1
    band = wintypes.DWORD(0)
    if _GetWindowBand(hwnd, ctypes.byref(band)):
        return int(band.value)
    return -1


def is_admin() -> bool:
    """判断当前进程是否具有管理员权限。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def has_uiaccess() -> bool:
    """判断当前进程令牌是否已获得 Windows UIAccess 权限（决定能否进入 Band=2）。"""
    try:
        advapi32 = ctypes.windll.advapi32
        TOKEN_QUERY = 0x0008
        TokenUIAccess = 26
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL

        hproc = kernel32.GetCurrentProcess()
        htok = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(hproc, TOKEN_QUERY, ctypes.byref(htok)):
            return False
        try:
            val = wintypes.DWORD(0)
            ret_len = wintypes.DWORD(0)
            if advapi32.GetTokenInformation(
                htok, TokenUIAccess, ctypes.byref(val), ctypes.sizeof(val), ctypes.byref(ret_len)
            ):
                return bool(val.value)
            return False
        finally:
            kernel32.CloseHandle(htok)
    except Exception:
        return False


def inspect_topmost_windows() -> None:
    """打印当前交互桌面中所有置顶窗口、高 Band 窗口及 UU远程/微信输入法窗口的层级信息。"""
    import psutil
    import win32gui
    import win32process

    band_names = {
        0: "DEFAULT",
        1: "DESKTOP",
        2: "UIACCESS",
        3: "IMMERSIVE_IHM",
        4: "IMMERSIVE_NOTIFICATION",
        5: "IMMERSIVE_APPCHROME",
        16: "SYSTEM_TOOLS",
        17: "LOCK",
        18: "ABOVELOCK_UX",
    }

    print(f"当前是否管理员权限: {is_admin()}")
    print("按 Z-Order 从高到低列出置顶 / 高 Band / UU远程 / 微信输入法 窗口：\n")

    rows = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        ex = win32gui.GetWindowLong(hwnd, GWL_EXSTYLE)
        band = get_window_band(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            pname = psutil.Process(pid).name()
        except Exception:
            pname = f"PID:{pid}"
        is_topmost = bool(ex & WS_EX_TOPMOST)
        interesting = is_topmost or band > 1 or any(
            k in pname.lower() for k in ("gameviewer", "mumu", "wetype", "wechat", "python")
        )
        if interesting:
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            w, h = r - l, b - t
            if w > 0 and h > 0:
                bname = band_names.get(band, str(band))
                rows.append((band, bname, is_topmost, pname, cls, title, f"{w}x{h}@{l},{t}", hex(ex)))
        return True

    win32gui.EnumWindows(_cb, None)
    for idx, (band, bname, is_top, pname, cls, title, geom, ex_hex) in enumerate(rows, 1):
        print(
            f"{idx:2d}. [Band={band}({bname}) Topmost={is_top}] "
            f"{pname} | cls={cls!r} | title={title!r} | {geom} | ex={ex_hex}"
        )
    if not rows:
        print("未检测到符合条件的可见窗口。")


def is_rdp_session() -> bool:
    """判断当前是否处于 Windows 原生远程桌面 (RDP) 会话。"""
    return bool(user32.GetSystemMetrics(SM_REMOTESESSION))


def acquire_single_instance_mutex(name: str = "Local\\CustomRemoteScreensaverMutex_v1"):
    """创建命名互斥体防止程序重复启动；返回 (handle, already_running)。"""
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, name)
    err = kernel32.GetLastError()
    already = (err == ERROR_ALREADY_EXISTS) or (not handle and err == ERROR_ACCESS_DENIED)
    return handle, already


# --- 全局热键支持 ---

_MOD_MAP = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
}

_VK_MAP: Dict[str, int] = {
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
    **{str(n): ord(str(n)) for n in range(10)},
    **{f"F{i}": 0x70 + (i - 1) for i in range(1, 13)},
    "SPACE": 0x20,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "PAUSE": 0x13,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "HOME": 0x24,
    "END": 0x23,
}


def parse_hotkey(spec: str) -> Tuple[int, int]:
    """解析类似 'ctrl+alt+s' 的热键配置为 (modifiers, vk)。"""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"无效热键配置: {spec!r}")

    mods = MOD_NOREPEAT
    vk: Optional[int] = None
    for part in parts:
        if part in _MOD_MAP:
            mods |= _MOD_MAP[part]
        else:
            key = part.upper()
            if key not in _VK_MAP:
                raise ValueError(f"不支持的热键按键: {part!r} (来自 {spec!r})")
            vk = _VK_MAP[key]

    if vk is None:
        raise ValueError(f"热键缺少主键: {spec!r}")
    return mods, vk


class GlobalHotkeyManager:
    """使用 Win32 RegisterHotKey 的后台全局热键监听器（不受窗口穿透和焦点影响）。"""

    def __init__(self) -> None:
        self._bindings: Dict[int, Tuple[str, int, int, Callable[[], None]]] = {}
        self._next_id = 1
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._ready = threading.Event()
        self.failed_hotkeys: List[str] = []

    def register(self, spec: str, callback: Callable[[], None]) -> None:
        mods, vk = parse_hotkey(spec)
        hotkey_id = self._next_id
        self._next_id += 1
        self._bindings[hotkey_id] = (spec, mods, vk, callback)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="GlobalHotkeyThread", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)

    def stop(self) -> None:
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        registered_ids: List[int] = []

        for hk_id, (spec, mods, vk, _cb) in self._bindings.items():
            if user32.RegisterHotKey(None, hk_id, mods, vk):
                registered_ids.append(hk_id)
            else:
                self.failed_hotkeys.append(spec)

        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                hk_id = int(msg.wParam)
                binding = self._bindings.get(hk_id)
                if binding is not None:
                    try:
                        binding[3]()
                    except Exception:
                        pass

        for hk_id in registered_ids:
            user32.UnregisterHotKey(None, hk_id)
