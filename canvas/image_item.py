import logging
import math
import time

from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsSimpleTextItem
from PySide6.QtGui import QPen, QPixmap, QFont, QColor, QPainter
from PySide6.QtCore import Qt, QRect, QRectF, QPointF, QTimer

from core import image_cache
import i18n

logger = logging.getLogger(__name__)


def _global_pos(event):
    """Глобальная позиция курсора для события сцены (QPoint)."""
    pos = event.screenPos()
    return pos.toPoint() if hasattr(pos, "toPoint") else pos


def _normalize_angle(angle) -> float:
    """Угол в диапазоне (-180; 180] — чтобы не копить обороты."""
    try:
        angle = float(angle) % 360.0
    except (TypeError, ValueError):
        return 0.0

    return angle - 360.0 if angle > 180.0 else angle


class ImageItem(QGraphicsPixmapItem):
    def __init__(self, pixmap: QPixmap, source_path=None):
        super().__init__(pixmap)

        # ====== ОРИГИНАЛ ======
        self.original_pixmap = pixmap
        self.base_size = pixmap.size()

        # Путь к исходному файлу (для панели превью и сохранения проекта)
        self.source_path = source_path

        # ====== ZOOM CONTENT ======
        self.zoom_factor = 1.0
        self.zoom_center = QPointF(
            pixmap.width() / 2,
            pixmap.height() / 2
        )

        # ====== PAN STATE ======
        self._panning = False
        self._last_mouse_pos = None
        # Кадрирование до начала сдвига мышью (режим Z):
        # нужно, чтобы записать весь жест в undo одной командой
        self._pan_view_start = None

        # ====== PAN INSIDE SLOT ======
        # Смещение относительно центра слота (панорамирование внутри слота).
        # Ограничивается в TemplateSlotItem.position_image так, чтобы слот
        # всегда оставался полностью покрыт изображением.
        self.slot_offset = QPointF(0, 0)
        self._slot_panning = False
        self._slot_pan_last = None
        self._slot_pan_old_offset = None

        # ====== ROTATION INSIDE SLOT ======
        # Поворот содержимого внутри слота, в градусах. Хранится
        # отдельно от rotation() свободного элемента: в слоте картинка
        # после поворота заново вписывается по cover
        # (см. TemplateSlotItem.position_image), чтобы не появлялись пустые углы.
        self.slot_rotation = 0.0
        self._slot_rotating = False
        self._slot_rotate_start_angle = 0.0
        self._slot_rotate_old_angle = 0.0

        # ====== FLAGS ======
        self.setFlags(
            QGraphicsPixmapItem.ItemIsMovable
            | QGraphicsPixmapItem.ItemIsSelectable
            | QGraphicsPixmapItem.ItemSendsGeometryChanges
        )

        self.setAcceptHoverEvents(True)
        self.setTransformOriginPoint(self.boundingRect().center())

        # ====== СОСТОЯНИЕ ДО ПЕРЕТАСКИВАНИЯ ======
        self._old_pos = self.pos()
        self._old_scale = self.scale()
        self._old_rotation = self.rotation()
        self._old_parent_slot = None

        # Флаги для зеркалирования
        self.mirrored_horizontal = False
        self.mirrored_vertical = False

        # Hover-swap timer
        self._hover_timer = QTimer()
        self._hover_timer.setSingleShot(True)
        # 0 мс = без задержки (умолчание приложения);
        # реальное значение приходит из scene.swap_delay_ms
        self._hover_timer.setInterval(0)  # ms
        self._hover_timer.timeout.connect(self._on_hover_timeout)
        self._hover_candidate_slot = None
        self._swap_done = False
        # Visual countdown indicator
        self._hover_countdown_timer = None
        self._hover_indicator = None
        self._hover_end_ts = None
        self._hover_indicator_interval = 100  # ms
        self._hover_ready = False

        # Панель превью, над которой сейчас тащат изображение
        # (обратный drag&drop: холст → превью)
        self._preview_drop_target = None

    # ---------- Helpers ----------

    def _window(self):
        """Главное окно приложения (или None, если недоступно)."""
        scene = self.scene()
        if scene is None or not scene.views():
            return None
        return scene.views()[0].window()

    def _find_slot_at(self, scene, scene_pos):
        """Найти слот шаблона под точкой сцены."""
        from canvas.slot_item import TemplateSlotItem

        for it in scene.items(scene_pos):
            if isinstance(it, TemplateSlotItem):
                return it
        return None

    def _preview_panel_at(self, global_pos):
        """Панель превью, если курсор находится над ней.

        Во время перетаскивания элемента мышь захвачена QGraphicsView,
        поэтому сама панель событий не получает — попадание
        определяем по глобальным координатам курсора.
        """
        window = self._window()
        if window is None:
            return None

        panel = getattr(window, "preview_panel", None)
        if panel is None or not panel.isVisible():
            return None

        rect = QRect(
            panel.mapToGlobal(panel.rect().topLeft()),
            panel.rect().size(),
        )
        return panel if rect.contains(global_pos) else None

    def _set_preview_drop_target(self, panel):
        """Подсветить панель превью как цель броска."""
        if panel is self._preview_drop_target:
            return

        previous = self._preview_drop_target
        if previous is not None and hasattr(previous, "set_drop_highlight"):
            previous.set_drop_highlight(False)

        self._preview_drop_target = panel

        if panel is not None and hasattr(panel, "set_drop_highlight"):
            panel.set_drop_highlight(True)

    def _drop_into_preview(self, scene):
        """Обратный drag&drop: изображение уходит с холста в панель.

        Используется та же атомарная команда, что и для клавиши X,
        поэтому Ctrl+Z возвращает фото на прежнее место (в слот
        шаблона или в свободную позицию ДО начала перетаскивания).
        """
        from undo.commands import ReturnToPreviewCommand

        window = self._window()
        if window is not None and hasattr(window, "undo_stack"):
            window.undo_stack.push(
                ReturnToPreviewCommand(
                    scene,
                    window,
                    self,
                    self._old_pos,
                    self._old_scale,
                    self._old_rotation,
                )
            )
        else:
            self._return_to_preview(scene)

    def _clear_hover_indicator(self):
        if self._hover_countdown_timer is not None:
            self._hover_countdown_timer.stop()
            self._hover_countdown_timer = None

        if self._hover_indicator is not None:
            scene = self.scene()
            if scene is not None and self._hover_indicator.scene() is scene:
                scene.removeItem(self._hover_indicator)
            self._hover_indicator = None

        self._hover_end_ts = None

    def paint(self, painter, option, widget=None):
        """Отрисовка без потери качества.

        ФИКС: раньше зум содержимого и зеркалирование "выпекались"
        в новый пиксмап (кроп + растяжка обратно до base_size), из-за
        чего терялись реальные пиксели — мыло попадало и в экспорт
        (scene.render рисовал уже деградированную версию). Теперь зум
        и зеркалирование — параметры отрисовки: видимое окно оригинала
        (sourceRect) рисуется напрямую в boundingRect элемента, а QPainter
        интерполирует в разрешении текущего вывода (экран/экспорт).
        Бонус: панорамирование больше не копирует пиксмап на каждый
        mouse move и движется субпиксельно плавно (QRectF без округления).
        """
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        target = QRectF(
            0, 0, self.base_size.width(), self.base_size.height()
        )
        pixmap, source = self._paint_source()

        painter.save()

        # Обрезка по внутренней области слота: отступы и скруглённые
        # углы. Сам слот продолжает клиповать детей по своему
        # прямоугольнику — тонкую форму задаём здесь, иначе
        # маркеры-ползунки слота обрезались бы вместе с картинкой.
        clip = self._slot_clip_path()
        if clip is not None:
            painter.setClipPath(clip)

        if self.mirrored_horizontal or self.mirrored_vertical:
            cx = target.width() / 2
            cy = target.height() / 2
            painter.translate(cx, cy)
            painter.scale(
                -1.0 if self.mirrored_horizontal else 1.0,
                -1.0 if self.mirrored_vertical else 1.0,
            )
            painter.translate(-cx, -cy)
        painter.drawPixmap(target, pixmap, source)
        painter.restore()

        # Рисуем рамку выделения только если сцена не просит скрыть визуалы
        scene = self.scene()
        suppress = bool(getattr(scene, 'suppress_visuals', False))

        if self.isSelected() and not suppress:
            pen = QPen(Qt.blue, 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(self.boundingRect())

    def _slot_clip_path(self):
        """Контур слота-родителя в координатах этого элемента.

        Возвращает None для свободных изображений (вне слота) —
        их ничего не обрезает.
        """
        from canvas.slot_item import TemplateSlotItem

        parent = self.parentItem()
        if not isinstance(parent, TemplateSlotItem):
            return None

        return self.mapFromParent(parent.content_path())

    def _paint_source(self):
        """Пиксмап и видимое окно в его координатах для отрисовки.

        На холсте живёт уменьшенная «рабочая» копия (см. core.image_cache):
        полноразмерные фото съедают сотни мегабайт, а экрану такое
        разрешение не нужно. А вот на экспорте (suppress_visuals)
        берём оригинал с диска и пересчитываем sourceRect в его
        координаты — итоговый файл собирается из полного разрешения.
        """
        source = self._visible_source_rect()
        working = self.original_pixmap

        scene = self.scene()
        if not bool(getattr(scene, "suppress_visuals", False)):
            return working, source

        if not self.source_path or working.isNull():
            return working, source

        full = image_cache.load_full_pixmap(self.source_path)
        if full.isNull() or full.size() == working.size():
            return working, source

        kx = full.width() / working.width()
        ky = full.height() / working.height()

        return full, QRectF(
            source.x() * kx,
            source.y() * ky,
            source.width() * kx,
            source.height() * ky,
        )

    def _visible_source_rect(self) -> QRectF:
        """Видимое окно оригинала с учётом зума и центра (в координатах
        original_pixmap). Окно ограничено граница���� изображения."""
        w = self.original_pixmap.width() / self.zoom_factor
        h = self.original_pixmap.height() / self.zoom_factor

        x = self.zoom_center.x() - w / 2
        y = self.zoom_center.y() - h / 2

        x = max(0.0, min(x, self.original_pixmap.width() - w))
        y = max(0.0, min(y, self.original_pixmap.height() - h))

        return QRectF(x, y, w, h)

    def _clamp_zoom_center(self):
        """Удерживаем центр зума в допустимых пределах, чтобы окно
        не выходило за границы и не накапливался "мёртвый ход"
        при панорамировании у края."""
        w = self.original_pixmap.width() / self.zoom_factor
        h = self.original_pixmap.height() / self.zoom_factor

        self.zoom_center = QPointF(
            max(w / 2, min(
                self.zoom_center.x(),
                self.original_pixmap.width() - w / 2,
            )),
            max(h / 2, min(
                self.zoom_center.y(),
                self.original_pixmap.height() - h / 2,
            )),
        )

    def zoom_content(self, factor: float):
        """Масштабируем содержимое (без пересоздания пиксмапа)"""
        self.zoom_factor = max(1.0, min(self.zoom_factor * factor, 8.0))
        self._clamp_zoom_center()
        self.update()

    def content_view_state(self):
        """Текущее кадрирование содержимого: (зум, центр).

        Используется undo-командами: жесты режима Z меняют
        только эти два значения.
        """
        return float(self.zoom_factor), QPointF(self.zoom_center)

    def apply_content_view(self, zoom, center):
        """Восстановить кадрирование содержимого (undo/redo)."""
        self.zoom_factor = max(1.0, min(float(zoom), 8.0))
        self.zoom_center = QPointF(center)
        self._clamp_zoom_center()
        self.update()

    def mirror_image(self, axis: str):
        """Применяем зеркалирование изображения (без потери качества)"""
        if axis == 'horizontal':
            self.mirrored_horizontal = not self.mirrored_horizontal
        elif axis == 'vertical':
            self.mirrored_vertical = not self.mirrored_vertical

        self.update()

    # ---------- Поворот внутри слота ----------

    def _parent_slot(self):
        """Родительский слот шаблона (или None для свободного элемента)."""
        from canvas.slot_item import TemplateSlotItem

        parent = self.parentItem()
        return parent if isinstance(parent, TemplateSlotItem) else None

    def _cursor_angle(self, scene_pos) -> float:
        """Угол курсора относительно центра слота, в градусах."""
        slot = self._parent_slot()
        if slot is None:
            return 0.0

        center = slot.mapToScene(slot.content_rect().center())
        delta = scene_pos - center

        return math.degrees(math.atan2(delta.y(), delta.x()))

    def _drag_rotate(self, event):
        """Поворот перетаскиванием: картинка следует за курсором.

        С зажатым Shift угол прилипает к шагу 15° — так проще выровнять
        завалившийся горизонт или поставить ровные 90°.
        """
        slot = self._parent_slot()
        if slot is None:
            return

        delta = self._cursor_angle(event.scenePos()) - self._slot_rotate_start_angle
        new_angle = self._slot_rotate_old_angle + delta

        if event.modifiers() & Qt.ShiftModifier:
            new_angle = round(new_angle / 15.0) * 15.0

        self.slot_rotation = _normalize_angle(new_angle)
        slot.position_image(self)

    def _finish_slot_rotation(self):
        """Завершить жест поворота и записать его в историю одним шагом."""
        self._slot_rotating = False
        old_angle = self._slot_rotate_old_angle

        slot = self._parent_slot()
        if slot is None or abs(old_angle - self.slot_rotation) < 1e-6:
            return

        self._push_rotate_command(slot, old_angle, self.slot_rotation)

    def _push_rotate_command(self, slot, old_angle, new_angle):
        """Положить поворот в undo-стек (или применить напрямую)."""
        from undo.commands import RotateInSlotCommand

        command = RotateInSlotCommand(slot, self, old_angle, new_angle)

        window = self._window()
        if window is not None and hasattr(window, "undo_stack"):
            window.undo_stack.push(command)
        else:
            logger.warning(
                "Undo stack is not available; rotation applied without undo"
            )
            command.redo()

    def rotate_in_slot(self, delta_degrees) -> bool:
        """Довернуть содержимое внутри слота (через undo-стек).

        Возвращает False, если изображение не в слоте: тогда вызывающая
        сторона крутит сам элемент сцены.
        """
        slot = self._parent_slot()
        if slot is None:
            return False

        old_angle = float(self.slot_rotation)
        new_angle = _normalize_angle(old_angle + float(delta_degrees))

        if abs(new_angle - old_angle) > 1e-6:
            self._push_rotate_command(slot, old_angle, new_angle)

        return True

    def reset_slot_rotation(self) -> bool:
        """Сбросить поворот содержимого в 0°."""
        slot = self._parent_slot()
        if slot is None:
            return False

        old_angle = float(self.slot_rotation)
        if abs(old_angle) > 1e-6:
            self._push_rotate_command(slot, old_angle, 0.0)

        return True

    # ---------- Project serialization ----------

    def view_state(self) -> dict:
        """Состояние отображения содержимого (для сохранения проекта)."""
        return {
            "zoom_factor": self.zoom_factor,
            "zoom_center": [self.zoom_center.x(), self.zoom_center.y()],
            # Размер рабочей копии: zoom_center хранится в её координатах,
            # а ограничение разрешения может отличаться между версиями
            "base_size": [
                self.original_pixmap.width(),
                self.original_pixmap.height(),
            ],
            "mirrored_horizontal": self.mirrored_horizontal,
            "mirrored_vertical": self.mirrored_vertical,
            "slot_offset": [self.slot_offset.x(), self.slot_offset.y()],
            "slot_rotation": self.slot_rotation,
        }

    def apply_view_state(self, state: dict):
        """Восстановить состояние отображения (при загрузке проекта)."""
        self.zoom_factor = float(state.get("zoom_factor", 1.0))

        center = state.get("zoom_center")
        if isinstance(center, (list, tuple)) and len(center) == 2:
            cx = float(center[0])
            cy = float(center[1])

            # Если рабочая копия загружена в другом разрешении, чем было
            # при сохранении, центр зума пересчитываем пропорционально
            base = state.get("base_size")
            if (
                isinstance(base, (list, tuple))
                and len(base) == 2
                and float(base[0]) > 0
                and float(base[1]) > 0
            ):
                cx *= self.original_pixmap.width() / float(base[0])
                cy *= self.original_pixmap.height() / float(base[1])

            self.zoom_center = QPointF(cx, cy)

        offset = state.get("slot_offset")
        if isinstance(offset, (list, tuple)) and len(offset) == 2:
            self.slot_offset = QPointF(float(offset[0]), float(offset[1]))

        # Проекты, сохранённые до появления поворота, откроются с 0°
        self.slot_rotation = _normalize_angle(state.get("slot_rotation", 0.0))

        self.mirrored_horizontal = bool(state.get("mirrored_horizontal", False))
        self.mirrored_vertical = bool(state.get("mirrored_vertical", False))

        # Пиксмап больше не "выпекается" — зум/зеркалирование
        # применяются при отрисовке (см. paint)
        self._clamp_zoom_center()
        self.update()

    # ---------- Mouse events ----------

    def mousePressEvent(self, event):
        from canvas.slot_item import TemplateSlotItem

        self._old_pos = self.pos()
        self._old_scale = self.scale()
        self._old_rotation = self.rotation()

        # Запоминаем родительский слот (если есть)
        parent = self.parentItem()
        self._old_parent_slot = parent if isinstance(parent, TemplateSlotItem) else None

        # Проверяем режим во View, а не клавишу
        scene = self.scene()
        if scene and scene.views():
            view = scene.views()[0]
            if getattr(view, "content_zoom_mode", False):
                self._panning = True
                self._last_mouse_pos = event.pos()
                # Состояние до жеста — для записи в undo при отпускании
                self._pan_view_start = self.content_view_state()
                event.accept()
                return

            # Поворот содержимого внутри слота: зажата R,
            # картинка следует за курсором вокруг центра слота
            if (
                getattr(view, "slot_rotate_mode", False)
                and self._old_parent_slot is not None
            ):
                self._slot_rotating = True
                self._slot_rotate_old_angle = float(self.slot_rotation)
                self._slot_rotate_start_angle = self._cursor_angle(
                    event.scenePos()
                )
                event.accept()
                return

            # Панорамирование внутри слота: зажата C (или Alt),
            # а изображение находится в слоте шаблона
            slot_pan = (
                getattr(view, "slot_pan_mode", False)
                or bool(event.modifiers() & Qt.AltModifier)
            )
            if slot_pan and self._old_parent_slot is not None:
                self._slot_panning = True
                self._slot_pan_last = event.scenePos()
                self._slot_pan_old_offset = QPointF(self.slot_offset)
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._slot_rotating:
            self._drag_rotate(event)
            event.accept()
            return

        if self._slot_panning:
            from canvas.slot_item import TemplateSlotItem

            parent = self.parentItem()
            if isinstance(parent, TemplateSlotItem):
                # Слот не масштабируется и не поворачивается, поэтому
                # дельта сцены совпадает с дельтой в координатах слота
                delta = event.scenePos() - self._slot_pan_last
                self._slot_pan_last = event.scenePos()
                self.slot_offset = QPointF(
                    self.slot_offset.x() + delta.x(),
                    self.slot_offset.y() + delta.y(),
                )
                parent.position_image(self)
            event.accept()
            return

        if self._panning:
            delta = event.pos() - self._last_mouse_pos
            self._last_mouse_pos = event.pos()

            # ФИКС: зеркалирование применяется на отрисовке
            # (painter.scale(-1)), поэтому по отражённой оси экранное
            # направление противоположно направлению в координатах
            # пиксмапа. Без компенсации после отражения картинка
            # ездила за мышью в обратную сторону.
            dx = -delta.x() if self.mirrored_horizontal else delta.x()
            dy = -delta.y() if self.mirrored_vertical else delta.y()

            # Панорамирование содержимого: только сдвиг центра +
            # перерисовка — без копирования пиксмапа на каждый mouse move
            self.zoom_center -= QPointF(dx, dy) / self.zoom_factor
            self._clamp_zoom_center()
            self.update()
            event.accept()
            return

        super().mouseMoveEvent(event)

        # Обратный drag&drop: подсвечиваем панель превью, когда
        # изображение тащат на неё
        preview_panel = self._preview_panel_at(_global_pos(event))
        self._set_preview_drop_target(preview_panel)

        # Над панелью логика слотов не нужна: гасим обратный отсчёт,
        # чтобы слот не остался "взведённым" на момент отпускания
        if preview_panel is not None:
            if self._hover_timer.isActive():
                self._hover_timer.stop()
            self._clear_hover_indicator()
            if self._hover_candidate_slot is not None:
                self._hover_candidate_slot.set_highlight(False)
                self._hover_candidate_slot = None
            self._hover_ready = False
            return

        # При перемещении отслеживаем, над каким слотом находится курсор —
        # запускаем таймер для swap
        scene = self.scene()
        if not scene or not getattr(scene, "is_template_mode", False):
            return

        from canvas.slot_item import TemplateSlotItem

        cursor_pos = event.scenePos()
        new_slot = self._find_slot_at(scene, cursor_pos)

        # Если у нас был родительский слот и мы всё ещё преимущественно внутри него,
        # игнорируем попадание в соседний слот (требуется >50% вне зоны для смены).
        parent = self.parentItem()
        if (
            isinstance(parent, TemplateSlotItem)
            and new_slot is not None
            and new_slot is not parent
        ):
            item_rect = self.mapToScene(self.boundingRect()).boundingRect()
            parent_rect = QRectF(
                parent.scenePos().x(),
                parent.scenePos().y(),
                parent.rect().width(),
                parent.rect().height(),
            )
            inter = item_rect.intersected(parent_rect)
            inter_area = max(0.0, inter.width() * inter.height())
            item_area = max(1.0, item_rect.width() * item_rect.height())
            # Если пересечение >= 50% — остаёмся в текущем слоте
            if inter_area / item_area >= 0.5:
                new_slot = parent

        if new_slot is not self._hover_candidate_slot:
            # Сменился кандидат — перезапускаем таймер и убираем индикатор
            if self._hover_timer.isActive():
                self._hover_timer.stop()
            self._clear_hover_indicator()

            # Сбрасываем подсветку предыдущего кандидата
            if self._hover_candidate_slot is not None:
                self._hover_candidate_slot.set_highlight(False)

            self._hover_candidate_slot = new_slot
            self._swap_done = False
            self._hover_ready = False

            if new_slot is not None:
                new_slot.set_highlight(True)

                # Если задана пользовательская задержка — используем её
                delay = getattr(scene, 'swap_delay_ms', None)
                if delay is not None:
                    self._hover_timer.setInterval(int(delay))

                if self._hover_timer.interval() <= 0:
                    # Задержка выключена: слот готов сразу, без таймера
                    # и без индикатора обратного отсчёта.
                    #
                    # Полагаться на QTimer с интервалом 0 нельзя: он
                    # срабатывает только на следующем проходе цикла событий,
                    # и быстрое перетаскивание успело бы завершиться раньше,
                    # чем взведётся _hover_ready.
                    self._hover_ready = True
                else:
                    self._hover_timer.start()
                    self._start_hover_indicator(scene, new_slot, cursor_pos)

    def _start_hover_indicator(self, scene, slot, cursor_pos):
        """Создаёт текстовый индикатор обратного отсчёта над слотом."""
        delay = self._hover_timer.interval()
        self._hover_end_ts = int(time.time() * 1000) + int(delay)

        indicator = QGraphicsSimpleTextItem("")
        indicator.setZValue(10000)
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        indicator.setFont(font)
        indicator.setBrush(QColor(255, 60, 60))

        # Позиционируем над слотом (по центру сверху)
        slot_center = slot.scenePos() + QPointF(slot.rect().width() / 2, 0)
        indicator.setPos(slot_center + QPointF(-20, -30))

        scene.addItem(indicator)
        self._hover_indicator = indicator

        ct = QTimer()
        ct.setInterval(self._hover_indicator_interval)

        def _update_indicator():
            if self._hover_end_ts is None:
                ct.stop()
                return

            now = int(time.time() * 1000)
            remaining = max(0, int(self._hover_end_ts - now))

            if self._hover_indicator is not None:
                if remaining > 0:
                    self._hover_indicator.setText(
                        f"{remaining} {i18n.t('ms')}"
                    )
                else:
                    self._hover_indicator.setText(i18n.t('release_to_drop'))

            if remaining <= 0:
                # Пометим, что на этом слоте можно поместить при отпускании
                self._hover_ready = True
                ct.stop()

        ct.timeout.connect(_update_indicator)
        ct.start()
        self._hover_countdown_timer = ct

    def _finish_content_pan(self):
        """Записать сдвиг кадра (режим Z) в историю одним шагом.

        ФИКС: раньше жест менял zoom_center напрямую — выбранное
        кадрирование нельзя было отменить, и окно не считало
        проект изменённым.
        """
        start = self._pan_view_start
        self._pan_view_start = None

        if start is None:
            return

        new_zoom, new_center = self.content_view_state()
        if abs(start[0] - new_zoom) < 1e-6 and start[1] == new_center:
            return

        from undo.commands import ContentViewCommand

        # Жест закончен, поэтому mergeable=False:
        # следующий сдвиг — отдельный шаг Ctrl+Z
        command = ContentViewCommand(
            self, start[0], start[1], new_zoom, new_center, mergeable=False
        )

        window = self._window()
        if window is not None and hasattr(window, "undo_stack"):
            window.undo_stack.push(command)
        else:
            logger.warning(
                "Undo stack is not available; framing applied without undo"
            )

    def mouseReleaseEvent(self, event):
        if self._slot_rotating:
            self._finish_slot_rotation()
            event.accept()
            return

        if self._slot_panning:
            from canvas.slot_item import TemplateSlotItem

            self._slot_panning = False
            self._slot_pan_last = None
            old_offset = self._slot_pan_old_offset
            self._slot_pan_old_offset = None

            parent = self.parentItem()
            if (
                isinstance(parent, TemplateSlotItem)
                and old_offset is not None
                and old_offset != self.slot_offset
            ):
                from undo.commands import PanInSlotCommand

                window = self._window()
                if window is not None and hasattr(window, "undo_stack"):
                    window.undo_stack.push(
                        PanInSlotCommand(
                            parent,
                            self,
                            old_offset,
                            QPointF(self.slot_offset),
                        )
                    )
            event.accept()
            return

        if self._panning:
            self._finish_content_pan()

        self._panning = False
        self._last_mouse_pos = None
        super().mouseReleaseEvent(event)

        # Останавливаем таймер, если он активен (быстрый бросок)
        if self._hover_timer.isActive():
            self._hover_timer.stop()

        scene = self.scene()
        if scene is None:
            self._set_preview_drop_target(None)
            return

        # ОБРАТНЫЙ DRAG&DROP: отпустили над панелью превью —
        # изображение возвращается в панель (аналог клавиши X)
        drop_panel = self._preview_panel_at(_global_pos(event))
        self._set_preview_drop_target(None)

        if drop_panel is not None:
            self._finish_slot_interaction()
            self._drop_into_preview(scene)
            return

        handled = False
        if getattr(scene, "is_template_mode", False):
            handled = self._handle_template_release(event, scene)

        # Обычная трансформация (перемещение/масштаб/поворот) — в undo-стек.
        # Пропускаем, если размещением занималась слот-команда.
        if not handled and (
            self._old_pos != self.pos()
            or self._old_scale != self.scale()
            or self._old_rotation != self.rotation()
        ):
            from undo.commands import TransformCommand

            window = self._window()
            if window is not None and hasattr(window, "undo_stack"):
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

    def _handle_template_release(self, event, scene):
        """Обработка отпускания мыши в шаблонном режиме.

        Возвращает True, если событие обработано логикой слотов
        (в этом случае TransformCommand не создаётся).
        """
        cursor_pos = event.scenePos()
        new_slot = self._find_slot_at(scene, cursor_pos)
        old_slot = self._old_parent_slot

        # 1) Слот под курсором не найден — возвращаемся на место или в превью
        if new_slot is None:
            if old_slot is not None:
                old_slot.accept_image(self)
            else:
                self._return_to_preview(scene)
            self._finish_slot_interaction()
            return True

        # 2) Упали в тот же слот — возвращаем на прежнее место
        #    (accept_image сохраняет slot_offset, поэтому случайный клик
        #    больше не центрирует изображение)
        if new_slot is old_slot:
            new_slot.accept_image(self)
            self._finish_slot_interaction()
            return True

        # ВАЖНО (fix): `other` инициализируется ДО всех веток — раньше при
        # быстром броске в чужой слот возникал UnboundLocalError.
        other = new_slot.image_item
        if other is self:
            other = None

        # 3) Hover подтверждён — выполняем перемещение/обмен через undo-стек
        if self._hover_ready:
            self._push_slot_command(scene, new_slot, old_slot, other)
            self._finish_slot_interaction()
            return True

        # 4) Пустой слот, перемещение между слотами без подтверждения — отмена
        if other is None and old_slot is not None:
            old_slot.accept_image(self)
            self._finish_slot_interaction()
            return True

        # 5) Пустой слот, элемент не был в слоте — размещаем сразу (через undo-стек)
        if other is None and old_slot is None:
            self._push_slot_command(scene, new_slot, None, None)
            self._finish_slot_interaction()
            return True

        # 6) Занятый слот без подтверждённого hover — отмена
        if old_slot is not None:
            old_slot.accept_image(self)
        else:
            self._return_to_preview(scene)
        self._finish_slot_interaction()
        return True

    def _finish_slot_interaction(self):
        """Сброс визуального состояния hover-взаимодействия."""
        self._clear_hover_indicator()
        if self._hover_candidate_slot is not None:
            self._hover_candidate_slot.set_highlight(False)
            self._hover_candidate_slot = None
        self._hover_ready = False
        self._swap_done = False

    def _return_to_preview(self, scene):
        """Вернуть изображение в панель превью и убрать его со сцены."""
        window = self._window()
        if window is not None and hasattr(window, "preview_panel"):
            window.preview_panel.add_pixmap(
                self.original_pixmap, self.source_path
            )
        else:
            logger.warning(
                "Preview panel is not available; image is removed from scene"
            )
        scene.removeItem(self)

    def _push_slot_command(self, scene, new_slot, old_slot, other):
        """Выполнить перемещение/обмен в слоте через undo-с��ек (пункт 4)."""
        from undo.commands import MoveImageToSlotCommand

        window = self._window()
        command = MoveImageToSlotCommand(
            scene, window, self, new_slot, old_slot, other
        )

        if window is not None and hasattr(window, "undo_stack"):
            # push() сразу вызывает redo() — операция выполняется здесь
            window.undo_stack.push(command)
        else:
            logger.warning(
                "Undo stack is not available; slot operation applied without undo"
            )
            command.redo()

    def _on_hover_timeout(self):
        """Таймер выдержки над слотом истёк — «взводим» слот.

        ФИКС: раньше здесь сразу выполнялся swap (accept_image) при ещё
        зажатой кнопке мыши. Стандартный drag-обработчик Qt продолжал
        двигать элемент относительно точки нажатия ДО перепривязки к слоту,
        поэтому любое микродвижение «отбрасывало» изображение далеко за
        границы слота (и оно скрывалось обрезкой ItemClipsChildrenToShape).

        Теперь таймер лишь помечает слот готовым (_hover_ready), а само
        перемещение/обмен выполняется в mouseReleaseEvent при отпускании
        кнопки (ветка `if self._hover_ready` в _handle_template_release).
        Пользователь может передумать и продолжить перетаскивание в другой
        слот — при смене кандидата флаг сбрасывается и таймер
        перезапускается (см. mouseMoveEvent).
        """
        if self._hover_candidate_slot is None:
            return

        self._hover_ready = True

        # Обновляем текст индикатора сразу, не дожидаясь тика
        # countdown-таймера
        if self._hover_indicator is not None:
            self._hover_indicator.setText(i18n.t('release_to_drop'))
