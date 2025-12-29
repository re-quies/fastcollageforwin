from PySide6.QtWidgets import (
    QDockWidget,
    QListWidget,
    QListWidgetItem,
    QFileDialog
)
from PySide6.QtGui import QPixmap, QIcon, QDrag
from PySide6.QtCore import Qt, QMimeData, QSize
import os


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

        self.setSelectionMode(QListWidget.SingleSelection)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return

        pixmap = item.data(Qt.UserRole)
        if not isinstance(pixmap, QPixmap):
            return

        mime = QMimeData()
        mime.setImageData(pixmap)

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(
            pixmap.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        drag.exec(Qt.CopyAction)

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
        super().__init__("Изображения", parent)

        self.list = PreviewList(self)
        self.setWidget(self.list)

        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

    def add_images_from_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Добавить изображения",
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
