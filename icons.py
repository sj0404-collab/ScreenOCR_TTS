"""
Фабрика иконок для кнопок.
Используется вместо unicode-символов для корректного отображения.
"""
from PyQt6.QtGui import (QIcon, QPixmap, QPainter, QColor, QPainterPath,
                          QPolygon, QPen)
from PyQt6.QtCore import Qt, QPoint, QRect


def _make_icon(draw_fn, size=24, bg=None):
    """Создаёт QIcon через draw_fn(painter, rect)."""
    pm = QPixmap(size, size)
    pm.fill(bg if bg else QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_fn(p, pm.rect())
    p.end()
    return QIcon(pm)


def icon_male(sz=24):
    def draw(p, r):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#4fc3f7"))
        cx, cy = r.center().x(), r.top() + 6
        p.drawEllipse(QPoint(cx, cy), 4, 4)
        path = QPainterPath()
        path.moveTo(cx - 5, cy + 6)
        path.lineTo(cx + 5, cy + 6)
        path.lineTo(cx + 7, r.bottom() - 1)
        path.lineTo(cx - 7, r.bottom() - 1)
        path.closeSubpath()
        p.drawPath(path)
    return _make_icon(draw, sz)


def icon_female(sz=24):
    def draw(p, r):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f48fb1"))
        cx, cy = r.center().x(), r.top() + 6
        p.drawEllipse(QPoint(cx, cy), 4, 4)
        path = QPainterPath()
        path.moveTo(cx - 4, cy + 6)
        path.lineTo(cx + 4, cy + 6)
        path.lineTo(cx + 8, r.bottom() - 1)
        path.lineTo(cx - 8, r.bottom() - 1)
        path.closeSubpath()
        p.drawPath(path)
    return _make_icon(draw, sz)


def icon_narrator(sz=24):
    def draw(p, r):
        p.setPen(Qt.PenStyle.NoPen)
        # Happy mask
        p.setBrush(QColor("#ce93d8"))
        p.drawEllipse(QPoint(r.center().x() - 4, r.center().y() - 1), 6, 7)
        p.setBrush(QColor("#1e1e2e"))
        p.drawEllipse(QPoint(r.center().x() - 6, r.center().y() - 3), 1, 1)
        p.drawEllipse(QPoint(r.center().x() - 2, r.center().y() - 3), 1, 1)
        # Sad mask
        p.setBrush(QColor("#9c27b0"))
        p.drawEllipse(QPoint(r.center().x() + 4, r.center().y() + 1), 6, 7)
        p.setBrush(QColor("#1e1e2e"))
        p.drawEllipse(QPoint(r.center().x() + 2, r.center().y() - 1), 1, 1)
        p.drawEllipse(QPoint(r.center().x() + 6, r.center().y() - 1), 1, 1)
    return _make_icon(draw, sz)


def icon_play(sz=24):
    def draw(p, r):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#81c784"))
        poly = QPolygon([QPoint(r.left() + 5, r.top() + 3),
                         QPoint(r.left() + 5, r.bottom() - 3),
                         QPoint(r.right() - 3, r.center().y())])
        p.drawPolygon(poly)
    return _make_icon(draw, sz)


def icon_stop(sz=24):
    def draw(p, r):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ef9a9a"))
        m = 6
        p.drawRoundedRect(QRect(r.left() + m, r.top() + m,
                                r.width() - 2*m, r.height() - 2*m), 2, 2)
    return _make_icon(draw, sz)
