# DBAide 打包与分发

DBAide 支持三种分发方式，按场景选择：

| 方式 | 适用 | macOS | Windows | Ubuntu |
|------|------|-------|---------|--------|
| **pip / wheel** | 开发者、服务器 CLI | ✅ | ✅ | ✅ |
| **PyInstaller GUI** | 桌面用户，免装 Python | ✅ | ✅ | ✅ |
| **PyInstaller CLI** | 单文件命令行工具 | ✅ | ✅ | ✅ |

> **原则**：PyInstaller 必须在**目标操作系统上**分别构建（不能交叉编译）。CI 用 GitHub Actions 在三平台并行打包容器。

---

## 前置条件

- Python **3.11+**
- 构建 GUI 包需安装 PyQt6

```bash
# 推荐：requirements 文件（与 pyproject.toml 同步）
pip install -r requirements-gui.txt      # CLI + GUI
pip install -r requirements-dev.txt      # + 测试与 PyInstaller

# 或 editable 安装
pip install -e ".[gui,dev]"
```

---

## 方式 1：Python 包（wheel / pip install）

最轻量，适合 CLI 和已有 Python 环境的用户。

```bash
# 生成分发包
pip install build
python -m build --outdir dist

# 安装（本机或目标机器）
pip install dist/dbaide-*.whl

# 或带 GUI
pip install "dist/dbaide-*.whl[gui]"
```

安装后：

```bash
dbaide --version          # CLI
dbaide-gui                # 桌面（需 [gui]）
python -m dbaide.gui      # 同上
```

**Ubuntu 服务器示例**

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv
python3 -m venv ~/.dbaide-venv
source ~/.dbaide-venv/bin/activate
pip install dbaide  # 或本地 wheel
dbaide connect add ...
```

---

## 方式 2：PyInstaller 独立桌面包（推荐给最终用户）

### macOS

```bash
chmod +x scripts/build_package.sh
./scripts/build_package.sh gui
```

产物：`dist/DBAide/DBAide`（.app 风格目录集合）

```bash
# 可选：打 DMG
hdiutil create -volname DBAide -srcfolder dist/DBAide -ov -format UDZO dist/DBAide-macOS.dmg
```

首次打开若被 Gatekeeper 拦截：系统设置 → 隐私与安全性 → 仍要打开。  
对外发布需 **代码签名 + 公证**（Apple Developer 账号），见 [Apple 文档](https://developer.apple.com/documentation/security/notarizing_macos_software_before_distribution)。

### Windows

```powershell
.\scripts\build_package.ps1 gui
```

产物：`dist\DBAide\DBAide.exe`

```powershell
Compress-Archive -Path dist\DBAide -DestinationPath dist\DBAide-Windows.zip
```

可选：用 [Inno Setup](https://jrsoftware.org/isinfo.php) 或 WiX 做 `.msi` 安装向导。

### Ubuntu / Linux

```bash
./scripts/build_package.sh gui
```

产物：`dist/DBAide/DBAide`

```bash
cd dist && tar -czf DBAide-linux-$(uname -m).tar.gz DBAide
```

用户解压后运行 `./DBAide/DBAide`。  
若缺系统库（如 `libxcb`），安装：

```bash
sudo apt install -y libxcb-cursor0 libxkbcommon-x11-0 libgl1
```

可选进阶： [AppImage](https://appimage.org/) 或 `fpm` 打 `.deb`（需额外脚本，当前仓库未内置）。

---

## 方式 3：PyInstaller 单文件 CLI

不含 GUI，体积更小：

```bash
# macOS / Linux
./scripts/build_package.sh cli

