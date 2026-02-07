from PySide6.QtWidgets import QGraphicsScene
from PySide6.QtGui import QBrush, QPen, QColor
from PySide6.QtCore import QRectF
from canvas.slot_item import TemplateSlotItem
from canvas.template_generator import generate_template

class CanvasScene(QGraphicsScene):
    def __init__(self, width=3840, height=2160, parent=None):
        super().__init__(parent)
        self.is_template_mode = False
        self.template_slots = []
        self.template_image_count = 0
        self.canvas_width = width
        self.canvas_height = height

        self.setSceneRect(0, 0, width, height)

        self.background_brush = QBrush(QColor(180, 180, 180))
        self.canvas_brush = QBrush(QColor(255, 255, 255))
        self.canvas_pen = QPen(QColor(120, 120, 120), 1)
        

    def set_canvas_size(self, width, height):
        self.canvas_width = width
        self.canvas_height = height
        self.setSceneRect(0, 0, width, height)
        self.update()

    def drawBackground(self, painter, rect):
        # Серый фон
        painter.fillRect(rect, self.background_brush)

        # Белая рабочая область
        canvas_rect = QRectF(0, 0, self.canvas_width, self.canvas_height)
        painter.fillRect(canvas_rect, self.canvas_brush)
        painter.setPen(self.canvas_pen)
        painter.drawRect(canvas_rect)

    def build_template(self):
        if not self.is_template_mode:
            return

        rects = generate_template(
            self.canvas_width,
            self.canvas_height,
            self.template_image_count
        )

        self.template_slots.clear()

        for i, rect in enumerate(rects):
            slot = TemplateSlotItem(rect, i)
            self.addItem(slot)
            self.template_slots.append(slot)