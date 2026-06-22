"""Compose the promo screenshots into a narrated demo video (MP4 + GIF).

This stitches the curated capability screenshots in ``docs/images/promo/`` into a
single walkthrough: an intro card, one captioned scene per screenshot (in the
narrative order from ``copy.md``), and an outro card. No live UI driving — it is a
deterministic compositor over the existing frames, so it reproduces identically.

Usage:
    ./.venv/bin/python tools/make_demo_video.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
PROMO = ROOT / "docs" / "images" / "promo"
OUT_MP4 = ROOT / "docs" / "images" / "demo.mp4"
OUT_GIF = ROOT / "docs" / "images" / "demo.gif"
OUT_POSTER = ROOT / "docs" / "images" / "demo-poster.png"

W, H = 1920, 1200
BG = (11, 13, 17)
PANEL = (18, 21, 27)
BORDER = (38, 44, 54)
ACCENT = (88, 150, 255)
TEXT = (236, 240, 245)
MUTED = (140, 150, 164)

FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size)


# (file stem, short section title, full caption) — narrative order from copy.md.
SCENES: list[tuple[str, str, str]] = [
    ("01-assets-initializing", "资产初始化",
     "资产初始化不再是黑盒：左侧结构树边构建边更新，进度明确到已构建表数与当前表。"),
    ("02-runtime-thinking", "运行时可观测",
     "复杂问题运行时可见：意图拆解、结构发现、关联校验、SQL 生成与风险检查都能追踪。"),
    ("17-agent-trace", "Agent Trace 时间线",
     "Trace 不是树状噪音，而是右侧时间线抽屉：步骤、耗时和详情分层查看。"),
    ("03-chart-answer-analysis", "图表化业务回答",
     "业务问题直接给出结构化结论：摘要、关键发现、趋势折线与双轴组合图同屏可读。"),
    ("04-chart-answer-breakdown", "丰富图表类型",
     "回答连续展示多种图表：堆叠面积、柱状、环形、漏斗、仪表盘、热力图与库存风险条。"),
    ("19-showcase-bridge-radar", "雷达图 · 多维对比",
     "渠道综合质量雷达：ROI、复购、履约、客单价、NPS、低退款六维横向对比。"),
    ("20-showcase-bars", "瀑布图 · 净收入桥",
     "净收入桥：从 GMV 一步步扣到净收入；分组柱状看各渠道分月走势。"),
    ("21-showcase-trends", "多轴 · 堆叠 · 饼图",
     "多轴趋势、堆叠构成、面积与饼图——常用分析图表一应俱全，自动按问题匹配。"),
    ("05-clarification", "主动澄清口径",
     "当口径不唯一时先澄清：避免 AI 擅自假设财务归属、取消订单和差异阈值。"),
    ("06-database-client-sql", "内置 SQL 客户端",
     "多标签 SQL 编辑、结果表格、导出、历史与结构树同屏，SQL 证据可继续复核。"),
    ("07-database-client-table", "表数据与结构",
     "表数据浏览与结构查看一体化：适合开发排障，也适合业务同学快速核对明细。"),
    ("08-developer-field-exploration", "字段探索（开发者）",
     "字段名不存在时，Agent 会先查字段、读表结构、验证关联路径，再改写成可执行 SQL。"),
    ("18-developer-dependency-tree", "外键依赖树（开发者）",
     "自动遍历 24 张表 / 37 条外键，以 orders 为根重建依赖树——上下游与资金链路一张图看清。"),
    ("09-developer-consistency-audit", "跨表一致性对账",
     "跨 orders/payments/refunds/ledger 自动对账，表格结论配柱状、环形与桑基图展示资金链路。"),
    ("22-showcase-correlation", "气泡 · 散点 · 找关联",
     "气泡图看投放花费 vs ROI（气泡=花费规模），散点图看客单价 vs 复购率的关联。"),
    ("23-showcase-hierarchy", "矩形树 · 旭日 · 拆层级",
     "矩形树看各类目 GMV 占比，旭日图按类目→子类目层层下钻。"),
    ("24-showcase-distribution", "箱线 · K 线 · 看分布",
     "箱线图看各品类客单价分布，K 线图看 TOP SKU 周价格波动。"),
    ("10-settings-connections", "连接管理",
     "连接管理、导入导出、默认连接切换都在一个面板完成，便于团队迁移与环境管理。"),
    ("11-settings-models", "模型配置",
     "模型、超时、上下文长度与 API 凭据分离管理；桌面与 CLI 共享同一套模型配置。"),
    ("12-settings-resources", "资源限制",
     "关键资源限制都可配置：SQL 超时、行数上限、Agent 步数、压缩阈值、并发运行数。"),
    ("13-settings-integrations", "MCP / 工具集成",
     "MCP 集成页可直接安装到 Claude、Codex、Cursor 等工具，支持 full / ask / tools 模式。"),
    ("14-backup-manager", "备份管理器",
     "统一查看历史备份的格式、行数、大小与位置，适合做本地快照与审计留存。"),
    ("15-build-assets-dialog", "构建资产",
     "构建资产支持按库选择、并发与时间预算设置，不必每次重扫整实例。"),
    ("16-connection-dialog", "连接表单",
     "连接表单内置只读负载配置、会话时区与 SSL 选项，便于安全接入生产或分析库。"),
]

TITLE = "DBAide"
SUBTITLE = "面向真实数据库的 AI 数据分析与开发工作台"
SUB2 = "结构发现 · SQL 生成 · 风险校验 · 图表回答 — 本地优先，技术与业务同流协作"


def _wrap_cjk(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> list[str]:
    """Wrap text to *max_w* pixels (per-character, since CJK has no spaces)."""
    lines: list[str] = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if draw.textlength(trial, font=fnt) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _fit(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    scale = min(max_w / img.width, max_h / img.height)
    return img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)


def _base() -> Image.Image:
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)
    # subtle top accent line
    d.rectangle([0, 0, W, 4], fill=ACCENT)
    return canvas


def scene_frame(index: int, stem: str, title: str, caption: str) -> Image.Image:
    canvas = _base()
    d = ImageDraw.Draw(canvas)

    # Header strip
    d.text((64, 40), TITLE, font=font(34), fill=TEXT)
    d.text((64 + d.textlength(TITLE, font=font(34)) + 18, 50), "/", font=font(28), fill=MUTED)
    tx = 64 + d.textlength(TITLE, font=font(34)) + 44
    d.text((tx, 46), title, font=font(30), fill=ACCENT)
    counter = f"{index:02d} / {len(SCENES):02d}"
    d.text((W - 64 - d.textlength(counter, font=font(24)), 52), counter, font=font(24), fill=MUTED)
    # progress dots
    dot_y = 96
    gap = 18
    total_w = (len(SCENES) - 1) * gap
    start_x = (W - total_w) // 2
    for i in range(len(SCENES)):
        cx = start_x + i * gap
        on = i == index - 1
        r = 5 if on else 3
        d.ellipse([cx - r, dot_y - r, cx + r, dot_y + r], fill=ACCENT if on else BORDER)

    # Screenshot area
    area_top, area_bottom = 132, 992
    max_w, max_h = W - 160, area_bottom - area_top
    shot = _fit(Image.open(PROMO / f"{stem}.png").convert("RGB"), max_w, max_h)
    sx = (W - shot.width) // 2
    sy = area_top + (max_h - shot.height) // 2
    # frame plate behind the screenshot
    pad = 10
    _rounded(d, [sx - pad, sy - pad, sx + shot.width + pad, sy + shot.height + pad],
             16, fill=PANEL, outline=BORDER, width=2)
    canvas.paste(shot, (sx, sy))

    # Caption band
    band_top = 1012
    _rounded(d, [48, band_top, W - 48, H - 28], 14, fill=PANEL, outline=BORDER, width=1)
    # number badge
    bx, by = 78, band_top + 26
    _rounded(d, [bx, by, bx + 54, by + 54], 12, fill=ACCENT)
    num = f"{index:02d}"
    nb = d.textbbox((0, 0), num, font=font(26))
    d.text((bx + (54 - (nb[2] - nb[0])) / 2, by + (54 - (nb[3] - nb[1])) / 2 - nb[1]),
           num, font=font(26), fill=(8, 12, 20))
    # caption text
    cfont = font(30)
    cx0 = bx + 54 + 26
    lines = _wrap_cjk(d, caption, cfont, W - 48 - cx0 - 40)
    ly = band_top + 34 if len(lines) > 1 else band_top + 50
    for ln in lines[:3]:
        d.text((cx0, ly), ln, font=cfont, fill=TEXT)
        ly += 42
    return canvas


def card(title_lines: list[tuple[str, int, tuple]], footer: str | None = None) -> Image.Image:
    canvas = _base()
    d = ImageDraw.Draw(canvas)
    heights = []
    for text, size, _ in title_lines:
        bb = d.textbbox((0, 0), text, font=font(size))
        heights.append(bb[3] - bb[1] + 22)
    total = sum(heights)
    y = (H - total) // 2 - 40
    for (text, size, color), hgt in zip(title_lines, heights):
        fnt = font(size)
        w = d.textlength(text, font=fnt)
        d.text(((W - w) / 2, y), text, font=fnt, fill=color)
        y += hgt
    if footer:
        fnt = font(24)
        w = d.textlength(footer, font=fnt)
        d.text(((W - w) / 2, H - 96), footer, font=fnt, fill=MUTED)
    return canvas


def build_frames() -> list[Image.Image]:
    frames = [
        card([
            (TITLE, 120, TEXT),
            (SUBTITLE, 40, ACCENT),
            ("", 12, TEXT),
            (SUB2, 26, MUTED),
        ], footer="github.com/W1412X/dbaide"),
    ]
    for i, (stem, title, caption) in enumerate(SCENES, start=1):
        frames.append(scene_frame(i, stem, title, caption))
    frames.append(card([
        ("看得见每一步 · 安全只读 · 本地优先", 44, TEXT),
        ("", 12, TEXT),
        ("SQLite · MySQL/MariaDB · PostgreSQL", 30, MUTED),
        ("中文 / English · 深色 / 浅色", 26, MUTED),
        ("", 12, TEXT),
        ("DBAide", 64, ACCENT),
    ], footer="github.com/W1412X/dbaide · MIT"))
    return frames


def render_mp4(frames, durations, intro_outro):
    import imageio.v2 as imageio
    fps = 25
    writer = imageio.get_writer(OUT_MP4, fps=fps, codec="libx264", quality=8,
                                macro_block_size=8, ffmpeg_log_level="error")
    import numpy as np
    def hold(img, secs):
        arr = np.asarray(img)
        for _ in range(int(secs * fps)):
            writer.append_data(arr)
    def fade(a, b, secs=0.35):
        na, nb = np.asarray(a).astype("float32"), np.asarray(b).astype("float32")
        n = int(secs * fps)
        for k in range(1, n + 1):
            t = k / (n + 1)
            writer.append_data((na * (1 - t) + nb * t).astype("uint8"))
    for i, img in enumerate(frames):
        hold(img, durations[i])
        if i + 1 < len(frames):
            fade(img, frames[i + 1])
    writer.close()


def render_gif(frames, durations):
    # Downscale for a lightweight, README-embeddable GIF. Cap per-scene hold so the
    # GIF stays snappy (the MP4 carries the fuller timing).
    gif_w = 1100
    ms = [min(int(d * 1000), 2600) for d in durations]
    small = []
    for f, d in zip(frames, ms):
        im = f.resize((gif_w, int(gif_w * H / W)), Image.LANCZOS).convert(
            "P", palette=Image.ADAPTIVE, colors=160)
        im.info["duration"] = d
        small.append(im)
    small[0].save(
        OUT_GIF, save_all=True, append_images=small[1:],
        duration=ms, loop=0, optimize=True, disposal=2,
    )


def main() -> int:
    frames = build_frames()
    # intro 3.4s, scenes 2.8s, outro 4.0s
    durations = [3.4] + [2.8] * len(SCENES) + [4.0]
    frames[1].save(OUT_POSTER)  # first real scene as poster
    try:
        render_mp4(frames, durations, None)
        print(f"mp4    -> {OUT_MP4} ({OUT_MP4.stat().st_size // 1024} KiB)")
    except Exception as exc:  # noqa: BLE001
        print(f"mp4 skipped: {exc}")
    render_gif(frames, durations)
    print(f"gif    -> {OUT_GIF} ({OUT_GIF.stat().st_size // 1024} KiB)")
    print(f"poster -> {OUT_POSTER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
