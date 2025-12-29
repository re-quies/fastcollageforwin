import webbrowser
from PySide6.QtWidgets import (
    QMainWindow,
    QGraphicsScene,
    QFileDialog,
    QGraphicsView,
    QMenuBar,
    QLabel,
    QInputDialog,
)
from PySide6.QtGui import (
    QPixmap,
    QImage,
    QPainter,
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
from ui.preview_panel import PreviewPanel
from canvas.image_item import ImageItem
from undo.commands import AddItemCommand
from canvas.scene import CanvasScene
from ui.canvas_size_dialog import CanvasSizeDialog

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


    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Z:
            self.content_zoom_mode = True
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Z:
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

        # 1) Drag из проводника (ОСТАВЛЯЕМ как есть)
        if md.hasUrls():
            for url in md.urls():
                path = url.toLocalFile()
                if path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
                    self._add_image_from_path(path, view_pos)
            event.acceptProposedAction()
            return

        # 2) Drag из панели превью (Pixmap)
        if md.hasImage():
            pixmap = md.imageData()
            if isinstance(pixmap, QPixmap) and not pixmap.isNull():
                self._add_image_from_pixmap(pixmap, view_pos)
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

        scene_pos = self.mapToScene(view_pos.toPoint())

        item = ImageItem(pixmap)
        item.setPos(
            scene_pos
            - QPointF(pixmap.width() / 2, pixmap.height() / 2)
        )

        self.scene().addItem(item)
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("PhotoCollage (Windows)")
        self.resize(1200, 800)

        self.undo_stack = QUndoStack(self)

        self.scene = CanvasScene()
        self.view = GraphicsView(self.scene)

        self.setCentralWidget(self.view)
        self.view.setAcceptDrops(True)
        

        self.zoom_label = QLabel("100%")
        self.statusBar().addPermanentWidget(self.zoom_label)

        self.preview_panel = PreviewPanel(self)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.preview_panel)

        self._create_menu()



    # ---------- Menu ----------
    def _create_menu(self):
        file_menu = self.menuBar().addMenu("Файл")

        add_image_action = QAction("Добавить изображение", self)
        add_image_action.setShortcut("Ctrl+O")
        add_image_action.triggered.connect(self.add_image)
        file_menu.addAction(add_image_action)
        add_to_panel_action = QAction("Загрузить в панель", self)
        add_to_panel_action.triggered.connect(
            self.preview_panel.add_images_from_files
        )
        file_menu.addAction(add_to_panel_action)

        export_action = QAction("Экспорт", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export_image)
        file_menu.addAction(export_action)

        edit_menu = self.menuBar().addMenu("Правка")

        undo_action = self.undo_stack.createUndoAction(self, "Отменить")
        undo_action.setShortcut("Ctrl+Z")
        edit_menu.addAction(undo_action)

        redo_action = self.undo_stack.createRedoAction(self, "Повторить")
        redo_action.setShortcut("Ctrl+Y")
        edit_menu.addAction(redo_action)

        delete_action = QAction("Удалить", self)
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(self.delete_selected)
        edit_menu.addAction(delete_action)

        layer_menu = self.menuBar().addMenu("Слои")

        bring_front = QAction("На передний план", self)
        bring_front.setShortcut("Ctrl+]")
        bring_front.triggered.connect(self.bring_to_front)

        send_back = QAction("На задний план", self)
        send_back.setShortcut("Ctrl+[")
        send_back.triggered.connect(self.send_to_back)

        layer_menu.addAction(bring_front)
        layer_menu.addAction(send_back)

        canvas_menu = self.menuBar().addMenu("Холст")

        resize_action = QAction("Размер холста...", self)
        resize_action.setShortcut("Ctrl+Shift+C")
        resize_action.triggered.connect(self.change_canvas_size)
        canvas_menu.addAction(resize_action)

        # Добавляем новые действия для зеркалирования
        mirror_menu = self.menuBar().addMenu("Изображение")
        
        horizontal_mirror_action = QAction("Зеркалить по горизонтали", self)
        horizontal_mirror_action.setShortcut("Ctrl+Shift+H")
        horizontal_mirror_action.triggered.connect(self.horizontal_mirror)
        mirror_menu.addAction(horizontal_mirror_action)

        vertical_mirror_action = QAction("Зеркалить по вертикали", self)
        vertical_mirror_action.setShortcut("Ctrl+Shift+V")
        vertical_mirror_action.triggered.connect(self.vertical_mirror)
        mirror_menu.addAction(vertical_mirror_action)

        developer_menu = self.menuBar().addMenu("Разработчик")
        developer_action = QAction("Перейти на GitHub", self)
        developer_action.triggered.connect(self.open_github)
        developer_menu.addAction(developer_action)

        view_menu = self.menuBar().addMenu("Вид")

        toggle_preview = QAction("Панель изображений", self)
        toggle_preview.setCheckable(True)
        toggle_preview.setChecked(True)
        toggle_preview.triggered.connect(
            lambda checked: self.preview_panel.setVisible(checked)
        )

        view_menu.addAction(toggle_preview)

        zoom_action = QAction("Масштаб...", self)
        zoom_action.setShortcut("Ctrl+M")
        zoom_action.triggered.connect(self.set_exact_zoom)
        view_menu.addAction(zoom_action)

    # ---------- Helpers ----------

    def open_github(self):
        """Открывает страницу GitHub в браузере."""
        url = "https://github.com/your-github-repository"  # Замените на ссылку вашего репозитория
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

        painter = QPainter(image)
        self.scene.render(painter)
        painter.end()

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

    def update_zoom_label(self, percent):
        self.zoom_label.setText(f"{percent}%")

    def set_exact_zoom(self):
        value, ok = QInputDialog.getInt(
            self,
            "Масштаб холста",
            "Укажите масштаб (%)",
            self.view.zoom_percent,
            10,
            800,
            10
        )

        if ok:
            self.view.set_zoom_percent(value)