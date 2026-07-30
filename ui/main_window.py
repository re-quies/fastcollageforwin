import json
import logging
import os
import webbrowser

from PySide6.QtWidgets import (
    QMainWindow,
    QFileDialog,
    QGraphicsView,
    QLabel,
    QInputDialog,
    QMessageBox,
    QToolButton,
    QPushButton,
)
from PySide6.QtGui import (
    QPixmap,
    QImage,
    QPainter,
    QIcon,
    QAction,
    QUndoStack,
    QDragEnterEvent,
    QDropEvent,
)
from PySide6.QtCore import Qt, QPointF, QSize

from ui.preview_panel import PreviewPanel
from canvas.image_item import ImageItem
from canvas.slot_item import TemplateSlotItem
from undo.commands import (
    AddItemCommand,
    DeleteItemsCommand,
    ReturnToPreviewCommand,
)
from canvas.scene import CanvasScene
from ui.canvas_size_dialog import CanvasSizeDialog
from core.collage_mode import CollageMode
from core import project_io
from ui.start_dialog import StartCollageDialog
import i18n

logger = logging.getLogger(__name__)


class GraphicsView(QGraphicsView):
    def wheelEvent(self, event):
        scene = self.scene()
        if not scene:
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta == 0:
            return

        factor = 1.1 if delta > 0 else 0.9
        # Колесом вращаем/масштабируем только изображения —
        # выделенный СЛОТ не должен деформироваться этими жестами
        selected = [
            it for it in scene.selectedItems() if isinstance(it, ImageItem)
        ]

        # =========================
        # SHIFT + WHEEL → ROTATE ITEM
        # =========================
        if event.modifiers() & Qt.ShiftModifier and selected:
            item = selected[0]
            angle = 5 if delta > 0 else -5
            item.setRotation(item.rotation() + angle)
            event.accept()
            return

        # =========================
        # CTRL + WHEEL
        # =========================
        if event.modifiers() & Qt.ControlModifier:

            # 1) ЕСТЬ выделенный объект → масштаб ОБЪЕКТА
            if selected:
                item = selected[0]
                item.setScale(item.scale() * factor)
                event.accept()
                return

            # 2) НЕТ выделения → масштаб ХОЛСТА (VIEW)
            self.set_zoom_percent(
                self.zoom_percent + (10 if delta > 0 else -10)
            )
            event.accept()
            return

        # =========================
        # Z-MODE + WHEEL → ZOOM CONTENT
        # =========================
        if self.content_zoom_mode and selected:
            item = selected[0]
            if hasattr(item, "zoom_content"):
                item.zoom_content(factor)
                event.accept()
                return

        # =========================
        # DEFAULT
        # =========================
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        # Поддержка русской и английской раскладки: проверяем и Qt.Key, и текст символа
        def _is_key(ev, qt_key, *chars):
            txt = ev.text().lower()
            return ev.key() == qt_key or (txt in chars)

        if _is_key(event, Qt.Key_Z, 'z', 'я'):
            self.content_zoom_mode = True
        if _is_key(event, Qt.Key_C, 'c', 'с'):
            self.slot_pan_mode = True
        if _is_key(event, Qt.Key_X, 'x', 'ч'):
            self._return_selected_item_to_preview()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        def _is_key(ev, qt_key, *chars):
            txt = ev.text().lower()
            return ev.key() == qt_key or (txt in chars)

        if _is_key(event, Qt.Key_Z, 'z', 'я'):
            self.content_zoom_mode = False
        if _is_key(event, Qt.Key_C, 'c', 'с'):
            self.slot_pan_mode = False
        super().keyReleaseEvent(event)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self._zoom = 1.0
        self.zoom_percent = 100
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)

        self.setRenderHints(
            QPainter.Antialiasing |
            QPainter.SmoothPixmapTransform
        )
        self.setAcceptDrops(True)
        self.content_zoom_mode = False
        # Режим панорамирования изображения внутри слота (зажата клавиша C)
        self.slot_pan_mode = False

    def dragEnterEvent(self, event: QDragEnterEvent):
        md = event.mimeData()

        if (
            md.hasUrls()
            or md.hasImage()
            or md.hasFormat("application/x-preview-path")
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def _template_drop_allowed(self, view_pos) -> bool:
        """В template режиме бросать можно только в слот."""
        from canvas.slot_item import TemplateSlotItem

        scene = self.scene()
        if not getattr(scene, "is_template_mode", False):
            return True

        scene_pos = self.mapToScene(view_pos.toPoint())
        return any(
            isinstance(it, TemplateSlotItem)
            for it in scene.items(scene_pos)
        )

    def dropEvent(self, event: QDropEvent):
        md = event.mimeData()
        view_pos = event.position()

        # 1) Drag из проводника
        if md.hasUrls():
            if not self._template_drop_allowed(view_pos):
                event.ignore()
                return

            for url in md.urls():
                path = url.toLocalFile()
                if path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
                    self._add_image_from_path(path, view_pos)
            event.acceptProposedAction()
            return

        # 2) Drag из панели превью: передаётся ПУТЬ к файлу —
        # полноразмерное изображение загружается только здесь
        # (экономия памяти: превью не хранит полноразмерные картинки)
        if md.hasFormat("application/x-preview-path"):
            if not self._template_drop_allowed(view_pos):
                event.ignore()
                return

            path = bytes(md.data("application/x-preview-path")).decode("utf-8")
            if self._add_image_from_path(path, view_pos):
                # ФИКС: удаляем из превью именно перетащенный элемент
                # (по пути из MIME-данных), а не "текущий выделенный" —
                # раньше при смене выделения во время drag или при
                # дубликатах мог удалиться чужой элемент панели.
                self.window().preview_panel.remove_image(path=path)

            event.acceptProposedAction()
            return

        # 3) Fallback: превью-элемент без пути к файлу
        if md.hasImage():
            pixmap = md.imageData()
            if isinstance(pixmap, QPixmap) and not pixmap.isNull():
                if not self._template_drop_allowed(view_pos):
                    event.ignore()
                    return

                self._add_image_from_pixmap(pixmap, view_pos)

                # ЕСЛИ drag пришёл из превью — чистим панель.
                # ФИКС: ищем именно перетащенный fallback-элемент
                # (по cacheKey пиксмапа), а не текущее выделение
                if md.hasFormat("application/x-preview-item"):
                    self.window().preview_panel.remove_image(pixmap=pixmap)

            event.acceptProposedAction()
            return

        event.ignore()

    def _add_image_from_path(self, path: str, view_pos) -> bool:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            logger.warning("Failed to load image from %s", path)
            return False

        self._add_image_from_pixmap(pixmap, view_pos, source_path=path)
        return True

    def _add_image_from_pixmap(self, pixmap: QPixmap, view_pos, source_path=None):
        from canvas.image_item import ImageItem
        from canvas.slot_item import TemplateSlotItem

        scene = self.scene()
        if scene is None:
            return

        # ФИКС (баг 1): drag&drop теперь добавляет изображение через
        # undo-стек (AddItemCommand), как и добавление через меню —
        # раньше перетащенную картинку нельзя было отменить через Ctrl+Z.
        window = self.window()
        undo_stack = getattr(window, "undo_stack", None)

        scene_pos = self.mapToScene(view_pos.toPoint())

        # Если мы в template mode — попытаемся положить изображение в слот
        if getattr(scene, "is_template_mode", False):
            slot = None
            for it in scene.items(scene_pos):
                if isinstance(it, TemplateSlotItem):
                    slot = it
                    break

            if slot is not None:
                # Снимаем предыдущее выделение,
                # чтобы не выделялись сразу все добавленные элементы
                scene.clearSelection()

                item = ImageItem(pixmap, source_path)
                self._apply_swap_delay(scene, item)

                if undo_stack is not None:
                    # AddItemCommand со слотом: redo добавляет на сцену
                    # и помещает в слот, undo очищает ссылку слота
                    undo_stack.push(AddItemCommand(scene, item, slot))
                else:
                    scene.addItem(item)
                    slot.accept_image(item)

                item.setSelected(True)
                return

        # Обычное поведение — свободный ImageItem
        scene.clearSelection()

        item = ImageItem(pixmap, source_path)
        item.setPos(
            scene_pos
            - QPointF(pixmap.width() / 2, pixmap.height() / 2)
        )
        self._apply_swap_delay(scene, item)

        if undo_stack is not None:
            undo_stack.push(AddItemCommand(scene, item))
        else:
            scene.addItem(item)

        item.setSelected(True)

    @staticmethod
    def _apply_swap_delay(scene, item):
        # ФИКС (пункт 3/4 ревью): раньше здесь было getattr(self.scene, ...) —
        # обращение к МЕТОДУ scene, а не к сцене, поэтому задержка
        # никогда не применялась (баг был скрыт try/except: pass).
        delay = getattr(scene, "swap_delay_ms", None)
        if delay is not None and hasattr(item, "_hover_timer"):
            item._hover_timer.setInterval(int(delay))

    # ФИКС (баг 2): раньше зум отслеживался двумя независимыми
    # переменными: _zoom (кнопки +/-) и zoom_percent (Ctrl+колесо,
    # диалог "Масштаб..."), которые не синхронизировались между
    # собой. Теперь единственный ис  очник истины — zoom_percent,
    # а все пути изменения масштаба проходят через set_zoom_percent().

    def zoom_in(self):
        self.set_zoom_percent(round(self.zoom_percent * 1.1))

    def zoom_out(self):
        self.set_zoom_percent(round(self.zoom_percent / 1.1))

    def reset_zoom(self):
        self.set_zoom_percent(100)

    def set_zoom_percent(self, percent: int):
        percent = max(10, min(int(round(percent)), 800))

        self.resetTransform()
        factor = percent / 100.0
        self.scale(factor, factor)

        self.zoom_percent = percent
        self._zoom = factor

        window = self.window()
        if hasattr(window, "update_zoom_label"):
            window.update_zoom_label(percent)

    def _return_selected_item_to_preview(self):
        scene = self.scene()
        if not scene:
            return

        items = scene.selectedItems()
        if not items:
            return

        item = items[0]

        # Нас интересуют только ImageItem
        if not hasattr(item, "original_pixmap"):
            return

        pixmap = item.original_pixmap
        if pixmap.isNull():
            return

        window = self.window()
        if not hasattr(window, "preview_panel"):
            return

        # ФИКС (баг 3): возврат в превью и удаление с холста — одна
        # атомарная undo-команда. Раньше в превью добавляли напрямую,
        # и после Ctrl+Z изображение возвращалось на холст, но его
        # копия оставалась в панели превью.
        if hasattr(window, "undo_stack"):
            window.undo_stack.push(
                ReturnToPreviewCommand(scene, window, item)
            )
        else:
            window.preview_panel.add_pixmap(
                pixmap, getattr(item, "source_path", None)
            )
            scene.removeItem(item)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.swap_delay_ms = 1000

        self.setWindowTitle(i18n.t('app_title'))
        self.resize(1200, 800)
        # Открывать приложение в развернутом (максимизированном) окне
        self.showMaximized()

        self.undo_stack = QUndoStack(self)

        self.scene = CanvasScene()
        self.scene.swap_delay_ms = self.swap_delay_ms
        self.view = GraphicsView(self.scene)

        self.setCentralWidget(self.view)
        self.view.setAcceptDrops(True)

        self.zoom_label = QLabel("100%")
        self.statusBar().addPermanentWidget(self.zoom_label)

        # Добавляем кно  ки масштаба справа снизу
        self.zoom_minus = QPushButton("-", self)
        self.zoom_minus.setFixedSize(24, 24)
        self.zoom_minus.clicked.connect(lambda: self.view.zoom_out())
        self.statusBar().addPermanentWidget(self.zoom_minus)

        self.zoom_plus = QPushButton("+", self)
        self.zoom_plus.setFixedSize(24, 24)
        self.zoom_plus.clicked.connect(lambda: self.view.zoom_in())
        self.statusBar().addPermanentWidget(self.zoom_plus)

        # Кнопка для регенерации случайной сетки в TEMPLATE режиме
        self.regen_grid_btn = QToolButton(self)
        icon = QIcon('assets/icons/new_grid.svg')
        if not icon.isNull():
            self.regen_grid_btn.setIcon(icon)
            self.regen_grid_btn.setIconSize(QSize(18, 18))

        self.regen_grid_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.regen_grid_btn.clicked.connect(self.regenerate_template_grid)
        self.regen_grid_btn.setToolTip(i18n.t('new_grid_tooltip'))

        self.statusBar().addPermanentWidget(self.regen_grid_btn)

        self.preview_panel = PreviewPanel(self)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.preview_panel)
        self.collage_mode = CollageMode.FREE
        self._create_menu()

    # ---------- Menu ----------
    def _create_menu(self):
        self.menuBar().clear()
        file_menu = self.menuBar().addMenu(i18n.t('file'))
        new_action = QAction(i18n.t('new_collage'), self)
        new_action.triggered.connect(self.create_new_collage)
        file_menu.addAction(new_action)

        open_project_action = QAction(i18n.t('open_project'), self)
        open_project_action.setShortcut("Ctrl+Shift+O")
        open_project_action.triggered.connect(self.open_project)
        file_menu.addAction(open_project_action)

        save_project_action = QAction(i18n.t('save_project'), self)
        save_project_action.setShortcut("Ctrl+S")
        save_project_action.triggered.connect(self.save_project)
        file_menu.addAction(save_project_action)

        add_image_action = QAction(i18n.t('add_image'), self)
        add_image_action.setShortcut("Ctrl+O")
        add_image_action.triggered.connect(self.add_image)
        file_menu.addAction(add_image_action)
        add_to_panel_action = QAction(i18n.t('load_to_panel'), self)
        add_to_panel_action.triggered.connect(
            self.preview_panel.add_images_from_files
        )
        file_menu.addAction(add_to_panel_action)

        export_action = QAction(i18n.t('export'), self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export_image)
        file_menu.addAction(export_action)

        edit_menu = self.menuBar().addMenu(i18n.t('edit'))

        undo_action = self.undo_stack.createUndoAction(self, i18n.t('undo'))
        undo_action.setShortcut("Ctrl+Z")
        edit_menu.addAction(undo_action)
        redo_action = self.undo_stack.createRedoAction(self, i18n.t('redo'))
        redo_action.setShortcut("Ctrl+Y")
        edit_menu.addAction(redo_action)

        delete_action = QAction(i18n.t('delete'), self)
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(self.delete_selected)
        edit_menu.addAction(delete_action)

        layer_menu = self.menuBar().addMenu(i18n.t('layers'))

        bring_front = QAction(i18n.t('bring_front'), self)
        bring_front.setShortcut("Ctrl+]")
        bring_front.triggered.connect(self.bring_to_front)
        send_back = QAction(i18n.t('send_back'), self)
        send_back.setShortcut("Ctrl+[")
        send_back.triggered.connect(self.send_to_back)

        layer_menu.addAction(bring_front)
        layer_menu.addAction(send_back)

        canvas_menu = self.menuBar().addMenu(i18n.t('canvas'))

        resize_action = QAction(i18n.t('canvas_size'), self)
        resize_action.setShortcut("Ctrl+Shift+C")
        resize_action.triggered.connect(self.change_canvas_size)
        canvas_menu.addAction(resize_action)

        # Добавляем новые действия для зеркалирования
        mirror_menu = self.menuBar().addMenu(i18n.t('image_menu'))

        horizontal_mirror_action = QAction(i18n.t('mirror_h'), self)
        horizontal_mirror_action.setShortcut("Ctrl+Shift+H")
        horizontal_mirror_action.triggered.connect(self.horizontal_mirror)
        mirror_menu.addAction(horizontal_mirror_action)

        vertical_mirror_action = QAction(i18n.t('mirror_v'), self)
        vertical_mirror_action.setShortcut("Ctrl+Shift+V")
        vertical_mirror_action.triggered.connect(self.vertical_mirror)
        mirror_menu.addAction(vertical_mirror_action)

        developer_menu = self.menuBar().addMenu(i18n.t('developer'))
        developer_action = QAction(i18n.t('open_github'), self)
        developer_action.triggered.connect(self.open_github)
        developer_menu.addAction(developer_action)

        view_menu = self.menuBar().addMenu(i18n.t('view'))

        toggle_preview = QAction(i18n.t('toggle_preview'), self)
        toggle_preview.setCheckable(True)
        toggle_preview.setChecked(True)
        toggle_preview.triggered.connect(
            lambda checked: self.preview_panel.setVisible(checked)
        )

        view_menu.addAction(toggle_preview)

        zoom_action = QAction(i18n.t('zoom'), self)
        zoom_action.setShortcut("Ctrl+M")
        zoom_action.triggered.connect(self.set_exact_zoom)
        view_menu.addAction(zoom_action)

        # --- Settings menu ---
        settings_menu = self.menuBar().addMenu(i18n.t('settings'))
        swap_delay_action = QAction(i18n.t('swap_delay'), self)
        swap_delay_action.triggered.connect(self.change_swap_delay)
        settings_menu.addAction(swap_delay_action)

        # Language submenu
        language_menu = settings_menu.addMenu(i18n.t('language'))
        ru_action = QAction(i18n.t('russian'), self)
        ru_action.triggered.connect(lambda: self.set_language('ru'))
        en_action = QAction(i18n.t('english'), self)
        en_action.triggered.connect(lambda: self.set_language('en'))
        es_action = QAction(i18n.t('spanish'), self)
        es_action.triggered.connect(lambda: self.set_language('es'))
        language_menu.addAction(ru_action)
        language_menu.addAction(en_action)
        language_menu.addAction(es_action)

    # ---------- Helpers ----------

    def open_github(self):
        url = "https://github.com/re-quies/fastcollageforwin"  
        if not url:
            logger.warning("GitHub URL is not configured; nothing to open")
            return
        webbrowser.open(url)

    def _selected_item(self):
        items = self.scene.selectedItems()
        return items[0] if items else None

    # ---------- Actions ----------
    def add_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            i18n.t('choose_image'),
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )

        if not file_path:
            return

        pixmap = QPixmap(file_path)
        if pixmap.isNull():
            logger.warning("Failed to load image from %s", file_path)
            return

        item = ImageItem(pixmap, file_path)
        item.setPos(0, 0)
        item.setSelected(True)

        # Устанавливаем задержку swap для нового элемента
        delay = getattr(self.scene, 'swap_delay_ms', None)
        if delay is not None and hasattr(item, '_hover_timer'):
            item._hover_timer.setInterval(int(delay))

        cmd = AddItemCommand(self.scene, item)
        self.undo_stack.push(cmd)

    def _layer_target(self):
        """Элемент, к которому применяются команды слоёв.

        Если выбран слот шаблона ИЛИ изображение внутри слота —
        слоями управляем на уровне слота (изображение — его часть
        и поднимается/опускается вместе со слотом).
        """
        item = self._selected_item()
        if item is None:
            return None

        if isinstance(item, TemplateSlotItem):
            return item

        parent = item.parentItem()
        if isinstance(parent, TemplateSlotItem):
            return parent

        return item

    def bring_to_front(self):
        item = self._layer_target()
        if not item:
            return

        # Слоты сравниваем только между собой (по базовому слою,
        # без учёта временной подсветки при перетаскивании)
        if isinstance(item, TemplateSlotItem):
            slots = getattr(self.scene, "template_slots", [])
            max_z = max((s.base_z() for s in slots), default=0.0)
            item.set_base_z(max_z + 1)
            return

        max_z = max(
            (i.zValue() for i in self.scene.items()
             if isinstance(i, ImageItem)),
            default=0,
        )
        item.setZValue(max_z + 1)

    def send_to_back(self):
        item = self._layer_target()
        if not item:
            return

        if isinstance(item, TemplateSlotItem):
            slots = getattr(self.scene, "template_slots", [])
            min_z = min((s.base_z() for s in slots), default=0.0)
            item.set_base_z(min_z - 1)
            return

        min_z = min(
            (i.zValue() for i in self.scene.items()
             if isinstance(i, ImageItem)),
            default=0,
        )
        item.setZValue(min_z - 1)

    def export_image(self):
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            i18n.t('export_image'),
            "",
            "PNG (*.png);;JPEG (*.jpg *.jpeg)"
        )

        if not file_path:
            return

        # ФИКС (баг 5): если пользователь не указал расширение —
        # подставляем его из выбранного фильтра, иначе QImage.save
        # не сможет определить формат и молча откажет.
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg"):
            file_path += ".jpg" if "JPEG" in selected_filter else ".png"

        rect = self.scene.sceneRect()
        image = QImage(
            int(rect.width()),
            int(rect.height()),
            QImage.Format_ARGB32
        )
        image.fill(Qt.white)

        # Скрыть все визуальные маркеры/выделения на сцене во время рендеринга
        prev_suppress = getattr(self.scene, 'suppress_visuals', False)
        self.scene.suppress_visuals = True

        # Удаляем визуальные индикаторы hover у ImageItem'ов
        for it in list(self.scene.items()):
            if hasattr(it, '_clear_hover_indicator'):
                try:
                    it._clear_hover_indicator()
                except Exception:
                    logger.exception(
                        "Failed to clear hover indicator before export"
                    )

        # Сохраняем предыдущее состояние подсветки слотов, затем отключаем их
        slots = getattr(self.scene, 'template_slots', [])
        prev_highlights = [bool(getattr(s, '_highlighted', False)) for s in slots]
        for slot in slots:
            slot.set_highlight(False)
            slot._update_handles()

        self.scene.update()

        painter = QPainter(image)
        self.scene.render(painter)
        painter.end()

        # Восстановим состояние визуализации
        for slot, prev in zip(slots, prev_highlights):
            slot.set_highlight(prev)
            slot._update_handles()
        self.scene.suppress_visuals = prev_suppress

        if not image.save(file_path):
            # ФИКС (баг 5): сообщаем об ошибке пользователю,
            # а не только в лог
            logger.error("Failed to save exported image to %s", file_path)
            QMessageBox.critical(
                self, i18n.t('error'), i18n.t('export_failed')
            )

    def change_canvas_size(self):
        dialog = CanvasSizeDialog(
            self.scene.canvas_width,
            self.scene.canvas_height,
            self
        )

        if dialog.exec():
            width, height = dialog.get_size()
            self.scene.set_canvas_size(width, height)

    def horizontal_mirror(self):
        """Обработчик горизонтального зеркалирования"""
        scene = self.get_active_scene()
        if scene:
            selected_items = scene.selectedItems()
            for item in selected_items:
                if isinstance(item, ImageItem):
                    item.mirror_image('horizontal')

    def vertical_mirror(self):
        """Обработчик вертикального зеркалирования"""
        scene = self.get_active_scene()
        if scene:
            selected_items = scene.selectedItems()
            for item in selected_items:
                if isinstance(item, ImageItem):
                    item.mirror_image('vertical')

    def get_active_scene(self):
        """Получаем активную сцену"""
        return self.scene

    def delete_selected(self):
        # Пункт 4: удаление через undo-стек + очистка ссылок слотов.
        # Слоты шаблона клавишей Delete не удаляются — только изображения
        items = [
            it for it in self.scene.selectedItems()
            if not isinstance(it, TemplateSlotItem)
        ]
        if not items:
            return

        self.undo_stack.push(DeleteItemsCommand(self.scene, items))

    def change_swap_delay(self):
        value, ok = QInputDialog.getInt(
            self,
            i18n.t('swap_delay'),
            i18n.t('swap_delay'),
            self.swap_delay_ms,
            100,
            5000,
            100,
        )

        if ok:
            self.swap_delay_ms = value
            self.scene.swap_delay_ms = value

            # Обновляем существующие ImageItem'ы
            for it in self.scene.items():
                if hasattr(it, '_hover_timer'):
                    it._hover_timer.setInterval(value)

    def set_language(self, lang: str):
        i18n.set_lang(lang)
        # Пересобираем меню и обновляем тексты
        self.setWindowTitle(i18n.t('app_title'))
        self._create_menu()
        # Обновляем заголовок панели превью и подсказку кнопки сетки
        self.preview_panel.setWindowTitle(i18n.t('images'))
        self.regen_grid_btn.setToolTip(i18n.t('new_grid_tooltip'))

    def update_zoom_label(self, percent):
        # percent может быть float (масштаб 1.0) или int (проценты)
        if isinstance(percent, float):
            value = int(round(percent * 100))
        else:
            value = int(round(percent))

        self.zoom_label.setText(f"{value}%")

    def set_exact_zoom(self):
        value, ok = QInputDialog.getInt(
            self,
            i18n.t('zoom'),
            i18n.t('enter_zoom'),
            self.view.zoom_percent,
            10,
            800,
            10
        )

        if ok:
            self.view.set_zoom_percent(value)

    # ---------- Project save / load ----------

    def save_project(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            i18n.t('save_project'),
            "",
            "FastCollage Project (*.fcproj);;JSON (*.json)"
        )
        if not file_path:
            return

        data, skipped = project_io.serialize(self)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.exception("Failed to save project to %s", file_path)
            QMessageBox.critical(
                self, i18n.t('error'), i18n.t('project_save_failed')
            )
            return

        if skipped:
            QMessageBox.warning(
                self, i18n.t('app_title'), i18n.t('unsaved_images')
            )

    def open_project(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            i18n.t('open_project'),
            "",
            "FastCollage Project (*.fcproj *.json)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            logger.exception("Failed to load project from %s", file_path)
            QMessageBox.critical(
                self, i18n.t('error'), i18n.t('project_load_failed')
            )
            return

        try:
            missing = project_io.apply(self, data)
        except ValueError:
            logger.exception("Invalid project file %s", file_path)
            QMessageBox.critical(
                self, i18n.t('error'), i18n.t('project_load_failed')
            )
            return

        if missing:
            QMessageBox.warning(
                self,
                i18n.t('app_title'),
                i18n.t('missing_images') + "\n" + "\n".join(missing[:10]),
            )

    def create_new_collage(self):
        dialog = StartCollageDialog(self)
        if not dialog.exec():
            return

        self.create_new_collage_from_data(dialog.result_data())

    def create_new_collage_from_data(self, data):
        self.collage_mode = data["mode"]

        # Пункт 2: очищаем историю undo — старые команды ссылаются на
        # элементы прежней сцены, и Ctrl+Z после нового коллажа мог бы
        # менять уже несуществующую сцену.
        self.undo_stack.clear()

        # --- TEMPLATE MODE ---
        if self.collage_mode == CollageMode.TEMPLATE:
            w, h = data["canvas_size"]

            self.scene = CanvasScene(w, h)
            self.scene.swap_delay_ms = self.swap_delay_ms
            self.scene.is_template_mode = True
            self.scene.template_image_count = data["count"]
            self.scene.build_template()

        # --- FREE MODE ---
        else:
            # В свободном режиме также используем выбранный размер холста
            w, h = data.get("canvas_size", (1920, 1080))
            self.scene = CanvasScene(w, h)
            self.scene.swap_delay_ms = self.swap_delay_ms

        # ВАЖНО: setScene ОДИН раз
        self.view.setScene(self.scene)

    def regenerate_template_grid(self):
        """Обработчик кнопки: регенерация сетки только в TEMPLATE режиме."""
        if self.collage_mode != CollageMode.TEMPLATE:
            return

        scene = self.get_active_scene()
        if not getattr(scene, 'is_template_mode', False):
            return

        # Есть ли изображения на холсте или в слотах
        images_present = any(
            hasattr(it, 'original_pixmap') for it in scene.items()
        ) or any(
            getattr(slot, 'image_item', None) is not None
            for slot in getattr(scene, 'template_slots', [])
        )

        # Если есть изображения — попросим подтверждение перед удалением
        if images_present:
            msg = QMessageBox(self)
            msg.setWindowTitle(i18n.t('confirm_regen_title'))
            msg.setText(i18n.t('confirm_regen_text'))
            msg.setIcon(QMessageBox.Warning)
            yes = msg.addButton(i18n.t('confirm'), QMessageBox.AcceptRole)
            msg.addButton(i18n.t('cancel'), QMessageBox.RejectRole)
            msg.exec()
            if msg.clickedButton() is not yes:
                return

        # Перестроим шаблон (CanvasScene.build_template отвечает за очистку)
        scene.build_template()

        # История undo может ссылаться на удалённые слоты/изображения (пункт 2)
        self.undo_stack.clear()
