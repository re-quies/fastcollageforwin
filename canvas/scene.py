import logging

from PySide6.QtWidgets import QGraphicsScene
from PySide6.QtGui import QBrush, QPen, QColor
from PySide6.QtCore import QRectF

from canvas.slot_item import TemplateSlotItem
from canvas.template_generator import generate_template

logger = logging.getLogger(__name__)


class CanvasScene(QGraphicsScene):
    def __init__(self, width=3840, height=2160, parent=None):
        super().__init__(parent)
        # Флаг для временного отключения визуальных элементов (handles, selection)
        self.suppress_visuals = False
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
        # Вычислим масштаб относительно текущего размера
        old_w = float(self.canvas_width)
        old_h = float(self.canvas_height)

        # Обновляем размеры сцены
        self.canvas_width = width
        self.canvas_height = height
        self.setSceneRect(0, 0, width, height)

        # Если есть слоты шаблона — масштабируем их пропорционально
        if self.template_slots:
            sx = width / old_w if old_w else 1.0
            sy = height / old_h if old_h else 1.0

            for slot in list(self.template_slots):
                try:
                    # текущая позиция и размеры в сцене
                    left = slot.scenePos().x()
                    top = slot.scenePos().y()
                    w = slot.rect().width()
                    h = slot.rect().height()

                    slot.setRect(0, 0, max(1.0, w * sx), max(1.0, h * sy))
                    slot.setPos(left * sx, top * sy)
                    slot._update_handles()

                    # Пересчитаем вложенное изображение под новый слот
                    if getattr(slot, 'image_item', None) is not None:
                        slot.accept_image(slot.image_item)
                except Exception:
                    logger.exception(
                        "Failed to rescale template slot %s",
                        getattr(slot, 'index', '?'),
                    )

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

        window = self.views()[0].window() if self.views() else None

        # Удаляем старые слоты и возвращаем вложенные изображения в превью
        for slot in list(self.template_slots):
            img = getattr(slot, 'image_item', None)
            if img is not None:
                if window is not None and hasattr(window, 'preview_panel'):
                    window.preview_panel.add_pixmap(
                        img.original_pixmap,
                        getattr(img, 'source_path', None),
                    )
                else:
                    logger.warning(
                        "Preview panel is not available; "
                        "image from slot %s is dropped",
                        getattr(slot, 'index', '?'),
                    )
                if img.scene() is self:
                    self.removeItem(img)

            if slot.scene() is self:
                self.removeItem(slot)

        # Генерируем новые прямоугольники и создаём слоты
        rects = generate_template(
            self.canvas_width,
            self.canvas_height,
            self.template_image_count
        )

        # Обновляем список слотов
        self.template_slots = []

        for i, rect in enumerate(rects):
            slot = TemplateSlotItem(rect, i)
            self.addItem(slot)
            self.template_slots.append(slot)
