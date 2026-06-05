"""Compose the GitHub social-preview card (1200×630) from the CURRENT app UI.

Renders the real MainWindow (via shoot.build_window/populate), grabs it, and paints a
branded hero card around it with QPainter — no external design tool or Pillow needed.
Keeps docs/images/social-preview.png in sync with the live UI. Usage:

    QT_QPA_PLATFORM=offscreen PYTHONPATH=. .venv/bin/python tools/shoot_social.py
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath,
                         QPen, QPixmap)
from PyQt6.QtWidgets import QApplication

from dbaide.desktop.theme import app_style
from tools.shoot import build_window, populate

W, H = 1200, 630
OUT = Path("docs/images/social-preview.png")


def _font(size: int, *, bold: bool = False) -> QFont:
    f = QFont("Helvetica Neue")
    f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setPixelSize(size)
    f.setBold(bold)
    return f


def compose(shot: QPixmap) -> QPixmap:
    card = QPixmap(W, H)
    card.setDevicePixelRatio(1)
    p = QPainter(card)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # Background: dark navy gradient.
    bg = QLinearGradient(0, 0, W, H)
    bg.setColorAt(0.0, QColor("#0b1018"))
    bg.setColorAt(1.0, QColor("#05080d"))
    p.fillRect(0, 0, W, H, QBrush(bg))

    left = 56
    # Brand.
    p.setPen(QColor("#5b9bff"))
    p.setFont(_font(20, bold=True))
    p.drawText(left, 70, "●  DBAide")

    # Title (two lines, second accented).
    p.setFont(_font(52, bold=True))
    p.setPen(QColor("#f3f6fb"))
    p.drawText(left, 168, "Ask your database")
    p.setPen(QColor("#5b9bff"))
    p.drawText(left, 226, "in plain language.")

    # Subtitle.
    p.setFont(_font(20))
    p.setPen(QColor("#9aa6b6"))
    p.drawText(QRectF(left, 250, 470, 70), int(Qt.TextFlag.TextWordWrap),
               "A local-first AI database assistant — safe, "
               "never-guesses, CLI + desktop.")

    # Bullets.
    bullets = [
        "Agentic discovery & safe read-only SQL",
        "Asks before it guesses — no wrong numbers",
        "SQLite · MySQL · PostgreSQL · EN / 中文",
    ]
    y = 360
    for b in bullets:
        p.setPen(QColor("#5b9bff"))
        p.setFont(_font(20, bold=True))
        p.drawText(left, y, "›")
        p.setPen(QColor("#c7d0dc"))
        p.setFont(_font(20))
        p.drawText(left + 26, y, b)
        y += 46

    # Footer.
    p.setFont(_font(17))
    p.setPen(QColor("#6b7686"))
    p.drawText(left, H - 36, "github.com/W1412X/dbaide      ·      MIT")

    # Right: the app screenshot, scaled into a rounded panel with a thin border.
    panel = QRectF(560, 86, W - 560 - 40, H - 86 - 60)
    scaled = shot.scaled(int(panel.width()), int(panel.height()),
                         Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation)
    path = QPainterPath()
    path.addRoundedRect(panel, 12, 12)
    p.save()
    p.setClipPath(path)
    p.drawPixmap(QPointF(panel.left(), panel.top()), scaled)
    p.restore()
    p.setPen(QPen(QColor("#2a3340"), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.end()
    return card


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(app_style())
    win = build_window(app)
    populate(win)
    for _ in range(5):
        app.processEvents()
    shot = win.grab()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    compose(shot).save(str(OUT))
    print(f"social card → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
