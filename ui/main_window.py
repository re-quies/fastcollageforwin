import webbrowser
from PySide6.QtWidgets import (
    QMainWindow,
    QGraphicsScene,
    QFileDialog,
    QGraphicsView,
    QMenuBar,
    QLabel,
    QInputDialog,
    QMessageBox,
    QToolButton,
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
    QKeySequence,
)
from PySide6.QtCore import (
    Qt,
    QMimeData, 
    QPointF
)    
from PySide6.QtCore import QSize
from ui.preview_panel import PreviewPanel
from canvas.image_item import ImageItem
from undo.commands import AddItemCommand
from canvas.scene import CanvasScene
from ui.canvas_size_dialog import CanvasSizeDialog
from core.collage_mode import CollageMode
from ui.start_dialog import StartCollageDialog
import i18n
from PySide6.QtWidgets import QPushButton

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
        selected = scene.selectedItems()

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


  #  def keyPressEvent(self, event):
  #      if event.key() == Qt.Key_Z:
   #         self.content_zoom_mode = True
    #    super().keyPressEvent(event)

    def keyPressEvent(self, event):
        # Поддержка русской и английской раскладки: проверяем и Qt.Key, и текст символа
        def _is_key(ev, qt_key, *chars):
            try:
                txt = ev.text().lower()
            except Exception:
                txt = ""
            return ev.key() == qt_key or (txt in chars)

        if _is_key(event, Qt.Key_Z, 'z', 'я'):
            self.content_zoom_mode = True
        if _is_key(event, Qt.Key_X, 'x', 'ч'):
            self._return_selected_item_to_preview()
            event.accept()
            return
        super().keyPressEvent(event)
    
    def keyReleaseEvent(self, event):
        def _is_key(ev, qt_key, *chars):
            try:
                txt = ev.text().lower()
            except Exception:
                txt = ""
            return ev.key() == qt_key or (txt in chars)

        if _is_key(event, Qt.Key_Z, 'z', 'я'):
            self.content_zoom_mode = False
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

    def dragEnterEvent(self, event: QDragEnterEvent):
        md = event.mimeData()

        if md.hasUrls() or md.hasImage():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        md = event.mimeData()
        view_pos = event.position()

        # 1) Drag из проводника — НЕ ТРОГАЕМ
        if md.hasUrls():
            # В template режиме — запрещаем добавление вне слота
            scene_pos = self.mapToScene(view_pos.toPoint())
            scene = self.scene()
            if getattr(scene, "is_template_mode", False):
                items = scene.items(scene_pos)
                from canvas.slot_item import TemplateSlotItem
                has_slot = any(isinstance(it, TemplateSlotItem) for it in items)
                if not has_slot:
                    event.ignore()
                    return

            for url in md.urls():
                path = url.toLocalFile()
                if path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
                    self._add_image_from_path(path, view_pos)
            event.acceptProposedAction()
            return

        # 2) Drag из панели превью
        if md.hasImage():
            pixmap = md.imageData()
            if isinstance(pixmap, QPixmap) and not pixmap.isNull():
                # В template режиме — запрещаем добавление вне слота
                scene_pos = self.mapToScene(view_pos.toPoint())
                scene = self.scene()
                if getattr(scene, "is_template_mode", False):
                    items = scene.items(scene_pos)
                    from canvas.slot_item import TemplateSlotItem
                    slot = None
                    for it in items:
                        if isinstance(it, TemplateSlotItem):
                            slot = it
                            break
                    if slot is None:
                        event.ignore()
                        return

                self._add_image_from_pixmap(pixmap, view_pos)

                # 🔴 ЕСЛИ drag пришёл из превью — чистим меню
                if md.hasFormat("application/x-preview-item"):
                    self.window().preview_panel.remove_current_item()

            event.acceptProposedAction()
            return

        event.ignore()

    def _add_image_from_path(self, path: str, view_pos):
            pixmap = QPixmap(path)
            if pixmap.isNull():
                return

            self._add_image_from_pixmap(pixmap, view_pos)

    def _add_image_from_pixmap(self, pixmap: QPixmap, view_pos):
        from canvas.image_item import ImageItem
        from canvas.slot_item import TemplateSlotItem

        scene_pos = self.mapToScene(view_pos.toPoint())

        # Если мы в template mode — попытаемся положить изображение в слот
        scene = self.scene()
        if getattr(scene, "is_template_mode", False):
            # Ищем слот под курсором
            items = scene.items(scene_pos)
            slot = None
            for it in items:
                if isinstance(it, TemplateSlotItem):
                    slot = it
                    break

            if slot is not None:
                item = ImageItem(pixmap)
                # Добавляем на сцену и делегируем слоту управление позиционированием
                scene.addItem(item)
                try:
                    delay = getattr(self.scene, 'swap_delay_ms', None)
                    if delay is not None and hasattr(item, '_hover_timer'):
                        item._hover_timer.setInterval(delay)
                except Exception:
                    pass
                slot.accept_image(item)
                item.setSelected(True)
                return

        # Обычное поведение — свободный ImageItem
        item = ImageItem(pixmap)
        item.setPos(
            scene_pos
            - QPointF(pixmap.width() / 2, pixmap.height() / 2)
        )

        self.scene().addItem(item)
        try:
            delay = getattr(self.scene, 'swap_delay_ms', None)
            if delay is not None and hasattr(item, '_hover_timer'):
                item._hover_timer.setInterval(delay)
        except Exception:
            pass

        item.setSelected(True)


    def _add_image(self, path: str, view_pos):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return

        scene_pos = self.mapToScene(view_pos.toPoint())

        item = ImageItem(pixmap)
        item.setPos(scene_pos - QPointF(pixmap.width() / 2, pixmap.height() / 2))

        self.scene().addItem(item)

    def zoom_in(self):
        self._apply_zoom(1.1)

    def zoom_out(self):
        self._apply_zoom(0.9)

    def reset_zoom(self):
        self.resetTransform()
        self._zoom = 1.0
        self._emit_zoom_changed()

    def _apply_zoom(self, factor):
        new_zoom = self._zoom * factor
        if not 0.1 <= new_zoom <= 5.0:
            return

        self.scale(factor, factor)
        self._zoom = new_zoom

        window = self.window()
        if hasattr(window, "update_zoom_label"):
            window.update_zoom_label(self._zoom)

    def _emit_zoom_changed(self):
        window = self.window()
        if hasattr(window, "update_zoom_label"):
            window.update_zoom_label(self._zoom)

    def set_zoom_percent(self, percent: int):
        percent = max(10, min(percent, 800))

        self.resetTransform()
        factor = percent / 100.0
        self.scale(factor, factor)

        self.zoom_percent = percent

        if hasattr(self.parent(), "update_zoom_label"):
            self.parent().update_zoom_label(percent)

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

        # 1️⃣ добавляем в превью
        window.preview_panel.add_pixmap(pixmap)

        # 2️⃣ удаляем с холста
        scene.removeItem(item)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.swap_delay_ms = 1000

        self.setWindowTitle(i18n.t('app_title'))
        self.resize(1200, 800)
        # Открывать приложение в развернутом (максимизированном) окне
        try:
            self.showMaximized()
        except Exception:
            # fallback: установить состояние окна как максимизированное
            self.setWindowState(self.windowState() | Qt.WindowMaximized)

        self.undo_stack = QUndoStack(self)

        self.scene = CanvasScene()
        self.scene.swap_delay_ms = self.swap_delay_ms
        self.view = GraphicsView(self.scene)

        self.setCentralWidget(self.view)
        self.view.setAcceptDrops(True)
        

        self.zoom_label = QLabel("100%")
        self.statusBar().addPermanentWidget(self.zoom_label)

        # Добавляем кнопки масштаба справа снизу
        self.zoom_minus = QPushButton("-", self)
        self.zoom_minus.setFixedSize(24, 24)
        self.zoom_minus.clicked.connect(lambda: self.view.zoom_out())
        self.statusBar().addPermanentWidget(self.zoom_minus)

        self.zoom_plus = QPushButton("+", self)
        self.zoom_plus.setFixedSize(24, 24)
        self.zoom_plus.clicked.connect(lambda: self.view.zoom_in())
        self.statusBar().addPermanentWidget(self.zoom_plus)

        # Кнопка для регенерации случайной сетки в TEMPLATE режиме (иконка только)
        self.regen_grid_btn = QToolButton(self)
        try:
            icon = QIcon('assets/icons/new_grid.svg')
            if not icon.isNull():
                self.regen_grid_btn.setIcon(icon)
                self.regen_grid_btn.setIconSize(QSize(18, 18))
        except Exception:
            pass

        self.regen_grid_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.regen_grid_btn.clicked.connect(self.regenerate_template_grid)
        # Локализованная подсказка
        try:
            self.regen_grid_btn.setToolTip(i18n.t('new_grid_tooltip'))
        except Exception:
            pass

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
        language_menu.addAction(ru_action)
        language_menu.addAction(en_action)

    # ---------- Helpers ----------

    def open_github(self):
        """Открывает страницу GitHub в браузере."""
        url = "https://github.com/re-quies/fastcollageforwin"  # Замените на ссылку вашего репозитория
        webbrowser.open(url)

    def _selected_item(self):
        items = self.scene.selectedItems()
        return items[0] if items else None

    # ---------- Actions ----------
    def add_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбрать изображение",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp)"
        )

        if not file_path:
            return

        pixmap = QPixmap(file_path)
        if pixmap.isNull():
            return

        item = ImageItem(pixmap)
        item.setPos(0, 0)
        item.setSelected(True)

        # Устанавливаем задержку swap для нового элемента
        try:
            delay = getattr(self.scene, 'swap_delay_ms', None)
            if delay is not None and hasattr(item, '_hover_timer'):
                item._hover_timer.setInterval(delay)
        except Exception:
            pass

        cmd = AddItemCommand(self.scene, item)
        self.undo_stack.push(cmd)

    def bring_to_front(self):
        item = self._selected_item()
        if not item:
            return

        max_z = max((i.zValue() for i in self.scene.items()), default=0)
        item.setZValue(max_z + 1)

    def send_to_back(self):
        item = self._selected_item()
        if not item:
            return

        min_z = min((i.zValue() for i in self.scene.items()), default=0)
        item.setZValue(min_z - 1)

    def export_image(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт изображения",
            "",
            "PNG (*.png);;JPEG (*.jpg *.jpeg)"
        )

        if not file_path:
            return

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

        # Удаляем визуальные индикаторы hover у ImageItem'ов (например "Release to drop")
        try:
            for it in list(self.scene.items()):
                try:
                    if hasattr(it, '_clear_hover_indicator'):
                        it._clear_hover_indicator()
                except Exception:
                    pass
        except Exception:
            pass

        # Сохраняем предыдущее состояние подсветки слотов, затем отключаем их
        prev_highlights = []
        try:
            for slot in getattr(self.scene, 'template_slots', []):
                prev_highlights.append(bool(getattr(slot, '_highlighted', False)))
                slot.set_highlight(False)
                slot._update_handles()
        except Exception:
            prev_highlights = []

        self.scene.update()

        painter = QPainter(image)
        self.scene.render(painter)
        painter.end()

        # Восстановим состояние визуализации
        try:
            for slot, prev in zip(getattr(self.scene, 'template_slots', []), prev_highlights):
                slot.set_highlight(prev)
                slot._update_handles()
        except Exception:
            pass
        self.scene.suppress_visuals = prev_suppress

        image.save(file_path)

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
        for item in self.scene.selectedItems():
            self.scene.removeItem(item)

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
            if hasattr(self.scene, 'swap_delay_ms'):
                self.scene.swap_delay_ms = value

            # Обновляем существующие ImageItem'ы
            for it in self.scene.items():
                try:
                    if hasattr(it, '_hover_timer'):
                        it._hover_timer.setInterval(value)
                except Exception:
                    pass

    def set_language(self, lang: str):
        import i18n as _i18n
        _i18n.set_lang(lang)
        # Пересобираем меню и обновляем тексты
        self.setWindowTitle(_i18n.t('app_title'))
        self._create_menu()
        # Обновляем заголовок панели превью
        try:
            self.preview_panel.setWindowTitle(_i18n.t('images'))
        except Exception:
            pass

    def update_zoom_label(self, percent):
        # percent может быть float (масштаб 1.0) или int (проценты)
        try:
            if isinstance(percent, float):
                value = int(round(percent * 100))
            else:
                value = int(round(percent))
        except Exception:
            value = percent

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

    def create_new_collage(self):
        dialog = StartCollageDialog(self)
        if not dialog.exec():
            return

        data = dialog.result_data()
        self.collage_mode = data["mode"]

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

    def create_new_collage_from_data(self, data):
        self.collage_mode = data["mode"]

        if self.collage_mode == CollageMode.TEMPLATE:
            w, h = data["canvas_size"]
            self.scene = CanvasScene(w, h)
            self.scene.swap_delay_ms = self.swap_delay_ms
            self.scene.is_template_mode = True
            self.scene.template_image_count = data["count"]
            self.scene.build_template()
        else:
            # В свободном режиме используем выбранный размер холста
            w, h = data.get("canvas_size", (1920, 1080))
            self.scene = CanvasScene(w, h)
            self.scene.swap_delay_ms = self.swap_delay_ms

        self.view.setScene(self.scene)

    def regenerate_template_grid(self):
        """Обработчик кнопки: регенерация сетки только в TEMPLATE режиме."""
        # Если текущее состояние не шаблон — ничего не делаем
        if self.collage_mode != CollageMode.TEMPLATE:
            return

        # Если сцена не в шаблонном режиме — ничего не делаем
        scene = self.get_active_scene()
        if not getattr(scene, 'is_template_mode', False):
            return

        # Если нет ни одного изображения на холсте и ни в слотах — подтверждение не требуем
        try:
            images_present = False

            # 1) Ищем ImageItem по наличию атрибута original_pixmap
            try:
                for it in scene.items():
                    if hasattr(it, 'original_pixmap'):
                        images_present = True
                        break
            except Exception:
                images_present = False

            # 2) Также проверяем, есть ли в слотах связанные изображения
            try:
                if not images_present:
                    for slot in getattr(scene, 'template_slots', []):
                        if getattr(slot, 'image_item', None) is not None:
                            images_present = True
                            break
            except Exception:
                pass
        except Exception:
            images_present = True

        # Если есть изображения — попросим подтверждение перед удалением
        if images_present:
            try:
                msg = QMessageBox(self)
                msg.setWindowTitle(i18n.t('confirm_regen_title'))
                msg.setText(i18n.t('confirm_regen_text'))
                msg.setIcon(QMessageBox.Warning)
                yes = msg.addButton(i18n.t('confirm'), QMessageBox.AcceptRole)
                no = msg.addButton(i18n.t('cancel'), QMessageBox.RejectRole)
                msg.exec()
                if msg.clickedButton() is not yes:
                    return
            except Exception:
                # Если диалог не доступен — продолжаем
                pass

        # Перестроим шаблон (CanvasScene.build_template отвечает за очистку старых слотов)
        try:
            scene.build_template()
        except Exception:
            pass