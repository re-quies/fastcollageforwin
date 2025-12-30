from PySide6.QtWidgets import QGraphicsRectItem
from PySide6.QtGui import QPen, QColor
from PySide6.QtCore import Qt, QRectF


class TemplateSlotItem(QGraphicsRectItem):
    def __init__(self, rect: QRectF):
        super().__init__(rect)

        self.occupied = False
        self.image_item = None

        self.setAcceptDrops(True)

        pen = QPen(QColor(120, 120, 120))
        pen.setStyle(Qt.DashLine)
        pen.setWidth(2)
        self.setPen(pen)

        self.setBrush(Qt.NoBrush)
        self.setZValue(-10)  # всегда под изображениями

    def is_empty(self) -> bool:
        return not self.occupied