# Windows
.\scripts\build_package.ps1 cli
```

产物：`dist/dbaide`（或 `dist/dbaide.exe`）

---

## 一键脚本速查

| 平台 | GUI | CLI | Wheel |
|------|-----|-----|-------|
| macOS / Linux | `./scripts/build_package.sh gui` | `./scripts/build_package.sh cli` | `./scripts/build_package.sh wheel` |
| Windows | `.\scripts\build_package.ps1 gui` | `.\scripts\build_package.ps1 cli` | `.\scripts\build_package.ps1 wheel` |

Spec 文件位置：

```text
packaging/pyinstaller/dbaide-gui.spec
packaging/pyinstaller/dbaide-cli.spec
```

---

## GitHub Actions 自动构建

推送 tag `v*` 或手动触发 **Release packages** workflow：

```bash
git tag v0.1.0
git push origin v0.1.0
```

Workflow 在 macOS (arm64/x86_64)、Ubuntu、Windows 上分别运行 PyInstaller，上传：

- `DBAide-macOS-arm64.tar.gz`
- `DBAide-macOS-x86_64.tar.gz`
- `DBAide-Linux-x86_64.tar.gz`
- `DBAide-Windows.zip`

在 Actions → Artifacts 下载即可。

---

## 配置与数据目录

打包后用户数据仍在标准路径，与 pip 安装一致：

```text
~/.dbaide/config.toml          # 连接与模型配置
~/.dbaide/assets/              # 离线 schema assets
~/.dbaide/joins/               # Join 目录
~/.dbaide/sessions/            # 聊天会话记录
~/.dbaide/query_history/       # SQL 编辑器历史
~/.dbaide/logs/                # 应用日志与 SQL 审计日志
```

升级安装包时**无需迁移**这些目录。

---

## 常见问题

**Q: 能否在一台 Mac 上打出 Windows 包？**  
A: 不能。PyInstaller 需在各自 OS 上构建（或用 CI matrix）。

**Q: GUI 包很大？**  
A: PyQt6 + Python 运行时通常 150–250MB，正常。CLI 单文件约 30–50MB。

**Q: MySQL/PostgreSQL 驱动是否打进包？**  
A: spec 已包含 `pymysql`、`psycopg_binary`。SQLite 用标准库。

**Q: 如何减小体积？**  
A: 使用 CLI spec；GUI 可尝试 `upx=True`（部分平台不稳定，默认关闭）。

**Q: 与 pip 安装共存？**  
A: 可以。独立包不依赖系统 Python，配置目录相同。

---

## 自动构建与发布（GitHub Actions）

`.github/workflows/release.yml` 在 **macOS / Linux / Windows** 上分别用 PyInstaller
构建桌面包,产物如下:

| 平台 | 产物 |
| --- | --- |
| macOS (Apple Silicon) | `DBAide-macOS-arm64.dmg`(拖入 Applications 安装) |
| Linux (x86_64) | `DBAide-Linux-x86_64.tar.gz` |
| Windows (x86_64) | `DBAide-Windows-x86_64.msi`(安装向导) |

**手动构建(不发布)** — 在 Actions 页面点 *Run workflow*(`workflow_dispatch`),
产物作为 workflow artifacts 提供下载。

**正式发布** — 打一个 `v*` 标签即可,CI 构建完四个平台后自动创建对应的 GitHub Release
并附上全部安装包(release notes 自动生成):

```bash
# 先在 pyproject.toml 里更新 version,提交,然后:
git tag v0.1.0
git push origin v0.1.0
```

`v0.1.0-rc1` 这类带连字符的标签会被标记为 pre-release。发布用的是 GitHub 自动注入的
`GITHUB_TOKEN`,无需额外配置 secret。

> 提示:PyInstaller 不能交叉编译,所以必须在各目标 OS 上分别构建 —— 这正是用
> Actions 矩阵的原因。macOS 包未做签名/公证,首次打开需在「系统设置 → 隐私与安全性」放行。

---

## 相关文件

- [pyproject.toml](../pyproject.toml) — 依赖与 entry points
- [docs/DESIGN.md](DESIGN.md) — 架构设计
- [.github/workflows/release.yml](../.github/workflows/release.yml) — CI 打包
