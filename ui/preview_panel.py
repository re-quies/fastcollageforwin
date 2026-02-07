from PySide6.QtWidgets import (
    QDockWidget,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMenu,
)
from PySide6.QtGui import QPixmap, QIcon, QDrag
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QMimeData, QSize
import os
import i18n


class PreviewList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(96, 96))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSpacing(8)

        # ВАЖНО
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setSelectionMode(QListWidget.SingleSelection)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return

        pixmap = item.data(Qt.UserRole)
        if not isinstance(pixmap, QPixmap):
            return

        # Снимаем выделение на холсте — важно, чтобы не было нескольких выделенных изображений
        window = self.window()
        if window and hasattr(window, "scene"):
            try:
                window.scene.clearSelection()
            except Exception:
                pass

        mime = QMimeData()
        mime.setImageData(pixmap)

        # 🔴 маркер: drag пришёл из превью
        mime.setData("application/x-preview-item", b"1")

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(
            pixmap.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        drag.exec(Qt.CopyAction)

    def mousePressEvent(self, event):
        # При обращении к панели превью снимаем выделение на холсте
        window = self.window()
        if window and hasattr(window, "scene"):
            try:
                window.scene.clearSelection()
            except Exception:
                pass

        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        # Показать контекстное меню только для превью-панели (удаление выбранного элемента)
        item = self.itemAt(event.pos())
        if not item:
            return

        menu = QMenu(self)
        remove_action = QAction(i18n.t('delete'), self)
        def _remove():
            row = self.row(item)
            if row >= 0:
                self.takeItem(row)

        remove_action.triggered.connect(_remove)
        menu.addAction(remove_action)
        menu.exec(event.globalPos())

    # ---------- Drag IN (из проводника) ----------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        md = event.mimeData()

        if not md.hasUrls():
            event.ignore()
            return

        for url in md.urls():
            path = url.toLocalFile()
            if not path.lower().endswith(
                (".png", ".jpg", ".jpeg", ".bmp", ".webp")
            ):
                continue

            pixmap = QPixmap(path)
            if pixmap.isNull():
                continue

            icon = QIcon(
                pixmap.scaled(
                    96, 96,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
            )

            item = QListWidgetItem(icon, "")
            item.setData(Qt.UserRole, pixmap)

            self.addItem(item)

        event.acceptProposedAction()

    

class PreviewPanel(QDockWidget):
    def __init__(self, parent=None):
        super().__init__(i18n.t('images'), parent)

        self.setAcceptDrops(True)   # 🔴 ВАЖНО

        self.list = PreviewList(self)
        self.setWidget(self.list)

        self.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        # передаём drop внутрь списка
        self.list.dropEvent(event)


    def add_images_from_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            i18n.t('add_images'),
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )

        for path in files:
            pixmap = QPixmap(path)
            if pixmap.isNull():
                continue

            icon = QIcon(
                pixmap.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

            item = QListWidgetItem(icon, os.path.basename(path))
            item.setData(Qt.UserRole, pixmap)

            self.list.addItem(item)

    def remove_current_item(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
    
    def add_pixmap(self, pixmap: QPixmap):
        item = QListWidgetItem()
        item.setIcon(QIcon(pixmap))
        item.setData(Qt.UserRole, pixmap)
        item.setSizeHint(QSize(96, 96))
        self.list.addItem(item)