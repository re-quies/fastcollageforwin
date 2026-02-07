from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsSimpleTextItem
from PySide6.QtGui import QPen, QPixmap, QTransform, QFont, QColor
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer
import time


class ImageItem(QGraphicsPixmapItem):
    def __init__(self, pixmap: QPixmap):
        super().__init__(pixmap)

        # ====== ОРИГИНАЛ ======
        self.original_pixmap = pixmap
        self.base_size = pixmap.size()

        # ====== ZOOM CONTENT ======
        self.zoom_factor = 1.0
        self.zoom_center = QPointF(
            pixmap.width() / 2,
            pixmap.height() / 2
        )

        # ====== PAN STATE ======
        self._panning = False
        self._last_mouse_pos = None

        # ====== FLAGS ======
        self.setFlags(
            QGraphicsPixmapItem.ItemIsMovable
            | QGraphicsPixmapItem.ItemIsSelectable
            | QGraphicsPixmapItem.ItemSendsGeometryChanges
        )

        self.setAcceptHoverEvents(True)
        self.setTransformOriginPoint(self.boundingRect().center())

        # ====== ZOOM & MIRROR STATE ======
        self._old_pos = self.pos()
        self._old_scale = self.scale()
        self._old_rotation = self.rotation()

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

    def _clear_hover_indicator(self):
        try:
            if self._hover_countdown_timer is not None:
                try:
                    self._hover_countdown_timer.stop()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if self._hover_indicator is not None:
                scene = self.scene()
                if scene is not None:
                    try:
                        scene.removeItem(self._hover_indicator)
                    except Exception:
                        pass
                self._hover_indicator = None
        except Exception:
            pass

        self._hover_countdown_timer = None
        self._hover_end_ts = None

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)

        if self.isSelected():
            pen = QPen(Qt.blue, 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(self.boundingRect())

    def zoom_content(self, factor: float):
        """Масштабируем содержимое, учитывая зеркалирование"""
        self.zoom_factor = max(1.0, min(self.zoom_factor * factor, 8.0))
        self._update_visible_pixmap()

    def _update_visible_pixmap(self):
        w = self.original_pixmap.width() / self.zoom_factor
        h = self.original_pixmap.height() / self.zoom_factor

        x = self.zoom_center.x() - w / 2
        y = self.zoom_center.y() - h / 2

        x = max(0, min(x, self.original_pixmap.width() - w))
        y = max(0, min(y, self.original_pixmap.height() - h))

        rect = QRectF(x, y, w, h)

        cropped = self.original_pixmap.copy(rect.toRect())

        # Применяем зеркалирование
        if self.mirrored_horizontal:
            cropped = cropped.transformed(QTransform(-1, 0, 0, 1, 0, 0))  # зеркалирование по горизонтали

        if self.mirrored_vertical:
            cropped = cropped.transformed(QTransform(1, 0, 0, -1, 0, 0))  # зеркалирование по вертикали

        # ВАЖНО: возвращаем к исходному размеру
        scaled = cropped.scaled(
            self.base_size,
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation
        )

        self.setPixmap(scaled)

    def mirror_image(self, axis: str):
        """Применяем зеркалирование изображения"""
        if axis == 'horizontal':
            self.mirrored_horizontal = not self.mirrored_horizontal
        elif axis == 'vertical':
            self.mirrored_vertical = not self.mirrored_vertical

        self._update_visible_pixmap()

    def mousePressEvent(self, event):
        self._old_pos = self.pos()
        self._old_scale = self.scale()
        self._old_rotation = self.rotation()
        # Track parent slot (если есть)
        parent = self.parentItem()
        self._old_parent_slot = parent if parent and type(parent).__name__ == 'TemplateSlotItem' else None

        # Проверяем режим во View, а не клавишу
        scene = self.scene()
        if scene and scene.views():
            view = scene.views()[0]
            if getattr(view, "content_zoom_mode", False):
                self._panning = True
                self._last_mouse_pos = event.pos()
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._last_mouse_pos
            self._last_mouse_pos = event.pos()

            self.zoom_center -= delta / self.zoom_factor
            self._update_visible_pixmap()
            event.accept()
            return

        super().mouseMoveEvent(event)

        # При перемещении отслеживаем, над каким слотом находится центр — запускаем таймер для swap
        scene = self.scene()
        if not scene or not getattr(scene, "is_template_mode", False):
            return

        # Используем позицию курсора в сцене, чтобы определять слот-кандидат.
        try:
            cursor_pos = event.scenePos()
        except Exception:
            cursor_pos = self.mapToScene(self.boundingRect().center())
        items = scene.items(cursor_pos)
        new_slot = None
        from canvas.slot_item import TemplateSlotItem
        for it in items:
            if isinstance(it, TemplateSlotItem):
                new_slot = it
                break

        # Если у нас был родительский слот и мы всё ещё преимущественно внутри него,
        # игнорируем попадание в соседний слот (требуется >50% вне зоны для смены).
        try:
            parent = self.parentItem()
            if parent is not None and type(parent).__name__ == 'TemplateSlotItem' and new_slot is not None and new_slot is not parent:
                # Рассчитываем площадь пересечения в координатах сцены
                item_rect = self.mapToScene(self.boundingRect()).boundingRect()
                parent_rect = QRectF(parent.scenePos().x(), parent.scenePos().y(), parent.rect().width(), parent.rect().height())
                inter = item_rect.intersected(parent_rect)
                inter_area = max(0.0, inter.width() * inter.height())
                item_area = max(1.0, item_rect.width() * item_rect.height())
                # Если пересечение >= 50% — остаёмся в текущем слоте
                if inter_area / item_area >= 0.5:
                    new_slot = parent
        except Exception:
            pass

        if new_slot is not self._hover_candidate_slot:
            # сменился кандидат — перезапускаем таймер
            if self._hover_timer.isActive():
                self._hover_timer.stop()
            # остановим и уберём визуальный индикатор
            try:
                self._clear_hover_indicator()
            except Exception:
                pass

            # Сбрасываем подсветку предыдущего кандидата
            try:
                if self._hover_candidate_slot is not None:
                    self._hover_candidate_slot.set_highlight(False)
            except Exception:
                pass

            self._hover_candidate_slot = new_slot
            self._swap_done = False
            # сброс готовности на новый кандидат
            self._hover_ready = False

            if new_slot is not None:
                try:
                    new_slot.set_highlight(True)
                except Exception:
                    pass
                # если для элемента задана пользовательская задержка — используем её
                try:
                    scene = self.scene()
                    delay = getattr(scene, 'swap_delay_ms', None)
                    if delay is not None:
                        self._hover_timer.setInterval(delay)
                except Exception:
                    pass

                # Запускаем hover-timer и визуальный индикатор
                self._hover_timer.start()

                try:
                    # Настроим финальную метку времени
                    delay = self._hover_timer.interval()
                    self._hover_end_ts = int(time.time() * 1000) + int(delay)

                    # Создаём текстовый индикатор и добавим в сцену поверх всего
                    scene = self.scene()
                    if scene is not None:
                        indicator = QGraphicsSimpleTextItem("")
                        indicator.setZValue(10000)
                        # Больший шрифт для лучшей видимости
                        try:
                            font = QFont()
                            font.setPointSize(14)
                            font.setBold(True)
                            indicator.setFont(font)
                            indicator.setBrush(QColor(255, 60, 60))
                        except Exception:
                            pass

                        # позиционируем над слотом (по центру сверху)
                        try:
                            slot_center = new_slot.scenePos() + QPointF(new_slot.rect().width() / 2, 0)
                            indicator.setPos(slot_center + QPointF(-20, -30))
                        except Exception:
                            indicator.setPos(cursor_pos + QPointF(-20, -30))

                        scene.addItem(indicator)
                        self._hover_indicator = indicator

                        # Создаём таймер обновления индикатора
                        ct = QTimer()
                        ct.setInterval(self._hover_indicator_interval)

                        def _update_indicator():
                            try:
                                now = int(time.time() * 1000)
                                remaining = max(0, int(self._hover_end_ts - now))
                                if self._hover_indicator is not None:
                                    self._hover_indicator.setText(f"{remaining} ms")
                                if remaining <= 0:
                                    try:
                                        ct.stop()
                                    except Exception:
                                        pass
                                    # при достижении нуля пометить готовность — но не выполнять перемещение
                                    try:
                                        # пометим, что на этом слоте можно поместить при отпускании
                                        self._hover_ready = True
                                        if self._hover_indicator is not None:
                                            self._hover_indicator.setText("Release to drop")
                                        ct.stop()
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                        ct.timeout.connect(_update_indicator)
                        ct.start()
                        self._hover_countdown_timer = ct
                except Exception:
                    pass

    def mouseReleaseEvent(self, event):
        self._panning = False
        self._last_mouse_pos = None
        super().mouseReleaseEvent(event)

        if (
            self._old_pos != self.pos()
            or self._old_scale != self.scale()
            or self._old_rotation != self.rotation()
        ):
            from undo.commands import TransformCommand

            scene = self.scene()
            if not scene or not scene.views():
                return

            view = scene.views()[0]
            window = view.parent()

            if hasattr(window, "undo_stack"):
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

        # Если swap уже выполнен ранее — завершаем (сброс состояния)
        if self._swap_done:
            try:
                self._clear_hover_indicator()
            except Exception:
                pass
            self._hover_candidate_slot = None
            self._swap_done = False
            return

        # Останавливаем таймер если он активен (быстрый бросок)
        if self._hover_timer.isActive():
            self._hover_timer.stop()

        # Обработка финального размещения: требуем подтверждённого swap для замены другого изображения
        scene = self.scene()
        if not scene:
            return

        if getattr(scene, "is_template_mode", False):
            # Определяем позицию курсора в координатах сцены и ищем слот под курсором
            try:
                cursor_pos = event.scenePos()
            except Exception:
                cursor_pos = self.mapToScene(self.boundingRect().center())
            items = scene.items(cursor_pos)
            new_slot = None
            from canvas.slot_item import TemplateSlotItem
            for it in items:
                if isinstance(it, TemplateSlotItem):
                    new_slot = it
                    break

            old_slot = getattr(self, "_old_parent_slot", None)

            # Если нет нового слота — возвращаемся на место или в превью
            if new_slot is None:
                if old_slot is not None:
                    old_slot.accept_image(self)
                else:
                    window = None
                    if scene.views():
                        view = scene.views()[0]
                        window = view.parent()
                    if window and hasattr(window, "preview_panel"):
                        window.preview_panel.add_pixmap(self.original_pixmap)
                    try:
                        scene.removeItem(self)
                    except Exception:
                        pass
                return

            # Если упали в тот же слот — просто центрируем
            if new_slot is old_slot:
                new_slot.accept_image(self)
                try:
                    self._clear_hover_indicator()
                except Exception:
                    pass
                return

            # Если hover-timer подтвердил готовность — выполняем операцию при отпускании
            if self._hover_ready:
                try:
                    other = new_slot.image_item
                    if other is not None and other is not self:
                        # поместить текущую в новый слот
                        new_slot.accept_image(self)
                        # вернуть другое изображение в старый слот или в превью
                        if old_slot is not None:
                            old_slot.accept_image(other)
                        else:
                            window = None
                            if scene.views():
                                view = scene.views()[0]
                                window = view.parent()
                            if window and hasattr(window, "preview_panel"):
                                window.preview_panel.add_pixmap(other.original_pixmap)
                                try:
                                    scene.removeItem(other)
                                except Exception:
                                    pass
                    else:
                        # пустой слот — просто перемещаем
                        new_slot.accept_image(self)
                except Exception:
                    pass

                try:
                    self._clear_hover_indicator()
                except Exception:
                    pass

                # сбрасываем флаг готовности
                self._hover_ready = False
                # пометим, что swap сделан, чтобы остальная логика корректно завершилась
                self._swap_done = True
                return

            # Если новый слот пустой — если старый слот был (перемещение между слотами), требуем подтверждённого hover
            if other is None and old_slot is not None:
                if self._swap_done:
                    new_slot.accept_image(self)
                    try:
                        self._clear_hover_indicator()
                    except Exception:
                        pass
                else:
                    # быстрый бросок — отмена
                    old_slot.accept_image(self)
                return

            # Если старого слота не было (перемещение из превью) — разрешаем поместить сразу
            if other is None and old_slot is None:
                new_slot.accept_image(self)
                try:
                    self._clear_hover_indicator()
                except Exception:
                    pass
                return

        return

        # Safety fallback: если по какой-то причине элемент был удалён из сцены — восстановим в старый слот
        try:
            if scene and old_slot is not None:
                if self not in scene.items():
                    scene.addItem(self)
                    old_slot.accept_image(self)
        except Exception:
            pass

    def _on_hover_timeout(self):
        """Выполняется, если пользователь держит изображение над слотом достаточно долго."""
        scene = self.scene()
        if not scene:
            return

        slot = self._hover_candidate_slot
        if slot is None:
            return

        old_slot = getattr(self, "_old_parent_slot", None)

        # Если тот же слот — центрируем
        if slot is old_slot:
            slot.accept_image(self)
            self._swap_done = True
            return

        other = slot.image_item
        if other is not None and other is not self:
            # Помещаем текущую в новый слот
            slot.accept_image(self)

            # Вернуть другое изображение в старый слот (если был)
            if old_slot is not None:
                old_slot.accept_image(other)
            else:
                if scene.views():
                    view = scene.views()[0]
                    window = view.parent()
                else:
                    window = None
                if window and hasattr(window, "preview_panel"):
                    window.preview_panel.add_pixmap(other.original_pixmap)
                    try:
                        scene.removeItem(other)
                    except Exception:
                        pass
        else:
            # Пустой слот — просто перемещаем
            slot.accept_image(self)

        # Очистим подсветку кандидата
        try:
            if self._hover_candidate_slot is not None:
                self._hover_candidate_slot.set_highlight(False)
        except Exception:
            pass

        self._swap_done = True
