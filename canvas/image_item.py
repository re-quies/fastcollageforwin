import logging
import time

from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsSimpleTextItem
from PySide6.QtGui import QPen, QPixmap, QFont, QColor, QPainter
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer

import i18n

logger = logging.getLogger(__name__)


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

        # ====== PAN INSIDE SLOT ======
        # Смещение относительно центра слота (панорамирование внутри слота).
        # Ограничивается в TemplateSlotItem.position_image так, чтобы слот
        # всегда оставался полностью покрыт изображением.
        self.slot_offset = QPointF(0, 0)
        self._slot_panning = False
        self._slot_pan_last = None
        self._slot_pan_old_offset = None

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
        self._hover_timer.setInterval(500)  # ms
        self._hover_timer.timeout.connect(self._on_hover_timeout)
        self._hover_candidate_slot = None
        self._swap_done = False
        # Visual countdown indicator
        self._hover_countdown_timer = None
        self._hover_indicator = None
        self._hover_end_ts = None
        self._hover_indicator_interval = 100  # ms
        self._hover_ready = False

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
        source = self._visible_source_rect()

        painter.save()
        if self.mirrored_horizontal or self.mirrored_vertical:
            cx = target.width() / 2
            cy = target.height() / 2
            painter.translate(cx, cy)
            painter.scale(
                -1.0 if self.mirrored_horizontal else 1.0,
                -1.0 if self.mirrored_vertical else 1.0,
            )
            painter.translate(-cx, -cy)
        painter.drawPixmap(target, self.original_pixmap, source)
        painter.restore()

        # Рисуем рамку выделения только если сцена не просит скрыть визуалы
        scene = self.scene()
        suppress = bool(getattr(scene, 'suppress_visuals', False))

        if self.isSelected() and not suppress:
            pen = QPen(Qt.blue, 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(self.boundingRect())

    def _visible_source_rect(self) -> QRectF:
        """Видимое окно оригинала с учётом зума и центра (в координатах
        original_pixmap). Окно ограничено границами изображения."""
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

    def mirror_image(self, axis: str):
        """Применяем зеркалирование изображения (без потери качества)"""
        if axis == 'horizontal':
            self.mirrored_horizontal = not self.mirrored_horizontal
        elif axis == 'vertical':
            self.mirrored_vertical = not self.mirrored_vertical

        self.update()

    # ---------- Project serialization ----------

    def view_state(self) -> dict:
        """Состояние отображения содержимого (для сохранения проекта)."""
        return {
            "zoom_factor": self.zoom_factor,
            "zoom_center": [self.zoom_center.x(), self.zoom_center.y()],
            "mirrored_horizontal": self.mirrored_horizontal,
            "mirrored_vertical": self.mirrored_vertical,
            "slot_offset": [self.slot_offset.x(), self.slot_offset.y()],
        }

    def apply_view_state(self, state: dict):
        """Восстановить состояние отображения (при загрузке проекта)."""
        self.zoom_factor = float(state.get("zoom_factor", 1.0))

        center = state.get("zoom_center")
        if isinstance(center, (list, tuple)) and len(center) == 2:
            self.zoom_center = QPointF(float(center[0]), float(center[1]))

        offset = state.get("slot_offset")
        if isinstance(offset, (list, tuple)) and len(offset) == 2:
            self.slot_offset = QPointF(float(offset[0]), float(offset[1]))

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

            # Панорамирование содержимого: только сдвиг центра +
            # перерисовка — без копирования пиксмапа на каждый mouse move
            self.zoom_center -= delta / self.zoom_factor
            self._clamp_zoom_center()
            self.update()
            event.accept()
            return

        super().mouseMoveEvent(event)

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

    def mouseReleaseEvent(self, event):
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

        self._panning = False
        self._last_mouse_pos = None
        super().mouseReleaseEvent(event)

        # Останавливаем таймер, если он активен (быстрый бросок)
        if self._hover_timer.isActive():
            self._hover_timer.stop()

        scene = self.scene()
        if scene is None:
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
        """Выполнить перемещение/обмен в слоте через undo-стек (пункт 4)."""
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
