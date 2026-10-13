from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QPushButton, QGraphicsOpacityEffect
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEvent


class ToastNotification(QWidget):
    """In-app toast: a child widget drawn INSIDE the TagPilot window (top-right
    corner, stacked under each other), never a separate on-screen window, so it
    never appears on top of other applications."""

    MARGIN = 20
    TOP = 40
    GAP = 8

    def __init__(self, parent, message, duration=5000, toast_type="error"):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("tpToast")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)

        if toast_type == "error":
            bg, text_color, icon = "rgba(220, 53, 69, 240)", "white", "❌ "
        elif toast_type == "warning":
            bg, text_color, icon = "rgba(255, 193, 7, 240)", "black", "⚠️ "
        else:
            bg, text_color, icon = "rgba(40, 167, 69, 240)", "white", "✅ "

        self.setStyleSheet(f"""
            QWidget#tpToast {{
                background-color: {bg};
                border-radius: 8px;
            }}
            QLabel {{
                background: transparent;
                color: {text_color};
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton {{
                background: transparent;
                border: none;
                color: {text_color};
                font-weight: bold;
                font-size: 16px;
            }}
            QPushButton:hover {{
                color: rgba(255, 255, 255, 180);
            }}
        """)

        self.msg_label = QLabel(f"{icon}{message}")
        self.msg_label.setWordWrap(True)
        self.msg_label.setMaximumWidth(420)
        layout.addWidget(self.msg_label)

        self.close_btn = QPushButton("✕")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.clicked.connect(self.hide_toast)
        layout.addWidget(self.close_btn, alignment=Qt.AlignTop)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide_toast)
        self._duration = duration

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self.animation = QPropertyAnimation(self._effect, b"opacity", self)
        self._closing = False
        self.adjustSize()
        parent.installEventFilter(self)          # follow the window when it is resized

    def _x(self):
        p = self.parent()
        return max(self.MARGIN, p.width() - self.width() - self.MARGIN)

    def show_toast(self):
        p = self.parent()
        others = [w for w in p.findChildren(ToastNotification, options=Qt.FindDirectChildrenOnly)
                  if w is not self and w.isVisible() and not w._closing]
        y = self.TOP + sum(w.height() + self.GAP for w in others)
        self.move(self._x(), y)
        self.show()
        self.raise_()
        self.animation.stop()
        self.animation.setDuration(300)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.start()
        self.timer.start(self._duration)

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QEvent.Resize and self.isVisible():
            self.move(self._x(), self.y())
        return False

    def hide_toast(self):
        if self._closing:
            return
        self._closing = True
        self.timer.stop()
        self.animation.stop()
        self.animation.setDuration(300)
        self.animation.setStartValue(self._effect.opacity())
        self.animation.setEndValue(0.0)
        self.animation.finished.connect(self._finish)
        self.animation.start()

    def _finish(self):
        try:
            self.parent().removeEventFilter(self)
        except Exception:
            pass
        self.hide()
        self.deleteLater()
