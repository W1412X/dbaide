from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy


from dbaide.desktop.theme import Theme
from dbaide.desktop.components.icons import svg_icon


def ghost_action_button(
    text: str, *, icon: QIcon | None = None, tooltip: str = "", parent=None
) -> QPushButton:
    """A low-profile inline action: small icon + label, no border, muted until
    hover (Codex/Claude message-action style)."""
    btn = QPushButton(text, parent)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setFixedHeight(22)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if icon is not None:
        btn.setIcon(icon)
        btn.setIconSize(QSize(12, 12))
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background: transparent;
            color: {Theme.MUTED};
            border: none;
            border-radius: 5px;
            padding: 0 6px;
            font-size: 11px;
            text-align: left;
        }}
        QPushButton:hover {{ background: {Theme.PANEL_2}; color: {Theme.TEXT}; }}
        QPushButton:pressed {{ background: {Theme.PANEL_3}; }}
        """
    )
    return btn


def _button_icon_for_text(text: str, *, primary: bool = False) -> QIcon | None:
    label = str(text or "").strip().lower()
    if not label:
        return None
    icon_name = ""
    checks: list[tuple[tuple[str, ...], str]] = [
        (("保存", "save"), "save"),
        (("测试", "test"), "play"),
        (("新建", "new"), "plus"),
        (("导入", "import"), "upload"),
        (("导出", "export"), "download"),
        (("更多", "more"), "more-horizontal"),
        (("重置", "恢复", "默认", "reset", "default"), "refresh"),
        (("取消", "cancel"), "x"),
        (("关闭", "close"), "x"),
        (("复制", "copy"), "copy"),
        (("构建", "build"), "database"),
        (("全不选", "select none", "none"), "x"),
        (("全选", "全部", "select all"), "check"),
        (("返回", "back"), "chevron-left"),
        (("设置", "settings"), "settings"),
        (("浏览", "browse"), "search"),
        (("发送", "send"), "arrow-up"),
        (("下一", "next"), "chevron-right"),
        (("清空", "清除", "clear"), "trash"),
        (("删除", "delete", "remove"), "trash"),
        (("编辑", "edit"), "pencil"),
        (("刷新", "refresh"), "refresh"),
        (("打开", "open"), "external-link"),
        (("确认", "确定", "应用", "ok", "confirm", "create", "创建", "apply"), "check"),
        (("执行", "运行", "run"), "play"),
        (("诊断", "diagnose"), "search"),
    ]
    for needles, candidate in checks:
        if any(needle in label for needle in needles):
            icon_name = candidate
            break
    if not icon_name:
        return None
    color = Theme.ACCENT if primary else Theme.TEXT_2
    return svg_icon(icon_name, color=color, size=14)


def compact_button(
    text: str,
    *,
    primary: bool = False,
    icon: QIcon | None = None,
    tooltip: str = "",
    width: int | None = None,
    parent=None,
) -> QPushButton:
    """Fixed-size action button — avoids macOS default-button blow-up."""
    btn = AgentButton(text, primary=primary, parent=parent)
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setFixedHeight(26)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    lower = str(text or "").strip().lower()
    danger = any(token in lower for token in ("删除", "移除", "危险", "delete", "remove", "danger"))
    if danger:
        icon = svg_icon("trash", color=Theme.RED, size=14)
        btn.setProperty("danger", True)
    elif icon is None:
        icon = _button_icon_for_text(text, primary=primary)
    if icon is not None:
        btn.setIcon(icon)
        btn.setIconSize(QSize(14, 14))
    if tooltip:
        btn.setToolTip(tooltip)
    if width is not None:
        btn.setFixedWidth(width)
    else:
        btn.adjustSize()
        btn.setFixedWidth(max(btn.sizeHint().width(), 74 if text else 30))
    return btn


class Panel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("panel", True)


class Pill(QLabel):
    def __init__(self, text: str, color: str = Theme.BLUE, parent=None) -> None:
        super().__init__(text, parent)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.setStyleSheet(
            f"color:{color}; background:{Theme.PANEL_2}; border:1px solid {Theme.BORDER_SOFT}; "
            "border-radius:8px; padding:3px 9px; font-size:11px; font-weight:700;"
        )


class StatusBadge(Pill):
    COLORS = {
        "ready": Theme.GREEN,
        "running": Theme.BLUE,
        "idle": Theme.MUTED,
        "failed": Theme.RED,
        "warning": Theme.YELLOW,
        "building": Theme.BLUE,
        "missing": Theme.YELLOW,
    }

    def set_state(self, text: str, state: str = "idle") -> None:
        # A small colored status dot + muted label on a quiet pill — calmer than a
        # fully color-outlined badge, the way AI IDEs show status.
        color = self.COLORS.get(state, Theme.MUTED)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setText(
            f"<span style='color:{color}; font-size:10px;'>●</span>"
            f"&nbsp;<span style='color:{Theme.TEXT_2};'>{text}</span>"
        )
        self.setFixedHeight(30)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.setStyleSheet(
            f"background:{Theme.PANEL_2}; border:1px solid {Theme.BORDER_SOFT};"
            " border-radius:8px; padding:0 11px; font-size:12px; font-weight:600;"
        )


class SectionLabel(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("muted", True)


class AgentButton(QPushButton):
    def __init__(
        self,
        text: str,
        *,
        primary: bool = False,
        tab: bool = False,
        active: bool = False,
        parent=None,
    ) -> None:
        super().__init__(text, parent)
        if primary:
            self.setProperty("primary", True)
        if tab:
            self.setProperty("tab", True)
        if active:
            self.setProperty("active", True)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(text)


def discard_widget(widget: QWidget | None) -> None:
    """Hide and schedule widget deletion without ``setParent(None)``.

    On macOS (and some other platforms), reparenting a visible ``QWidget`` to
    ``None`` promotes it to a transient top-level window until ``deleteLater``
    runs — causing ghost popups, empty native frames, and flicker during rapid
    layout rebuilds. ``takeAt`` / ``removeWidget`` already detach from layout;
    hiding first avoids paint ghosts before the event loop deletes the widget.
    """
    from PyQt6.QtWidgets import QWidget

    if widget is None:
        return
    widget.hide()
    widget.deleteLater()


def clear_layout_widgets(layout) -> None:
    """Remove every widget from *layout* using :func:`discard_widget`."""
    while layout.count():
        item = layout.takeAt(0)
        discard_widget(item.widget())
