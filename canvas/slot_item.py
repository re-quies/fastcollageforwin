from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QStyle,
    QStyleOptionGraphicsItem,
)
from PySide6.QtGui import QPen, QBrush, QColor
from PySide6.QtCore import Qt, QRectF, QPointF


class TemplateSlotItem(QGraphicsRectItem):
    def __init__(self, rect: QRectF, index: int):
        # rect может содержать абсолютные координаты — сделаем локальный rect и установим позицию
        local = QRectF(0, 0, rect.width(), rect.height())
        super().__init__(local)

        # позиция слота в системе сцены
        self.setPos(rect.left(), rect.top())

        self.index = index
        self.image_item = None  # сюда позже кладём ImageItem

        # Базовый z-уровень слота (слои: выше/ниже других слотов).
        # Подсветка при перетаскивании временно поднимает слот,
        # поэтому настоящий слой храним отдельно и не теряем его.
        self._base_z = 0.0
        self.setZValue(0)

        # Слот можно выделить кликом (по пустой области или рамке),
        # чтобы управлять его слоем через меню "Слои"
        self.setFlag(QGraphicsRectItem.ItemIsSelectable, True)

        # визуально — только для отладки
        self.setPen(QPen(Qt.black, 1))
        self.setBrush(QBrush(Qt.transparent))

        self.setFlag(QGraphicsRectItem.ItemClipsChildrenToShape, True)

        self._highlighted = False

        # Минимальный размер слота
        self._min_width = 30
        self._min_height = 30

        # Создаём маркеры-ползунки по краям (лево/право/верх/низ)
        self._handles = {}
        self._create_handles()

        # Поддержка hover-событий для показа/скрытия ползунков
        self.setAcceptHoverEvents(True)

    def set_highlight(self, on: bool):
        """Включить/выключить визуальную подсветку слота."""
        if self._highlighted == on:
            return
        self._highlighted = on
        if on:
            self.setPen(QPen(Qt.red, 2))
            # полупрозрачная заливка для визуального эффекта
            self.setBrush(QBrush(QColor(255, 0, 0, 50)))
            # Поднимаем НАД всеми слотами, но с учётом базового слоя
            self.setZValue(self._base_z + 10)
        else:
            self.setPen(QPen(Qt.black, 1))
            self.setBrush(QBrush(Qt.transparent))
            self.setZValue(self._base_z)

        # показать/скрыть маркеры (учитываем режим скрытия визуализаций на сцене)
        suppress = bool(getattr(self.scene(), 'suppress_visuals', False))

        for h in self._handles.values():
            h.setVisible(on and not suppress)

    # ---------- Слои (z-порядок слотов) ----------

    def base_z(self) -> float:
        """Текущий слой слота (без учёта временной подсветки)."""
        return self._base_z

    def set_base_z(self, z: float):
        """Установить слой слота (выше/ниже других слотов).

        Изображение внутри слота — дочерний элемент, поэтому
        оно поднимается/опускается вместе со слотом.
        """
        self._base_z = float(z)
        # Если слот сейчас подсвечен — сохраняем приподнятое состояние
        self.setZValue(self._base_z + 10 if self._highlighted else self._base_z)

    def paint(self, painter, option, widget=None):
        # При экспорте (suppress_visuals) скрываем стандартную
        # пунктирную рамку выделения выбранного слота
        suppress = bool(getattr(self.scene(), 'suppress_visuals', False))
        if suppress and (option.state & QStyle.State_Selected):
            option = QStyleOptionGraphicsItem(option)
            option.state = option.state & ~QStyle.State_Selected
        super().paint(painter, option, widget)

    def accept_image(self, image_item):
        """Разместить `image_item` как дочерний элемент слота.

        Изображение масштабируется под слот (cover) и позиционируется
        по центру с учётом пользовательского смещения `slot_offset`
        (панорамирование внутри слота). Смещение сбрасывается только
        когда изображение попадает в ДРУГОЙ слот (или приходит извне),
        поэтому случайный клик/бросок в тот же слот больше не центрирует.
        """
        # Если изображение раньше было привязано к другому слоту — очистим ту ссылку.
        # ВАЖНО (fix): очищаем ссылку только если она указывает именно на это
        # изображение — иначе при обмене (swap) затиралась ссылка на уже
        # помещённое в слот другое изображение.
        prev = image_item.parentItem()
        if (
            isinstance(prev, TemplateSlotItem)
            and prev is not self
            and prev.image_item is image_item
        ):
            prev.image_item = None

        # Смещение имеет смысл только внутри "своего" слота:
        # при попадании в новый слот начинаем с центра.
        if prev is not self:
            image_item.slot_offset = QPointF(0, 0)

        self.image_item = image_item
        # Делегируем родительство — дочерний элемент будет обрезан по форме слота
        image_item.setParentItem(self)

        self.position_image(image_item)

        # При изменении слота гарантируем, что маркеры обновлены
        self._update_handles()

    def position_image(self, image_item):
        """Пересчитать масштаб и позицию изображения внутри слота.

        Учитывает `image_item.slot_offset` — смещение относительно центра,
        ограниченное так, чтобы слот всегда оставался полностью покрыт
        изображением (без пустых полос по краям).
        """
        slot_rect = self.rect()  # локальный rect с origin (0,0)
        pw = image_item.original_pixmap.width()
        ph = image_item.original_pixmap.height()

        # Масштабируем так, чтобы картинка покрывала слот (cover)
        sx = slot_rect.width() / pw
        sy = slot_rect.height() / ph
        scale = max(sx, sy)
        image_item.setScale(scale)

        w_scaled = pw * scale
        h_scaled = ph * scale

        # Устанавливаем точку трансформации в левый верхний угол, чтобы масштабирование
        # и позиционирование были детерминированы относительно (0,0)
        image_item.setTransformOriginPoint(0, 0)

        # Допустимое смещение: половина "излишка" картинки за пределами слота
        max_dx = max(0.0, (w_scaled - slot_rect.width()) / 2)
        max_dy = max(0.0, (h_scaled - slot_rect.height()) / 2)

        offset = getattr(image_item, "slot_offset", QPointF(0, 0))
        off_x = max(-max_dx, min(offset.x(), max_dx))
        off_y = max(-max_dy, min(offset.y(), max_dy))

        # Сохраняем уже ограниченное смещение
        # (важно для undo и сохранения проекта)
        image_item.slot_offset = QPointF(off_x, off_y)

        x = (slot_rect.width() - w_scaled) / 2 + off_x
        y = (slot_rect.height() - h_scaled) / 2 + off_y
        image_item.setPos(x, y)

    def remove_image(self):
        if self.image_item:
            # Отсоединяем связь, но не удаляем сам объект
            # (удаление/перемещение обрабатывается снаружи)
            self.image_item = None

    def _create_handles(self):
        # Вспомогательный класс для маркера
        class _Handle(QGraphicsRectItem):
            def __init__(self, parent_slot, side):
                super().__init__(0, 0, 24, 24, parent_slot)
                self.slot = parent_slot
                self.side = side  # 'left','right','top','bottom'
                self.setBrush(QBrush(QColor(200, 200, 200)))
                self.setPen(QPen(Qt.darkGray, 1))
                self.setZValue(1000)
                self.setVisible(False)
                self.setFlag(QGraphicsRectItem.ItemIsMovable, False)
                self.setAcceptedMouseButtons(Qt.LeftButton)

            def mousePressEvent(self, event):
                self._start_scene_pos = event.scenePos()
                self._orig_rect = QRectF(self.slot.rect())
                self._orig_pos = QPointF(self.slot.pos())
                event.accept()

            def mouseMoveEvent(self, event):
                # смещение в координатах сцены
                delta = event.scenePos() - self._start_scene_pos

                # исходные крайние координаты сцены
                orig_left = self._orig_pos.x()
                orig_top = self._orig_pos.y()
                orig_right = orig_left + self._orig_rect.width()
                orig_bottom = orig_top + self._orig_rect.height()

                scene = self.slot.scene()
                canvas_w = getattr(scene, 'canvas_width', None)
                canvas_h = getattr(scene, 'canvas_height', None)

                new_left = orig_left
                new_top = orig_top
                new_right = orig_right
                new_bottom = orig_bottom

                if self.side == 'left':
                    dx = delta.x()
                    new_left = orig_left + dx
                    # ограничение по min width
                    if new_right - new_left < self.slot._min_width:
                        new_left = new_right - self.slot._min_width
                elif self.side == 'right':
                    dx = delta.x()
                    new_right = orig_right + dx
                    if new_right - new_left < self.slot._min_width:
                        new_right = new_left + self.slot._min_width
                elif self.side == 'top':
                    dy = delta.y()
                    new_top = orig_top + dy
                    if new_bottom - new_top < self.slot._min_height:
                        new_top = new_bottom - self.slot._min_height
                elif self.side == 'bottom':
                    dy = delta.y()
                    new_bottom = orig_bottom + dy
                    if new_bottom - new_top < self.slot._min_height:
                        new_bottom = new_top + self.slot._min_height

                # Ограничиваем по границам холста, если известны размеры
                if canvas_w is not None:
                    if new_left < 0:
                        new_left = 0
                    if new_right > canvas_w:
                        new_right = canvas_w
                    # При ограничении правой стороны поддерживаем минимальную ширину
                    if new_right - new_left < self.slot._min_width:
                        new_left = max(0, new_right - self.slot._min_width)

                if canvas_h is not None:
                    if new_top < 0:
                        new_top = 0
                    if new_bottom > canvas_h:
                        new_bottom = canvas_h
                    if new_bottom - new_top < self.slot._min_height:
                        new_top = max(0, new_bottom - self.slot._min_height)

                new_w = new_right - new_left
                new_h = new_bottom - new_top

                # Устанавливаем локальный rect и позицию в сцене
                self.slot.setRect(0, 0, new_w, new_h)
                self.slot.setPos(QPointF(new_left, new_top))
                self.slot._update_handles()

                # Если в слоте есть изображение — перестроить его
                if self.slot.image_item is not None:
                    self.slot.accept_image(self.slot.image_item)

                event.accept()

            def mouseReleaseEvent(self, event):
                event.accept()

        # Создаём 4 маркера
        sides = ['left', 'right', 'top', 'bottom']
        for s in sides:
            h = _Handle(self, s)
            self._handles[s] = h

        self._update_handles()

    def hoverEnterEvent(self, event):
        # показываем маркеры при наведении (если не отключено на сцене)
        suppress = bool(getattr(self.scene(), 'suppress_visuals', False))

        for h in self._handles.values():
            h.setVisible(not suppress)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        # скрываем маркеры при уходе курсора
        for h in self._handles.values():
            h.setVisible(False)
        super().hoverLeaveEvent(event)

    def _update_handles(self):
        # Позиционирование маркеров по краям слота (в координатах слота)
        r = self.rect()
        # Размер маркера
        hw = self._handles.get('left').rect().width() if self._handles else 8
        hh = self._handles.get('left').rect().height() if self._handles else 8

        # Учитываем режим скрытия визуалов на сцене
        suppress = bool(getattr(self.scene(), 'suppress_visuals', False))

        # left: по центру левой границы
        left = self._handles.get('left')
        if left is not None:
            left.setRect(-hw/2, r.height()/2 - hh/2, hw, hh)
            left.setVisible(left.isVisible() and not suppress)

        right = self._handles.get('right')
        if right is not None:
            right.setRect(r.width() - hw/2, r.height()/2 - hh/2, hw, hh)
            right.setVisible(right.isVisible() and not suppress)

        top = self._handles.get('top')
        if top is not None:
            top.setRect(r.width()/2 - hw/2, -hh/2, hw, hh)
            top.setVisible(top.isVisible() and not suppress)

        bottom = self._handles.get('bottom')
        if bottom is not None:
            bottom.setRect(r.width()/2 - hw/2, r.height() - hh/2, hw, hh)
            bottom.setVisible(bottom.isVisible() and not suppress)
