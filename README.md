# 防远控采集自定义隐私屏保

基于 Windows 10 (2004+) / Windows 11 原生 `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` API 实现的自定义隐私屏保：

- **物理显示器上**：全屏置顶显示自定义画面（系统维护、Windows 更新伪装、纯黑屏、或自定义图片/GIF），覆盖全部物理显示器。
- **远控端（UU远程 / ToDesk / 向日葵等）**：操作系统底层自动将该遮罩层从录屏/捕获流中剔除，控制端看到的仍是清晰、可正常点击操作的真实桌面。

## 目录结构

```text
├── src/                  # 核心 Python 源码（app.py / overlay.py / detector.py / win_api.py）
├── scripts/              # 日常启动、调试与诊断脚本
├── build_config/         # PyInstaller 打包配置、UIAccess 清单与签名部署脚本
├── config.json           # 默认配置文件
├── requirements.txt      # Python 依赖列表
└── README.md
```

## 安装与打包

```bash
pip install -r requirements.txt
pyinstaller --noconfirm --clean build_config/CustomScreensaver.spec
```

## 快速开始

- **UIAccess (Band=2) 部署（可覆盖 UU远程 隐私屏）**：先完成上方打包，再右键以管理员身份运行 `scripts\一键部署UIAccess(管理员).bat`
- **日常使用（无黑框，常驻右下角系统托盘）**：双击 `scripts\启动屏保(无黑框).vbs`
- **查看实时日志调试**：双击 `scripts\调试运行.bat`
- **核对 UU远程 连接特征**：双击 `scripts\诊断远控检测.bat`

## 默认快捷键（全局有效，不抢焦点）

| 快捷键 | 功能 |
| --- | --- |
| `Ctrl + Alt + S` | 一键开启 / 关闭自定义屏保 |
| `Ctrl + Alt + Q` | 退出托盘程序 |

可在 `config.json` 中修改 `hotkey_toggle` 和 `hotkey_exit`。

## 托盘右键功能

- **开启/关闭屏保**（左键双击托盘图标也可直接切换）
- **主题切换**：
  - `系统维护锁屏`（暗色科技风 + 实时时钟，文案可在 `config.json` 自定义）
  - `Windows 更新伪装`（经典蓝色转圈进度动画）
  - `极简纯黑屏`
  - `自定义图片`（支持 PNG / JPG / WebP / BMP 及动态 GIF）
- **选择自定义图片 / GIF...**：直接弹窗选图并自动写入 `config.json`
- **图片缩放模式**：等比填满 (`cover`) / 完整居中 (`contain`) / 拉伸填满 (`stretch`)
- **自动检测 UU远程 / 远控连接**：默认关闭，勾选后自动监听 `MuMuRemoteBackend.exe` 等远控传输状态，远控接入自动开启、断开超过 5 秒自动关闭；期间若手动关闭则不会强行反复弹出。

## 配合 UU远程 使用建议

如果 UU远程 开启了自带的“隐私屏”，它可能使用驱动级或高权限遮罩盖住普通置顶窗口。建议**关闭 UU远程 自带的隐私屏**，完全由本脚本接管物理屏遮罩，既不会被远控端看到，又能自由展示任意自定义画面。
