import logging

from PySide6.QtWidgets import QGraphicsScene
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtCore import QRectF

from canvas.slot_item import TemplateSlotItem
from canvas.template_generator import generate_template
from core.canvas_presets import DEFAULT_CANVAS_SIZE

logger = logging.getLogger(__name__)


def _to_float(value, default):
    """Значения приходят из JSON и QSettings — тип не гарантирован."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CanvasScene(QGraphicsScene):
    def __init__(self, width=None, height=None, parent=None):
        super().__init__(parent)

        # Размер по умолчанию берётся из одного места
        # (core.canvas_presets.DEFAULT_CANVAS_SIZE): раньше здесь было
        # зашито 3840×2160, а в project_io и диалогах — 1920×1080
        width = DEFAULT_CANVAS_SIZE[0] if width is None else width
        height = DEFAULT_CANVAS_SIZE[1] if height is None else height
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

        # --- Оформление коллажа (см. ui/layout_style_dialog.py) ---
        # gutter — зазор между слотами, corner_radius — скругление
        # углов, background_color / transparent_background — чем залит
        # холст под ними. Геометрию слотов эти параметры не меняют.
        self.gutter = 0.0
        self.corner_radius = 0.0
        self.background_color = QColor(255, 255, 255)
        self.transparent_background = False

        # Шахматка «прозрачности» рисуется только в редакторе
        self._checker_pixmap = None

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
        # ФИКС: при экспорте (suppress_visuals) серое поле вокруг холста
        # и служебная рамка больше не рисуются: раньше тонкая
        # серая линия по периметру попадала в готовый файл.
        export = bool(self.suppress_visuals)

        canvas_rect = QRectF(0, 0, self.canvas_width, self.canvas_height)

        if not export:
            painter.fillRect(rect, self.background_brush)

        if self.transparent_background:
            # В файле остаётся альфа-канал, в редакторе — шахматка
            if not export:
                painter.fillRect(canvas_rect, self._checker_brush())
        else:
            painter.fillRect(canvas_rect, self.canvas_brush)

        if not export:
            painter.setPen(self.canvas_pen)
            painter.drawRect(canvas_rect)

    # ---------- Оформление: отступы, скругления, фон ----------

    def layout_style(self) -> dict:
        """Текущее оформление в JSON-совместимом виде."""
        return {
            "gutter": float(self.gutter),
            "corner_radius": float(self.corner_radius),
            "background": self.background_color.name(),
            "transparent": bool(self.transparent_background),
        }

    def apply_layout_style(self, style: dict):
        """Применить оформление.

        Пропущенные ключи не трогаем — это важно для старых
        проектов, где блока "style" ещё нет.
        """
        style = style or {}

        self.gutter = max(0.0, _to_float(style.get("gutter"), self.gutter))
        self.corner_radius = max(
            0.0, _to_float(style.get("corner_radius"), self.corner_radius)
        )

        color = QColor(str(style.get("background") or ""))
        if color.isValid():
            self.background_color = color

        self.canvas_brush = QBrush(self.background_color)

        self.transparent_background = bool(
            style.get("transparent", self.transparent_background)
        )

        self._reflow_slots()
        self.update()

    def _reflow_slots(self):
        """Пересчитать картинки в слотах после смены отступов."""
        for slot in list(self.template_slots):
            if slot.scene() is not self:
                continue

            slot.update()
            if getattr(slot, "image_item", None) is not None:
                slot.position_image(slot.image_item)

    def _checker_brush(self) -> QBrush:
        """Серо-белая шахматка — индикатор прозрачного фона."""
        if self._checker_pixmap is None:
            cell = 16
            pixmap = QPixmap(cell * 2, cell * 2)
            pixmap.fill(QColor(255, 255, 255))

            painter = QPainter(pixmap)
            painter.fillRect(0, 0, cell, cell, QColor(214, 214, 214))
            painter.fillRect(cell, cell, cell, cell, QColor(214, 214, 214))
            painter.end()

            self._checker_pixmap = pixmap

        return QBrush(self._checker_pixmap)

    def build_template_from_rects(self, rects):
        """Собрать шаблон по готовым прямоугольникам.

        В отличие от build_template() сетка не случайная: геометрия
        приходит снаружи — например, из подбора холста под
        конкретные фотографии (core.auto_layout).

        Рассчитан на только что созданную сцену: изображения из
        старых слотов в панель превью здесь не возвращаются.
        """
        self.is_template_mode = True

        for slot in list(self.template_slots):
            if slot.scene() is self:
                self.removeItem(slot)

        self.template_slots = []

        for i, rect in enumerate(rects):
            slot = TemplateSlotItem(QRectF(rect), i)
            self.addItem(slot)
            self.template_slots.append(slot)

        self.template_image_count = len(self.template_slots)

        return self.template_slots

    def build_template(self):
        if not self.is_template_mode:
            logger.warning("build_template called outside of template mode")
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
