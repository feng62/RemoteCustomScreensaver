"""多显示器防采集自定义屏保遮罩层（运行在 Tk 主线程，支持自定义图片/GIF 与多种伪装主题）。"""

from __future__ import annotations

import logging
import math
import os
import queue
import time
import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageOps, ImageSequence, ImageTk

from win_api import (
    MonitorRect,
    apply_overlay_styles,
    check_point_passes_through,
    clear_non_rude_window,
    describe_hwnd,
    enforce_topmost,
    ensure_taskbars_topmost,
    get_monitors,
    get_window_band,
    get_window_exstyle,
    get_window_rect,
    mark_non_rude_window,
    resolve_top_hwnd,
)

logger = logging.getLogger("screensaver.overlay")

THEMES = {
    "maintenance": "系统维护锁屏",
    "win_update": "Windows 更新伪装",
    "black": "极简纯黑屏",
    "image": "自定义图片",
}


class _OverlayWindow:
    """单个物理显示器对应的全屏遮罩窗口。"""

    def __init__(
        self,
        master: tk.Tk,
        monitor: MonitorRect,
        config: Dict[str, Any],
    ) -> None:
        self.monitor = monitor
        self.config = config
        self.top = tk.Toplevel(master)
        self.top.title("CustomRemoteScreensaverOverlay")
        self.top.overrideredirect(True)
        # 先以 1x1 尺寸在 Band=1 完成映射，贴上 NonRudeHWND 标记后再扩成全屏并升至 Band=2：
        # 1) 避免 UIAccess 下 Tk 跨 Band SetParent 失败导致孤立窗口吃掉点击；
        # 2) 避免 Windows Shell 在窗口首次映射为全屏时误判全屏应用而隐藏/压低任务栏！
        self.top.geometry(f"1x1{monitor.x:+d}{monitor.y:+d}")
        self.top.attributes("-alpha", 0.996)

        self._photo_refs: List[ImageTk.PhotoImage] = []
        self._after_ids: List[str] = []
        self._closed = False
        self._hwnd: int = 0

        # 渲染主题内容
        self._build_content()

        # 先让 Tk 在 Band=1 完成 1x1 窗口映射与父子绑定
        self.top.update()
        wid = self.top.winfo_id()
        try:
            frame_str = self.top.wm_frame()
            frame_hwnd = int(frame_str, 16) if frame_str else 0
        except Exception:
            frame_hwnd = 0
        root_hwnd = resolve_top_hwnd(wid)
        self._hwnd = frame_hwnd or root_hwnd

        # 关键：在扩成全屏之前先设置 NonRudeHWND，阻止 Windows Shell 隐藏/压低任务栏
        mark_non_rude_window(self._hwnd)
        if root_hwnd and root_hwnd != self._hwnd:
            mark_non_rude_window(root_hwnd)

        self.top.geometry(f"{monitor.width}x{monitor.height}{monitor.x:+d}{monitor.y:+d}")
        self.top.update()

        exclude = bool(config.get("exclude_from_capture", True))
        target_rect = (monitor.x, monitor.y, monitor.width, monitor.height)
        ok, err = apply_overlay_styles(
            self._hwnd,
            exclude_from_capture=exclude,
            rect=target_rect,
        )
        # 兜底：万一 root_hwnd 与 frame_hwnd 仍不同，对另一个句柄也应用穿透与防捕获，绝不留无穿透窗口
        if root_hwnd and root_hwnd != self._hwnd:
            logger.warning(
                "检测到 Tk 分离窗口: frame=%s root=%s，同步应用穿透样式",
                describe_hwnd(self._hwnd),
                describe_hwnd(root_hwnd),
            )
            apply_overlay_styles(root_hwnd, exclude_from_capture=exclude, rect=target_rect)

        band = get_window_band(self._hwnd)
        actual_rect = get_window_rect(self._hwnd)
        cx = actual_rect[0] + actual_rect[2] // 2
        cy = actual_rect[1] + actual_rect[3] // 2
        pass_ok, hit_hwnd = check_point_passes_through(self._hwnd, cx, cy)
        logger.info(
            "遮罩窗口就绪: 目标=(%d,%d %dx%d) 实际=(%d,%d %dx%d) self=%s affinity_ok=%s click_through=%s hit=%s",
            monitor.x,
            monitor.y,
            monitor.width,
            monitor.height,
            actual_rect[0],
            actual_rect[1],
            actual_rect[2],
            actual_rect[3],
            describe_hwnd(self._hwnd),
            ok,
            pass_ok,
            describe_hwnd(hit_hwnd),
        )
        self.top.bind(
            "<Button-1>",
            lambda e: logger.warning("遮罩窗口意外截获了鼠标左键点击: (%d,%d)", e.x_root, e.y_root),
            add="+",
        )
        if not ok:
            logger.warning(
                "显示器 (%d,%d %dx%d) SetWindowDisplayAffinity 失败 (err=%d)，请确认系统为 Win10 2004+。",
                monitor.x,
                monitor.y,
                monitor.width,
                monitor.height,
                err,
            )

        # 周期刷新 Z-Order，一旦发现有新窗口（如 UU远程隐私屏）压到上方立即重排到顶层
        refresh_ms = max(50, int(config.get("topmost_refresh_ms", 100)))
        self._schedule_topmost(refresh_ms)

    def _schedule(self, delay_ms: int, func: Callable[[], None]) -> None:
        if self._closed:
            return
        after_id = self.top.after(delay_ms, func)
        self._after_ids.append(after_id)

    def _schedule_topmost(self, refresh_ms: int) -> None:
        if self._closed:
            return
        if self._hwnd:
            enforce_topmost(self._hwnd)
        self._schedule(refresh_ms, lambda: self._schedule_topmost(refresh_ms))

    def _build_content(self) -> None:
        theme = self.config.get("theme", "maintenance")
        if theme == "image":
            image_path = str(self.config.get("image_path", "")).strip()
            if image_path and os.path.isfile(image_path):
                if self._build_image_theme(image_path):
                    return
                logger.warning("自定义图片加载失败，自动回退到系统维护主题: %s", image_path)
            else:
                logger.info("未设置有效图片路径，回退到系统维护主题。")
            self._build_maintenance_theme()
        elif theme == "win_update":
            self._build_win_update_theme()
        elif theme == "black":
            self._build_black_theme()
        else:
            self._build_maintenance_theme()

    # --- 主题 1：极简纯黑 ---
    def _build_black_theme(self) -> None:
        self.top.configure(bg="#000000")

    # --- 主题 2：系统维护锁屏 ---
    def _build_maintenance_theme(self) -> None:
        bg = "#0B0F19"
        self.top.configure(bg=bg)

        container = tk.Frame(self.top, bg=bg)
        container.place(relx=0.5, rely=0.47, anchor="center")

        # 顶部状态指示条
        accent_canvas = tk.Canvas(container, width=180, height=28, bg=bg, highlightthickness=0)
        accent_canvas.pack(pady=(0, 18))
        dot_id = accent_canvas.create_oval(14, 9, 24, 19, fill="#38BDF8", outline="")
        accent_canvas.create_text(
            102,
            14,
            text="PRIVACY SHIELD ACTIVE",
            fill="#64748B",
            font=("Consolas", 10, "bold"),
        )

        title_text = self.config.get("maintenance_title", "SYSTEM LOCKED // 业务系统维护中")
        subtitle_text = self.config.get("maintenance_subtitle", "请勿断开电源或操作键盘鼠标")

        tk.Label(
            container,
            text=title_text,
            font=("Microsoft YaHei UI", 30, "bold"),
            fg="#E2E8F0",
            bg=bg,
        ).pack(pady=(0, 12))

        tk.Label(
            container,
            text=subtitle_text,
            font=("Microsoft YaHei UI", 14),
            fg="#94A3B8",
            bg=bg,
        ).pack(pady=(0, 26))

        clock_label = tk.Label(
            container,
            text=time.strftime("%Y-%m-%d  %H:%M:%S"),
            font=("Consolas", 20),
            fg="#38BDF8",
            bg=bg,
        )
        clock_label.pack()

        pulse_state = {"bright": True}

        def _tick():
            if self._closed:
                return
            clock_label.config(text=time.strftime("%Y-%m-%d  %H:%M:%S"))
            pulse_state["bright"] = not pulse_state["bright"]
            accent_canvas.itemconfig(dot_id, fill="#38BDF8" if pulse_state["bright"] else "#0EA5E9")
            self._schedule(1000, _tick)

        self._schedule(1000, _tick)

    # --- 主题 3：Windows 正在更新伪装 ---
    def _build_win_update_theme(self) -> None:
        bg = "#006DAE"
        self.top.configure(bg=bg)

        # 用全屏背景容器显式撑满并绘制，避免多屏空窗口在 WS_EX_LAYERED 下不重绘
        bg_frame = tk.Frame(self.top, bg=bg)
        bg_frame.place(x=0, y=0, relwidth=1.0, relheight=1.0)

        container = tk.Frame(bg_frame, bg=bg)
        container.place(relx=0.5, rely=0.47, anchor="center")

        size = 76
        canvas = tk.Canvas(container, width=size, height=size, bg=bg, highlightthickness=0)
        canvas.pack(pady=(0, 26))

        progress = {"pct": 17, "angle": 0.0, "ticks": 0}
        status_label = tk.Label(
            container,
            text="正在处理更新 17%",
            font=("Microsoft YaHei UI", 22),
            fg="#FFFFFF",
            bg=bg,
        )
        status_label.pack(pady=(0, 8))

        tk.Label(
            container,
            text="请不要关闭计算机。这可能需要一段时间。",
            font=("Microsoft YaHei UI", 15),
            fg="#E6F2FA",
            bg=bg,
        ).pack()

        tk.Label(
            bg_frame,
            text="你的电脑将重启几次",
            font=("Microsoft YaHei UI", 13),
            fg="#CCE4F4",
            bg=bg,
        ).place(relx=0.5, rely=0.90, anchor="center")

        num_dots = 5
        radius = 24
        cx, cy = size / 2, size / 2
        dot_ids = [
            canvas.create_oval(0, 0, 0, 0, fill="#FFFFFF", outline="")
            for _ in range(num_dots)
        ]

        def _animate():
            if self._closed:
                return
            base = progress["angle"]
            for i, did in enumerate(dot_ids):
                # 模拟 Windows 圆点先快后慢的追逐节奏
                phase = (base - i * 0.34) % (2 * math.pi)
                eased = phase + 0.35 * math.sin(phase)
                x = cx + radius * math.cos(eased - math.pi / 2)
                y = cy + radius * math.sin(eased - math.pi / 2)
                r = 3.6
                canvas.coords(did, x - r, y - r, x + r, y + r)

            progress["angle"] = (base + 0.14) % (2 * math.pi)
            progress["ticks"] += 1
            # 约每 18 秒缓慢推进 1%，到 99% 后停住
            if progress["ticks"] % 360 == 0 and progress["pct"] < 99:
                progress["pct"] += 1
                status_label.config(text=f"正在处理更新 {progress['pct']}%")

            self._schedule(50, _animate)

        _animate()

    # --- 主题 4：自定义图片 / 动图 GIF ---
    def _fit_image(self, img: Image.Image, mode: str) -> Image.Image:
        tw, th = self.monitor.width, self.monitor.height
        rgba = img.convert("RGBA")
        if mode == "stretch":
            fitted = rgba.resize((tw, th), Image.Resampling.LANCZOS)
        elif mode == "contain":
            contained = ImageOps.contain(rgba, (tw, th), method=Image.Resampling.LANCZOS)
            fitted = Image.new("RGBA", (tw, th), (0, 0, 0, 255))
            ox = (tw - contained.width) // 2
            oy = (th - contained.height) // 2
            fitted.paste(contained, (ox, oy), contained)
        else:
            # 默认 cover：等比放大并居中裁切填满
            fitted = ImageOps.fit(rgba, (tw, th), method=Image.Resampling.LANCZOS)

        # 合成到不透明黑底 RGB，避免 RGBA PhotoImage 在 WS_EX_LAYERED 窗口下出现透明/黑屏异常
        rgb_bg = Image.new("RGB", (tw, th), (0, 0, 0))
        rgb_bg.paste(fitted, (0, 0), fitted)
        return rgb_bg

    def _build_image_theme(self, image_path: str) -> bool:
        mode = str(self.config.get("image_mode", "cover")).lower()
        self.top.configure(bg="#000000")

        try:
            with Image.open(image_path) as im:
                is_animated = getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1
                if not is_animated:
                    rendered = self._fit_image(im, mode)
                    photo = ImageTk.PhotoImage(rendered, master=self.top)
                    self._photo_refs.append(photo)
                    label = tk.Label(self.top, image=photo, bg="#000000", borderwidth=0, highlightthickness=0)
                    label.image = photo  # type: ignore[attr-defined]
                    label.place(x=0, y=0, width=self.monitor.width, height=self.monitor.height)
                    return True

                # 动图 GIF（限制最多 120 帧避免超高内存占用）
                frames: List[Tuple[ImageTk.PhotoImage, int]] = []
                for idx, frame in enumerate(ImageSequence.Iterator(im)):
                    if idx >= 120:
                        break
                    delay = int(frame.info.get("duration", 80) or 80)
                    delay = max(30, min(delay, 2000))
                    rendered = self._fit_image(frame, mode)
                    photo = ImageTk.PhotoImage(rendered, master=self.top)
                    self._photo_refs.append(photo)
                    frames.append((photo, delay))

                if not frames:
                    return False

                label = tk.Label(self.top, image=frames[0][0], bg="#000000", borderwidth=0, highlightthickness=0)
                label.image = frames[0][0]  # type: ignore[attr-defined]
                label.place(x=0, y=0, width=self.monitor.width, height=self.monitor.height)

                state = {"idx": 0}

                def _next_frame():
                    if self._closed:
                        return
                    state["idx"] = (state["idx"] + 1) % len(frames)
                    photo_obj, delay_ms = frames[state["idx"]]
                    label.config(image=photo_obj)
                    self._schedule(delay_ms, _next_frame)

                self._schedule(frames[0][1], _next_frame)
                return True
        except Exception as exc:
            logger.exception("加载图片异常: %s", exc)
            return False

    def destroy(self) -> None:
        if self._closed:
            return
        self._closed = True
        for aid in self._after_ids:
            try:
                self.top.after_cancel(aid)
            except Exception:
                pass
        self._after_ids.clear()
        if self._hwnd:
            clear_non_rude_window(self._hwnd)
        try:
            self.top.destroy()
        except Exception:
            pass
        self._photo_refs.clear()


