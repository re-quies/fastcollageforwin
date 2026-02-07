from PySide6.QtWidgets import QGraphicsRectItem
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

        self.setZValue(0)

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
            self.setZValue(10)
        else:
            self.setPen(QPen(Qt.black, 1))
            self.setBrush(QBrush(Qt.transparent))
            self.setZValue(0)

        # показать/скрыть маркеры (учитываем режим скрытия визуализаций на сцене)
        scene = self.scene()
        suppress = False
        try:
            suppress = bool(getattr(scene, 'suppress_visuals', False))
        except Exception:
            suppress = False

        for h in self._handles.values():
            h.setVisible(on and not suppress)

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

        # При изменении слота гарантируем, что маркеры обновлены
        self._update_handles()

    def remove_image(self):
        if self.image_item:
            # Отсоединяем связь, но не удаляем сам объект (удаление/перемещение обрабатывается снаружи)
            self.image_item = None

    def _create_handles(self):
        # Вспомогательный класс для маркера
        from PySide6.QtWidgets import QGraphicsRectItem

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
                canvas_w = None
                canvas_h = None
                try:
                    canvas_w = getattr(scene, 'canvas_width', None)
                    canvas_h = getattr(scene, 'canvas_height', None)
                except Exception:
                    canvas_w = None
                    canvas_h = None

                new_left = orig_left
                new_top = orig_top
                new_right = orig_right
                new_bottom = orig_bottom

                if self.side == 'left':
                    dx = delta.x()
                    new_left = orig_left + dx
                    # огранчение по min width
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
                try:
                    if self.slot.image_item is not None:
                        self.slot.accept_image(self.slot.image_item)
                except Exception:
                    pass

                event.accept()

            def mouseReleaseEvent(self, event):
                event.accept()

        # Создаём 4-х маркеров
        sides = ['left', 'right', 'top', 'bottom']
        for s in sides:
            h = _Handle(self, s)
            self._handles[s] = h

        self._update_handles()

    def hoverEnterEvent(self, event):
        # показываем маркеры при наведении (если не отключено на сцене)
        scene = self.scene()
        suppress = False
        try:
            suppress = bool(getattr(scene, 'suppress_visuals', False))
        except Exception:
            suppress = False

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
        scene = self.scene()
        suppress = False
        try:
            suppress = bool(getattr(scene, 'suppress_visuals', False))
        except Exception:
            suppress = False

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
