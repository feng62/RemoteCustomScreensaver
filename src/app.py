"""防远控采集自定义屏保 — 系统托盘常驻主程序。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pystray
from PIL import Image, ImageDraw

from detector import RemoteSessionDetector, run_diagnostics
from overlay import THEMES, OverlayManager
from win_api import (
    GlobalHotkeyManager,
    acquire_single_instance_mutex,
    enable_dpi_awareness,
    has_uiaccess,
    inspect_topmost_windows,
    is_admin,
    lock_workstation,
)

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CustomRemoteScreensaver"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = APP_DIR

BASE_DIR = DATA_DIR
CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "screensaver.log"

DEFAULT_CONFIG: Dict[str, Any] = {
    "theme": "win_update",
    "image_path": "",
    "image_mode": "cover",
    "maintenance_title": "SYSTEM LOCKED // 业务系统维护中",
    "maintenance_subtitle": "请勿断开电源或操作键盘鼠标",
    "hotkey_toggle": "ctrl+alt+s",
    "hotkey_exit": "ctrl+alt+q",
    "auto_detect": True,
    "auto_detect_mode": "privacy_window",
    "lock_on_end": False,
    "remote_processes": [
        "GameViewerServer.exe",
        "MuMuRemoteBackend.exe",
        "MuMuRemoteService.exe",
        "UURemote.exe",
        "ToDesk_Service.exe",
        "ToDesk.exe",
        "SunloginClient.exe",
        "OrayRemoteService.exe",
        "rustdesk.exe",
        "AnyDesk.exe",
    ],
    "detect_interval_sec": 0.5,
    "auto_hide_delay_sec": 1.0,
    "topmost_refresh_ms": 100,
    "cover_all_monitors": True,
    "exclude_from_capture": True,
}

STARTUP_SHORTCUT_NAME = "CustomRemoteScreensaver.lnk"
CREATE_NO_WINDOW = 0x08000000


def _get_startup_shortcut_path() -> Path:
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_SHORTCUT_NAME
    return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_SHORTCUT_NAME


def is_startup_enabled() -> bool:
    return _get_startup_shortcut_path().is_file()


def set_startup_enabled(enable: bool) -> bool:
    lnk_path = _get_startup_shortcut_path()
    try:
        if not enable:
            if lnk_path.is_file():
                lnk_path.unlink()
            return True

        installed_exe = Path(r"C:\Program Files\CustomScreensaver\CustomScreensaver.exe")
        if getattr(sys, "frozen", False):
            target = Path(sys.executable).resolve()
            args = ""
            workdir = target.parent
        elif installed_exe.is_file():
            target = installed_exe
            args = ""
            workdir = installed_exe.parent
        else:
            target = Path(sys.executable).resolve()
            args = f'"{Path(__file__).resolve()}"'
            workdir = APP_DIR

        lnk_path.parent.mkdir(parents=True, exist_ok=True)
        ps_script = (
            "$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{str(lnk_path).replace(chr(39), chr(39)*2)}'); "
            f"$s.TargetPath = '{str(target).replace(chr(39), chr(39)*2)}'; "
            f"$s.Arguments = '{args.replace(chr(39), chr(39)*2)}'; "
            f"$s.WorkingDirectory = '{str(workdir).replace(chr(39), chr(39)*2)}'; "
            "$s.Description = '防远控采集自定义隐私屏保'; "
            "$s.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
            check=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=10,
        )
        return lnk_path.is_file()
    except Exception as exc:
        logging.getLogger("screensaver.app").warning("设置开机自启动失败: %s", exc)
        return False


def setup_logging(console: bool = False) -> None:
    handlers: list[logging.Handler] = [
        logging.FileHandler(LOG_PATH, encoding="utf-8", mode="a"),
    ]
    if console and sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def load_config() -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    seed_cfg = APP_DIR / "config.json"
    if not CONFIG_PATH.is_file() and seed_cfg.is_file() and seed_cfg != CONFIG_PATH:
        try:
            CONFIG_PATH.write_text(seed_cfg.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    if CONFIG_PATH.is_file():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            if isinstance(user_cfg, dict):
                cfg.update(user_cfg)
        except Exception as exc:
            logging.getLogger("screensaver").warning("读取 config.json 失败，使用默认配置: %s", exc)
    else:
        save_config(cfg)
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logging.getLogger("screensaver").warning("保存 config.json 失败: %s", exc)


def create_tray_icon_image(active: bool) -> Image.Image:
    """绘制 64x64 托盘图标：待机为深蓝显示器，屏保开启时带亮青盾牌高亮。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 显示器外框
    frame_color = (56, 189, 248, 255) if active else (148, 163, 184, 255)
    screen_fill = (14, 116, 144, 255) if active else (15, 23, 42, 255)
    draw.rounded_rectangle((6, 10, 58, 46), radius=6, fill=screen_fill, outline=frame_color, width=4)
    # 底座
    draw.rectangle((26, 46, 38, 52), fill=frame_color)
    draw.rounded_rectangle((18, 51, 46, 56), radius=2, fill=frame_color)

    # 中心状态指示
    if active:
        draw.ellipse((24, 20, 40, 36), fill=(250, 204, 21, 255))
    else:
        draw.ellipse((27, 23, 37, 33), fill=(56, 189, 248, 255))

    return img