class OverlayManager:
    """运行在主线程的 Tk 事件循环管理器，通过线程安全队列接收外部控制指令。"""

    def __init__(
        self,
        config: Dict[str, Any],
        on_state_change: Optional[Callable[[bool], None]] = None,
        on_config_updated: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.config = config
        self._on_state_change = on_state_change
        self._on_config_updated = on_config_updated

        self._cmd_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._windows: List[_OverlayWindow] = []
        self._active_monitors: List[MonitorRect] = []
        self._visible = False
        self._running = False
        self._monitor_check_tick = 0

        self.root = tk.Tk()
        self.root.withdraw()

    @property
    def visible(self) -> bool:
        return self._visible

    # --- 线程安全的外部调用接口 ---

    def request_show(self) -> None:
        self._cmd_queue.put(("show", None))

    def request_hide(self) -> None:
        self._cmd_queue.put(("hide", None))

    def request_toggle(self) -> None:
        self._cmd_queue.put(("toggle", None))

    def request_set_theme(self, theme: str) -> None:
        self._cmd_queue.put(("set_theme", theme))

    def request_set_image_mode(self, mode: str) -> None:
        self._cmd_queue.put(("set_image_mode", mode))

    def request_pick_image(self) -> None:
        self._cmd_queue.put(("pick_image", None))

    def request_reload(self, new_config: Dict[str, Any]) -> None:
        self._cmd_queue.put(("reload", new_config))

    def request_quit(self) -> None:
        self._cmd_queue.put(("quit", None))

    # --- 主线程执行逻辑 ---

    def run_forever(self) -> None:
        self._running = True
        self._poll_queue()
        self.root.mainloop()

    def _poll_queue(self) -> None:
        if not self._running:
            return
        try:
            while True:
                cmd, payload = self._cmd_queue.get_nowait()
                self._handle_command(cmd, payload)
        except queue.Empty:
            pass
        if self._running:
            if self._visible:
                self._monitor_check_tick += 1
                if self._monitor_check_tick >= 20:  # 每 1 秒检查一次显示器拓扑是否变化
                    self._monitor_check_tick = 0
                    all_monitors = bool(self.config.get("cover_all_monitors", True))
                    current_monitors = get_monitors(all_monitors=all_monitors)
                    if current_monitors != self._active_monitors:
                        logger.info("检测到显示器拓扑变化，自动重建遮罩层...")
                        self._rebuild()
            self.root.after(50, self._poll_queue)

    def _handle_command(self, cmd: str, payload: Any) -> None:
        if cmd == "show":
            self._show()
        elif cmd == "hide":
            self._hide()
        elif cmd == "toggle":
            if self._visible:
                self._hide()
            else:
                self._show()
        elif cmd == "set_theme":
            self.config["theme"] = str(payload)
            self._notify_config_updated()
            if self._visible:
                self._rebuild()
        elif cmd == "set_image_mode":
            self.config["image_mode"] = str(payload)
            self._notify_config_updated()
            if self._visible and self.config.get("theme") == "image":
                self._rebuild()
        elif cmd == "pick_image":
            self._pick_image_dialog()
        elif cmd == "reload":
            self.config.update(payload)
            if self._visible:
                self._rebuild()
        elif cmd == "quit":
            self._hide()
            self._running = False
            try:
                self.root.quit()
                self.root.destroy()
            except Exception:
                pass

    def _notify_state_change(self) -> None:
        if self._on_state_change:
            try:
                self._on_state_change(self._visible)
            except Exception:
                pass

    def _notify_config_updated(self) -> None:
        if self._on_config_updated:
            try:
                self._on_config_updated(self.config)
            except Exception:
                pass

    def _show(self) -> None:
        if self._visible:
            return
        all_monitors = bool(self.config.get("cover_all_monitors", True))
        monitors = get_monitors(all_monitors=all_monitors)
        self._active_monitors = list(monitors)
        logger.info("开启自定义屏保，覆盖 %d 块显示器，当前主题=%s", len(monitors), self.config.get("theme"))
        self._windows = [_OverlayWindow(self.root, m, self.config) for m in monitors]
        taskbars = ensure_taskbars_topmost()
        if taskbars:
            logger.info("已保活任务栏置顶状态 (Band=1): %s", ", ".join(taskbars))
        # 300ms 后再保活一次，防止 explorer.exe 异步全屏检测或 UU远程隐私屏延迟压低任务栏
        self.root.after(300, lambda: ensure_taskbars_topmost() if self._visible else None)
        self._visible = True
        self._notify_state_change()

    def _hide(self) -> None:
        if not self._visible and not self._windows:
            return
        logger.info("关闭自定义屏保")
        for w in self._windows:
            w.destroy()
        self._windows.clear()
        self._visible = False
        self._notify_state_change()

    def _rebuild(self) -> None:
        self._hide()
        self._show()

    def _pick_image_dialog(self) -> None:
        was_visible = self._visible
        if was_visible:
            self._hide()

        initial_dir = os.path.dirname(self.config.get("image_path", "")) or os.path.expanduser("~")
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择自定义屏保图片或 GIF",
            initialdir=initial_dir if os.path.isdir(initial_dir) else os.path.expanduser("~"),
            filetypes=[
                ("图片与动图", "*.png;*.jpg;*.jpeg;*.bmp;*.webp;*.gif"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self.config["image_path"] = path
            self.config["theme"] = "image"
            self._notify_config_updated()
            logger.info("已选择自定义图片: %s", path)
            self._show()
        elif was_visible:
            self._show()
