from PySide6.QtWidgets import QGraphicsRectItem
from PySide6.QtGui import QPen, QBrush, QColor
from PySide6.QtCore import Qt, QRectF


class TemplateSlotItem(QGraphicsRectItem):
    def __init__(self, rect: QRectF, index: int):
        # rect может содержать абсолютные координаты — сделаем локальный rect и установим позицию
        local = QRectF(0, 0, rect.width(), rect.height())
        super().__init__(local)

        # позиция слота в системе сцены
        self.setPos(rect.left(), rect.top())

        self.index = index
        self.image_item = None  # сюда позже кладём ImageItem

        self.setZValue(0)

        # визуально — только для отладки
        self.setPen(QPen(Qt.black, 1))
        self.setBrush(QBrush(Qt.transparent))

        self.setFlag(QGraphicsRectItem.ItemClipsChildrenToShape, True)

        self._highlighted = False

    def set_highlight(self, on: bool):
        """Включить/выключить визуальную подсветку слота."""
        if self._highlighted == on:
            return
        self._highlighted = on
        if on:
            self.setPen(QPen(Qt.red, 2))
            # полупрозрачная заливка для визуального эффекта
            self.setBrush(QBrush(QColor(255, 0, 0, 50)))
            self.setZValue(10)
        else:
            self.setPen(QPen(Qt.black, 1))
            self.setBrush(QBrush(Qt.transparent))
            self.setZValue(0)

    def accept_image(self, image_item):
        """Разместить `image_item` как дочерний элемент слота и центровать его."""
        # Если в слоте уже есть изображение — удалим ссылку (фактический объект будет заменён извне)
        if self.image_item is image_item:
            return

        # Если изображение раньше было привязано к другому слоту — очистим ту ссылку
        try:
            prev = image_item.parentItem()
            if prev is not None and type(prev).__name__ == 'TemplateSlotItem' and prev is not self:
                prev.image_item = None
        except Exception:
            pass

        self.image_item = image_item
        # Делегируем родительство — дочерний элемент будет обрезан по форме слота
        image_item.setParentItem(self)

        # Подгоняем позицию и масштаб под слот
        slot_rect = self.rect()  # локальный rect с origin (0,0)
        pw = image_item.original_pixmap.width()
        ph = image_item.original_pixmap.height()

        # Масштабируем так, чтобы картинка покрывала слот (cover)
        sx = slot_rect.width() / pw
        sy = slot_rect.height() / ph
        scale = max(sx, sy)
        image_item.setScale(scale)

        # Центрируем изображение внутри слота (координаты относительны слоту)
        w_scaled = pw * scale
        h_scaled = ph * scale

        # Устанавливаем точку трансформации в левый верхний угол, чтобы масштабирование
        # и позиционирование были детерминированы относительно (0,0)
        try:
            image_item.setTransformOriginPoint(0, 0)
        except Exception:
            pass

        x = (slot_rect.width() - w_scaled) / 2
        y = (slot_rect.height() - h_scaled) / 2
        image_item.setPos(x, y)

    def remove_image(self):
        if self.image_item:
            # Отсоединяем связь, но не удаляем сам объект (удаление/перемещение обрабатывается снаружи)
            self.image_item = None
