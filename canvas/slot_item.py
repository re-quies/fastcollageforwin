import logging
import math

from PySide6.QtWidgets import QGraphicsRectItem
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtCore import Qt, QRectF, QPointF

logger = logging.getLogger(__name__)

# Размер маркеров изменения размера слота в ЭКРАННЫХ пикселях.
#
# ФИКС: раньше маркер был квадратом 24×24 в координатах СЦЕНЫ,
# то есть его экранный размер зависел от масштаба просмотра:
# на холсте 4000×3000, открытом в масштабе 15%, в маркер размером
# три пикселя попасть было невозможно, а на 400% он закрывал
# полслота. Теперь маркеры не масштабируются вместе с холстом
# (ItemIgnoresTransformations) и всегда выглядят одинаково.
HANDLE_THICKNESS = 10
HANDLE_LENGTH = 28


def _to_float(value, default):
    """Значения оформления приходят из JSON/QSettings — тип не гарантирован."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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

        # Желаемая видимость маркеров (хранится отдельно от фактической).
        # ФИКС: раньше _update_handles вычислял видимость как
        # `h.isVisible() and not suppress`, то есть мог только гасить
        # маркеры: после экспорта восстановить их было нечем.
        self._handles_visible = False

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
        self._set_handles_visible(on)

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
        """Отрисовка самого слота (рамка и заливка).

        ФИКС: при экспорте (suppress_visuals) слот вообще не рисует себя.
        Раньше гасился только пунктир выделения, а служебная чёрная
        рамка слота (setPen(QPen(Qt.black, 1))) попадала в готовый файл:
        на экспортированном коллаже оставались тонкие темные линии по
        границам всех слотов. Изображение внутри слота — отдельный
        дочерний элемент, сцена рисует его независимо, поэтому сам
        коллаж от этого не страдает.
        """
        if bool(getattr(self.scene(), 'suppress_visuals', False)):
            return

        # Рамка и подсветка рисуются по ВНУТРЕННЕЙ области слота,
        # чтобы были видны реальные отступы и скругления
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawPath(self.content_path())

    # ---------- Внутренняя область (отступы и скругления) ----------

    def content_rect(self) -> QRectF:
        """Область под изображение: rect минус половина отступа.

        Отступ делится пополам между соседями, поэтому зазор между
        двумя слотами равен заданному значению, а у края холста
        получается половинная рамка. Геометрия самого слота не
        меняется — отступ можно крутить туда-сюда без потери
        раскладки, а клики и drop по-прежнему ловятся по всей ячейке.
        """
        gutter = _to_float(getattr(self.scene(), "gutter", 0.0), 0.0)
        rect = self.rect()

        inset = max(0.0, gutter / 2.0)
        inset = min(
            inset,
            max(0.0, (rect.width() - 1.0) / 2.0),
            max(0.0, (rect.height() - 1.0) / 2.0),
        )

        return rect.adjusted(inset, inset, -inset, -inset)

    def corner_radius(self) -> float:
        """Скругление, ограниченное половиной меньшей стороны."""
        radius = _to_float(getattr(self.scene(), "corner_radius", 0.0), 0.0)
        rect = self.content_rect()

        return max(0.0, min(radius, min(rect.width(), rect.height()) / 2.0))

    def content_path(self) -> QPainterPath:
        """Контур внутренней области (по нему обрезается картинка)."""
        rect = self.content_rect()
        radius = self.corner_radius()

        path = QPainterPath()
        if radius > 0:
            path.addRoundedRect(rect, radius, radius)
        else:
            path.addRect(rect)

        return path

    def accept_image(self, image_item, keep_offset: bool = False):
        """Разместить `image_item` как дочерний элемент слота.

        Изображение масштабируется под слот (cover) и позиционируется
        по центру с учётом пользовательского смещения `slot_offset`
        (панорамирование внутри слота). Смещение сбрасывается только
        когда изображение попадает в ДРУГОЙ слот (или приходит извне),
        поэтому случайный клик/бросок в тот же слот больше не центрирует.

        `keep_offset=True` нужен при ВОССТАНОВЛЕНИИ состояния
        (открытие проекта, отмена удаления): там изображение ещё не
        привязано к слоту (parentItem() is None), и проверка ниже
        приняла бы его за «гостя из другого слота».
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
        if prev is not self and not keep_offset:
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
        # Изображение вписывается во ВНУТРЕННЮЮ область слота
        # (rect минус отступ): так между картинками появляется зазор,
        # а сетка слотов остаётся прежней
        slot_rect = self.content_rect()  # локальные координаты слота
        pw = image_item.original_pixmap.width()
        ph = image_item.original_pixmap.height()

        if pw <= 0 or ph <= 0:
            # Иначе картинка просто не встаёт в ячейку без всякого следа
            logger.warning(
                "Slot %s: empty pixmap (%dx%d), image not placed",
                self.index,
                pw,
                ph,
            )
            return

        # Поворот содержимого внутри слота (в градусах, по часовой стрелке)
        angle = _to_float(getattr(image_item, "slot_rotation", 0.0), 0.0)
        rad = math.radians(angle)
        cos_a = abs(math.cos(rad))
        sin_a = abs(math.sin(rad))

        # Габариты слота в системе координат ПОВЁРНУТОЙ картинки: именно
        # их и нужно перекрыть, иначе после поворота в углах слота
        # появятся пустые треугольники
        need_w = slot_rect.width() * cos_a + slot_rect.height() * sin_a
        need_h = slot_rect.width() * sin_a + slot_rect.height() * cos_a

        # Масштабируем так, чтобы картинка покрывала слот (cover)
        scale = max(need_w / pw, need_h / ph)
        image_item.setScale(scale)

        w_scaled = pw * scale
        h_scaled = ph * scale

        # Крутим и масштабируем вокруг центра картинки: так cover-масштаб
        # и допустимое смещение считаются симметрично, а слот не съезжает
        image_item.setTransformOriginPoint(pw / 2.0, ph / 2.0)
        image_item.setRotation(angle)

        # Допустимое смещение считаем вдоль осей самой картинки —
        # именно по ним она и выходит за края слота
        max_du = max(0.0, (w_scaled - need_w) / 2)
        max_dv = max(0.0, (h_scaled - need_h) / 2)

        offset = getattr(image_item, "slot_offset", QPointF(0, 0))

        # Смещение хранится в координатах слота (тянем мышь вправо —
        # картинка едет вправо), поэтому для ограничения переводим его
        # в систему картинки и возвращаем обратно
        cos_r = math.cos(rad)
        sin_r = math.sin(rad)

        u = offset.x() * cos_r + offset.y() * sin_r
        v = -offset.x() * sin_r + offset.y() * cos_r

        u = max(-max_du, min(u, max_du))
        v = max(-max_dv, min(v, max_dv))

        off_x = u * cos_r - v * sin_r
        off_y = u * sin_r + v * cos_r

        # Сохраняем уже ограниченное смещение
        # (важно для undo и сохранения проекта)
        image_item.slot_offset = QPointF(off_x, off_y)

        # Позиционируем по центру внутренней области: при ненулевом
        # отступе её центр смещён относительно (0,0) слота
        center = slot_rect.center()
        image_item.setPos(
            center.x() + off_x - pw / 2.0,
            center.y() + off_y - ph / 2.0,
        )

    def remove_image(self):
        if self.image_item:
            # Отсоединяем связь, но не удаляем сам объект
            # (удаление/перемещение обрабатывается снаружи)
            self.image_item = None

    def _create_handles(self):
        # Вспомогательный класс для маркера
        class _Handle(QGraphicsRectItem):
            def __init__(self, parent_slot, side):
                super().__init__(
                    0, 0, HANDLE_THICKNESS, HANDLE_THICKNESS, parent_slot
                )
                self.slot = parent_slot
                self.side = side  # 'left','right','top','bottom'
                self.setBrush(QBrush(QColor(200, 200, 200)))
                self.setPen(QPen(Qt.darkGray, 1))
                self.setZValue(1000)
                self.setVisible(False)
                self.setFlag(QGraphicsRectItem.ItemIsMovable, False)

                # ФИКС: размер маркера задан в пикселях экрана и больше
                # не зависит от масштаба просмотра и размера холста
                self.setFlag(
                    QGraphicsRectItem.ItemIgnoresTransformations, True
                )

                self.setAcceptedMouseButtons(Qt.LeftButton)

                # Курсор сразу показывает, что границу можно тянуть
                self.setCursor(
                    Qt.SizeHorCursor if side in ('left', 'right')
                    else Qt.SizeVerCursor
                )

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
                # ФИКС: ресайз слота теперь попадает в undo-стек одной
                # командой на весь жест (а не на каждый mouse move).
                orig_rect = getattr(self, "_orig_rect", None)
                orig_pos = getattr(self, "_orig_pos", None)
                self._orig_rect = None
                self._orig_pos = None

                if orig_rect is None or orig_pos is None:
                    event.accept()
                    return

                new_rect = QRectF(self.slot.rect())
                new_pos = QPointF(self.slot.pos())

                if orig_rect == new_rect and orig_pos == new_pos:
                    event.accept()
                    return

                undo_stack = self.slot.undo_stack()
                if undo_stack is not None:
                    from undo.commands import ResizeSlotCommand

                    undo_stack.push(
                        ResizeSlotCommand(
                            self.slot, orig_pos, orig_rect, new_pos, new_rect
                        )
                    )

                event.accept()

        # Создаём 4 маркера
        sides = ['left', 'right', 'top', 'bottom']
        for s in sides:
            h = _Handle(self, s)
            self._handles[s] = h

        self._update_handles()

    def hoverEnterEvent(self, event):
        # показываем маркеры при наведении (если не отключено на сцене)
        self._set_handles_visible(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        # скрываем маркеры при уходе курс��ра
        self._set_handles_visible(False)
        super().hoverLeaveEvent(event)

    # ---------- Маркеры-ползунки ----------

    def undo_stack(self):
        """Undo-стек главного окна (или None, если недоступен)."""
        scene = self.scene()
        if scene is None or not scene.views():
            return None

        window = scene.views()[0].window()
        return getattr(window, "undo_stack", None)

    def _set_handles_visible(self, visible: bool):
        """Запомнить желаемую видимость маркеров и применить её."""
        self._handles_visible = bool(visible)
        self._apply_handles_visibility()

    def _apply_handles_visibility(self):
        """Применить видимость с учётом режима экспорта.

        Желаемое состояние хранится в _handles_visible, поэтому после
        экспорта маркеры корректно возвращаются.
        """
        suppress = bool(getattr(self.scene(), 'suppress_visuals', False))
        visible = self._handles_visible and not suppress

        for handle in self._handles.values():
            handle.setVisible(visible)

    def _update_handles(self):
        """Расставить маркеры по серединам сторон слота.

        Позиция — в локальных координатах слота (Qt сам переведёт
        её в экранные), а размер — в пикселях экрана
        (ItemIgnoresTransformations). Прямоугольник сдвинут целиком
        ВНУТРЬ слота: у слота включён ItemClipsChildrenToShape,
        и маркер по центру границы был бы обрезан наполовину.
        """
        r = self.rect()

        thickness = float(HANDLE_THICKNESS)
        length = float(HANDLE_LENGTH)

        # side -> (точка привязки, прямоугольник относительно точки)
        geometry = {
            'left': (
                QPointF(r.left(), r.center().y()),
                QRectF(0.0, -length / 2.0, thickness, length),
            ),
            'right': (
                QPointF(r.right(), r.center().y()),
                QRectF(-thickness, -length / 2.0, thickness, length),
            ),
            'top': (
                QPointF(r.center().x(), r.top()),
                QRectF(-length / 2.0, 0.0, length, thickness),
            ),
            'bottom': (
                QPointF(r.center().x(), r.bottom()),
                QRectF(-length / 2.0, -thickness, length, thickness),
            ),
        }

        for side, (point, rect) in geometry.items():
            handle = self._handles.get(side)
            if handle is None:
                continue

            handle.setRect(rect)
            handle.setPos(point)

        # Видимость — отдельно от позиционирования
        self._apply_handles_visibility()
