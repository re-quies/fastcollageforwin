from PySide6.QtWidgets import QGraphicsPixmapItem
from PySide6.QtGui import QPen, QPixmap, QTransform
from PySide6.QtCore import Qt, QRectF, QPointF


class ImageItem(QGraphicsPixmapItem):
    def __init__(self, pixmap: QPixmap):
        super().__init__(pixmap)

        # ====== ОРИГИНАЛ ======
        self.original_pixmap = pixmap
        self.base_size = pixmap.size()

        # ====== ZOOM CONTENT ======
        self.zoom_factor = 1.0
        self.zoom_center = QPointF(
            pixmap.width() / 2,
            pixmap.height() / 2
        )

        # ====== PAN STATE ======
        self._panning = False
        self._last_mouse_pos = None

        # ====== FLAGS ======
        self.setFlags(
            QGraphicsPixmapItem.ItemIsMovable
            | QGraphicsPixmapItem.ItemIsSelectable
            | QGraphicsPixmapItem.ItemSendsGeometryChanges
        )

        self.setAcceptHoverEvents(True)
        self.setTransformOriginPoint(self.boundingRect().center())

        # ====== ZOOM & MIRROR STATE ======
        self._old_pos = self.pos()
        self._old_scale = self.scale()
        self._old_rotation = self.rotation()

        # Флаги для зеркалирования
        self.mirrored_horizontal = False
        self.mirrored_vertical = False

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)

        if self.isSelected():
            pen = QPen(Qt.blue, 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(self.boundingRect())

    def zoom_content(self, factor: float):
        """Масштабируем содержимое, учитывая зеркалирование"""
        self.zoom_factor = max(1.0, min(self.zoom_factor * factor, 8.0))
        self._update_visible_pixmap()

    def _update_visible_pixmap(self):
        w = self.original_pixmap.width() / self.zoom_factor
        h = self.original_pixmap.height() / self.zoom_factor

        x = self.zoom_center.x() - w / 2
        y = self.zoom_center.y() - h / 2

        x = max(0, min(x, self.original_pixmap.width() - w))
        y = max(0, min(y, self.original_pixmap.height() - h))

        rect = QRectF(x, y, w, h)

        cropped = self.original_pixmap.copy(rect.toRect())

        # Применяем зеркалирование
        if self.mirrored_horizontal:
            cropped = cropped.transformed(QTransform(-1, 0, 0, 1, 0, 0))  # зеркалирование по горизонтали

        if self.mirrored_vertical:
            cropped = cropped.transformed(QTransform(1, 0, 0, -1, 0, 0))  # зеркалирование по вертикали

        # ВАЖНО: возвращаем к исходному размеру
        scaled = cropped.scaled(
            self.base_size,
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation
        )

        self.setPixmap(scaled)

    def mirror_image(self, axis: str):
        """Применяем зеркалирование изображения"""
        if axis == 'horizontal':
            self.mirrored_horizontal = not self.mirrored_horizontal
        elif axis == 'vertical':
            self.mirrored_vertical = not self.mirrored_vertical

        self._update_visible_pixmap()

    def mousePressEvent(self, event):
        self._old_pos = self.pos()
        self._old_scale = self.scale()
        self._old_rotation = self.rotation()

        # Проверяем режим во View, а не клавишу
        scene = self.scene()
        if scene and scene.views():
            view = scene.views()[0]
            if getattr(view, "content_zoom_mode", False):
                self._panning = True
                self._last_mouse_pos = event.pos()
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._last_mouse_pos
            self._last_mouse_pos = event.pos()

            self.zoom_center -= delta / self.zoom_factor
            self._update_visible_pixmap()
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._panning = False
        self._last_mouse_pos = None
        super().mouseReleaseEvent(event)

        if (
            self._old_pos != self.pos()
            or self._old_scale != self.scale()
            or self._old_rotation != self.rotation()
        ):
            from undo.commands import TransformCommand

            scene = self.scene()
            if not scene or not scene.views():
                return

            view = scene.views()[0]
            window = view.parent()

            if hasattr(window, "undo_stack"):
                window.undo_stack.push(
                    TransformCommand(
                        self,
                        self._old_pos,
                        self._old_scale,
                        self._old_rotation,
                        self.pos(),
                        self.scale(),
                        self.rotation(),
                    )
                )