class ScreensaverApp:
    def __init__(self, config: Dict[str, Any], start_visible: bool = False) -> None:
        self.logger = logging.getLogger("screensaver.app")
        self.config = config
        self.start_visible = start_visible

        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._manual_suppress_auto = False
        self._auto_shown = False
        self._session_active = False

        self.detector = RemoteSessionDetector(
            config.get("remote_processes", []),
            mode=str(config.get("auto_detect_mode", "privacy_window")),
        )
        self.hotkeys = GlobalHotkeyManager()
        self.tray_icon: Optional[pystray.Icon] = None

        self.overlay = OverlayManager(
            config=self.config,
            on_state_change=self._on_overlay_state_change,
            on_config_updated=self._on_config_updated,
        )

    # --- 回调与状态同步 ---

    def _on_overlay_state_change(self, visible: bool) -> None:
        if self.tray_icon is not None:
            try:
                self.tray_icon.icon = create_tray_icon_image(visible)
                self.tray_icon.title = (
                    "自定义隐私屏保 [已开启]" if visible else "自定义隐私屏保 [待机中]"
                )
                self.tray_icon.update_menu()
            except Exception:
                pass

    def _on_config_updated(self, cfg: Dict[str, Any]) -> None:
        save_config(cfg)
        self.detector.update_processes(cfg.get("remote_processes", []))
        self.detector.update_mode(str(cfg.get("auto_detect_mode", "privacy_window")))
        if self.tray_icon is not None:
            try:
                self.tray_icon.update_menu()
            except Exception:
                pass

    # --- 用户手动操作 ---

    def manual_toggle(self) -> None:
        with self._state_lock:
            if self.overlay.visible:
                # 只要当前处于检测到的活跃会话中，用户手动关闭后在本次会话结束前不再自动弹出
                if self._auto_shown or self._session_active:
                    self._manual_suppress_auto = True
                self._auto_shown = False
                self.overlay.request_hide()
            else:
                self._manual_suppress_auto = False
                self._auto_shown = False
                self.overlay.request_show()

    def toggle_auto_detect(self) -> None:
        new_val = not bool(self.config.get("auto_detect", False))
        self.config["auto_detect"] = new_val
        with self._state_lock:
            self._manual_suppress_auto = False
            if not new_val:
                self._session_active = False
        save_config(self.config)
        self.logger.info("自动检测远控状态已%s", "开启" if new_val else "关闭")
        if self.tray_icon is not None:
            self.tray_icon.update_menu()

    def set_auto_detect_mode(self, mode: str) -> None:
        self.config["auto_detect_mode"] = mode
        self.detector.update_mode(mode)
        with self._state_lock:
            self._manual_suppress_auto = False
        save_config(self.config)
        self.logger.info("自动检测模式已切换为: %s", mode)
        if self.tray_icon is not None:
            self.tray_icon.update_menu()

    def toggle_lock_on_end(self) -> None:
        new_val = not bool(self.config.get("lock_on_end", False))
        self.config["lock_on_end"] = new_val
        save_config(self.config)
        self.logger.info("结束后自动锁屏已%s", "开启" if new_val else "关闭")
        if self.tray_icon is not None:
            self.tray_icon.update_menu()

    def set_auto_hide_delay(self, seconds: float) -> None:
        self.config["auto_hide_delay_sec"] = float(seconds)
        save_config(self.config)
        self.logger.info("结束后关闭延迟已设为: %.1f 秒", float(seconds))
        if self.tray_icon is not None:
            self.tray_icon.update_menu()

    def toggle_cover_all_monitors(self) -> None:
        new_val = not bool(self.config.get("cover_all_monitors", True))
        self.config["cover_all_monitors"] = new_val
        save_config(self.config)
        self.logger.info("覆盖全部显示器已%s", "开启" if new_val else "关闭")
        self.overlay.request_reload({"cover_all_monitors": new_val})
        if self.tray_icon is not None:
            self.tray_icon.update_menu()

    def toggle_startup(self) -> None:
        target = not is_startup_enabled()
        ok = set_startup_enabled(target)
        self.logger.info("开机自动启动%s: ok=%s", "开启" if target else "关闭", ok)
        if self.tray_icon is not None:
            self.tray_icon.update_menu()

    def open_config_folder(self) -> None:
        try:
            subprocess.Popen(["explorer", str(BASE_DIR)])
        except Exception as exc:
            self.logger.warning("打开目录失败: %s", exc)

    def quit_app(self) -> None:
        self.logger.info("收到退出指令，正在清理资源...")
        self._stop_event.set()
        self.hotkeys.stop()
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.overlay.request_quit()

    # --- 自动检测后台线程 ---

    def _auto_detect_loop(self) -> None:
        idle_since_ts = 0.0
        while not self._stop_event.is_set():
            interval = max(0.3, float(self.config.get("detect_interval_sec", 0.5)))
            if not self.config.get("auto_detect", False):
                idle_since_ts = 0.0
                self._stop_event.wait(interval)
                continue

            res = self.detector.check()
            now = time.monotonic()

            if res.active:
                idle_since_ts = 0.0
                with self._state_lock:
                    self._session_active = True
                    if not self.overlay.visible and not self._manual_suppress_auto:
                        self.logger.info("自动检测触发开启: %s", res.reason)
                        self._auto_shown = True
                        self.overlay.request_show()
            else:
                should_lock = False
                hide_delay = max(0.0, float(self.config.get("auto_hide_delay_sec", 1.0)))
                with self._state_lock:
                    was_active = self._session_active
                if was_active:
                    if idle_since_ts <= 0.0:
                        idle_since_ts = now
                    if (now - idle_since_ts) >= hide_delay:
                        with self._state_lock:
                            self._session_active = False
                            self._manual_suppress_auto = False
                            if self._auto_shown and self.overlay.visible:
                                self.logger.info(
                                    "检测到远控/隐私屏已结束（持续 %.1f 秒），自动关闭自定义屏保",
                                    now - idle_since_ts,
                                )
                                self._auto_shown = False
                                self.overlay.request_hide()
                            should_lock = bool(self.config.get("lock_on_end", False))
                        idle_since_ts = 0.0
                        if should_lock:
                            # 稍等遮罩销毁后再触发系统锁屏
                            if not self._stop_event.wait(0.25):
                                locked = lock_workstation()
                                self.logger.info("结束后自动锁屏 (LockWorkStation): ok=%s", locked)
                else:
                    idle_since_ts = 0.0

            self._stop_event.wait(interval)

    # --- 系统托盘菜单 ---

    def _build_tray_menu(self) -> pystray.Menu:
        hotkey_toggle = self.config.get("hotkey_toggle", "ctrl+alt+s").upper()
        hotkey_exit = self.config.get("hotkey_exit", "ctrl+alt+q").upper()

        def _make_theme_action(theme_key: str):
            return lambda _icon, _item: self.overlay.request_set_theme(theme_key)

        def _make_theme_checked(theme_key: str):
            return lambda _item: self.config.get("theme") == theme_key

        def _make_mode_action(mode_key: str):
            return lambda _icon, _item: self.overlay.request_set_image_mode(mode_key)

        def _make_mode_checked(mode_key: str):
            return lambda _item: self.config.get("image_mode", "cover") == mode_key

        def _make_detect_mode_action(mode_key: str):
            return lambda _icon, _item: self.set_auto_detect_mode(mode_key)

        def _make_detect_mode_checked(mode_key: str):
            return lambda _item: str(self.config.get("auto_detect_mode", "privacy_window")) == mode_key

        def _make_delay_action(sec: float):
            return lambda _icon, _item: self.set_auto_hide_delay(sec)

        def _make_delay_checked(sec: float):
            return lambda _item: abs(float(self.config.get("auto_hide_delay_sec", 1.0)) - sec) < 0.01

        theme_items = [
            pystray.MenuItem(
                label,
                _make_theme_action(key),
                checked=_make_theme_checked(key),
                radio=True,
            )
            for key, label in THEMES.items()
        ]

        mode_labels = [
            ("cover", "等比填满 (cover)"),
            ("contain", "完整居中 (contain)"),
            ("stretch", "拉伸填满 (stretch)"),
        ]
        mode_items = [
            pystray.MenuItem(
                label,
                _make_mode_action(key),
                checked=_make_mode_checked(key),
                radio=True,
            )
            for key, label in mode_labels
        ]

        detect_mode_items = [
            pystray.MenuItem(
                "跟随 UU远程 隐私屏（推荐，退出秒关）",
                _make_detect_mode_action("privacy_window"),
                checked=_make_detect_mode_checked("privacy_window"),
                radio=True,
            ),
            pystray.MenuItem(
                "任意远控连接时自动开启（无需开自带隐私屏）",
                _make_detect_mode_action("remote_session"),
                checked=_make_detect_mode_checked("remote_session"),
                radio=True,
            ),
        ]

        delay_items = [
            pystray.MenuItem(
                label,
                _make_delay_action(sec),
                checked=_make_delay_checked(sec),
                radio=True,
            )
            for sec, label in [
                (0.0, "立即关闭 (0 秒)"),
                (1.0, "延迟 1 秒"),
                (3.0, "延迟 3 秒"),
                (5.0, "延迟 5 秒"),
            ]
        ]

        return pystray.Menu(
            pystray.MenuItem(
                lambda _item: (
                    f"关闭屏保 ({hotkey_toggle})"
                    if self.overlay.visible
                    else f"开启屏保 ({hotkey_toggle})"
                ),
                lambda _icon, _item: self.manual_toggle(),
                default=True,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("主题切换", pystray.Menu(*theme_items)),
            pystray.MenuItem(
                "选择自定义图片 / GIF...",
                lambda _icon, _item: self.overlay.request_pick_image(),
            ),
            pystray.MenuItem("图片缩放模式", pystray.Menu(*mode_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "自动检测开关",
                lambda _icon, _item: self.toggle_auto_detect(),
                checked=lambda _item: bool(self.config.get("auto_detect", False)),
            ),
            pystray.MenuItem("自动触发模式", pystray.Menu(*detect_mode_items)),
            pystray.MenuItem(
                "结束后自动锁屏 (Win+L)",
                lambda _icon, _item: self.toggle_lock_on_end(),
                checked=lambda _item: bool(self.config.get("lock_on_end", False)),
            ),
            pystray.MenuItem("结束后关闭延迟", pystray.Menu(*delay_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "覆盖全部显示器",
                lambda _icon, _item: self.toggle_cover_all_monitors(),
                checked=lambda _item: bool(self.config.get("cover_all_monitors", True)),
            ),
            pystray.MenuItem(
                "开机自动启动",
                lambda _icon, _item: self.toggle_startup(),
                checked=lambda _item: is_startup_enabled(),
            ),
            pystray.MenuItem(
                "打开配置与日志目录",
                lambda _icon, _item: self.open_config_folder(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                f"退出 ({hotkey_exit})",
                lambda _icon, _item: self.quit_app(),
            ),
        )

    def _start_tray(self) -> None:
        self.tray_icon = pystray.Icon(
            name="CustomRemoteScreensaver",
            icon=create_tray_icon_image(False),
            title="自定义隐私屏保 [待机中]",
            menu=self._build_tray_menu(),
        )
        threading.Thread(target=self.tray_icon.run, name="TrayThread", daemon=True).start()

    def _start_hotkeys(self) -> None:
        hk_toggle = str(self.config.get("hotkey_toggle", "ctrl+alt+s")).strip()
        hk_exit = str(self.config.get("hotkey_exit", "ctrl+alt+q")).strip()
        try:
            if hk_toggle:
                self.hotkeys.register(hk_toggle, self.manual_toggle)
            if hk_exit:
                self.hotkeys.register(hk_exit, self.quit_app)
            self.hotkeys.start()
            if self.hotkeys.failed_hotkeys:
                self.logger.warning("以下全局热键注册失败（可能与其他软件冲突）: %s", self.hotkeys.failed_hotkeys)
            else:
                self.logger.info("全局热键已注册: 开关=%s, 退出=%s", hk_toggle, hk_exit)
        except Exception as exc:
            self.logger.warning("注册全局热键异常: %s", exc)

    def run(self) -> None:
        self.logger.info(
            "自定义隐私屏保启动，目录=%s，管理员权限=%s，UIAccess=%s",
            BASE_DIR,
            is_admin(),
            has_uiaccess(),
        )
        self._start_hotkeys()
        self._start_tray()
        threading.Thread(target=self._auto_detect_loop, name="AutoDetectThread", daemon=True).start()

        if self.start_visible:
            self.overlay.request_show()

        self.overlay.run_forever()


def run_affinity_self_test() -> int:
    """快速验证本机是否支持 SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)。"""
    import tkinter as tk
    from win_api import apply_overlay_styles, resolve_top_hwnd

    enable_dpi_awareness()
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry("200x100+0+0")
    root.update_idletasks()
    hwnd = resolve_top_hwnd(root.winfo_id())
    ok, err = apply_overlay_styles(hwnd, exclude_from_capture=True)
    root.destroy()
    if ok:
        print(f"[OK] SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) 验证通过 (hwnd={hwnd})")
        return 0
    print(f"[FAIL] SetWindowDisplayAffinity 失败 (hwnd={hwnd}, err={err})")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="防远控采集自定义屏保（支持 UU远程 与自定义图片/伪装主题）")
    parser.add_argument("--show", action="store_true", help="启动后立即开启屏保")
    parser.add_argument("--diagnose", action="store_true", help="运行远控进程与网络连接诊断")
    parser.add_argument("--inspect-zorder", action="store_true", help="查看当前置顶窗口与 UU远程/微信输入法 的 Z-Band 层级")
    parser.add_argument("--test-affinity", action="store_true", help="测试本机防捕获 API 可用性")
    parser.add_argument("--console", action="store_true", help="同时输出日志到控制台")
    args = parser.parse_args()

    enable_dpi_awareness()

    if args.test_affinity:
        return run_affinity_self_test()

    if args.inspect_zorder:
        inspect_topmost_windows()
        return 0

    cfg = load_config()
    if args.diagnose:
        run_diagnostics(cfg.get("remote_processes", []), duration_sec=12)
        return 0

    setup_logging(console=args.console or (sys.stdout is not None and sys.stdout.isatty()))

    mutex_handle, already_running = acquire_single_instance_mutex()
    if already_running:
        logging.getLogger("screensaver").info("程序已在后台运行，请查看右下角系统托盘图标。")
        return 0

    try:
        app = ScreensaverApp(cfg, start_visible=args.show)
        app.run()
    finally:
        # 保持 mutex_handle 引用直至退出
        _ = mutex_handle
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
