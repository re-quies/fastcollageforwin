import logging
import os

from PySide6.QtWidgets import (
    QDockWidget,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMenu,
)
from PySide6.QtGui import QPixmap, QIcon, QDrag, QAction, QImageReader
from PySide6.QtCore import Qt, QMimeData, QSize

import i18n

logger = logging.getLogger(__name__)

# Роли данных элемента списка:
# PATH_ROLE — путь к исходному файлу (str или None);
# PIXMAP_ROLE — полноразмерный QPixmap-fallback для элементов без файла.
PATH_ROLE = Qt.UserRole
PIXMAP_ROLE = Qt.UserRole + 1

THUMB_SIZE = 96

MIME_PREVIEW_MARKER = "application/x-preview-item"
MIME_PREVIEW_PATH = "application/x-preview-path"

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def load_thumbnail(path: str, size: int = THUMB_SIZE) -> QPixmap:
    """Загрузить миниатюру, не удерживая полноразмерное изображение в памяти.

    QImageReader с setScaledSize декодирует картинку сразу в уменьшенном
    размере — полноразмерный QPixmap в памяти не создаётся вовсе.
    """
    reader = QImageReader(path)
    reader.setAutoTransform(True)

    src_size = reader.size()
    if src_size.isValid():
        reader.setScaledSize(src_size.scaled(size, size, Qt.KeepAspectRatio))

    image = reader.read()
    if image.isNull():
        return QPixmap()

    return QPixmap.fromImage(image)


class PreviewList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
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

    # ---------- Items ----------

    def add_path_item(self, path: str) -> bool:
        """Добавить элемент по пути к файлу (в памяти — только миниатюра)."""
        thumb = load_thumbnail(path)
        if thumb.isNull():
            logger.warning("Failed to load image from %s", path)
            return False

        item = QListWidgetItem(QIcon(thumb), os.path.basename(path))
        item.setData(PATH_ROLE, path)
        self.addItem(item)
        return True

    # ---------- Drag OUT (на холст) ----------

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return

        # Снимаем выделение на холсте — важно, чтобы не было
        # нескольких выделенных изображений
        window = self.window()
        if window and hasattr(window, "scene"):
            window.scene.clearSelection()

        path = item.data(PATH_ROLE)
        fallback = item.data(PIXMAP_ROLE)

        mime = QMimeData()
        mime.setData(MIME_PREVIEW_MARKER, b"1")

        if path:
            # Передаём только путь — полноразмерное изображение
            # загрузит принимающая сторона (экономия памяти)
            mime.setData(MIME_PREVIEW_PATH, str(path).encode("utf-8"))
        elif isinstance(fallback, QPixmap) and not fallback.isNull():
            mime.setImageData(fallback)
        else:
            return

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon().pixmap(THUMB_SIZE, THUMB_SIZE))
        drag.exec(Qt.CopyAction)

    def mousePressEvent(self, event):
        # При обращении к панели превью снимаем выделение на холсте
        window = self.window()
        if window and hasattr(window, "scene"):
            window.scene.clearSelection()

        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        # Контекстное меню превью-панели (удаление элемента)
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
            if not path.lower().endswith(IMAGE_EXTENSIONS):
                continue

            self.add_path_item(path)

        event.acceptProposedAction()


class PreviewPanel(QDockWidget):
    def __init__(self, parent=None):
        super().__init__(i18n.t('images'), parent)

        self.setAcceptDrops(True)   # ВАЖНО

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

    # ---------- Public API ----------

    def add_images_from_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            i18n.t('add_images'),
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )

        for path in files:
            self.add_path(path)

    def add_path(self, path: str) -> bool:
        """Добавить изображение по пути к файлу."""
        return self.list.add_path_item(path)

    def add_pixmap(self, pixmap: QPixmap, path: str = None):
        """Добавить изображение, вернувшееся с холста.

        Если известен путь к исходному файлу — храним только путь и
        миниатюру (экономия памяти). Иначе — fallback с полноразмерным
        QPixmap, чтобы не потерять данные.
        """
        if path and self.list.add_path_item(path):
            return

        if path:
            logger.warning(
                "Source file %s is unavailable; keeping full-size pixmap",
                path,
            )

        thumb = pixmap.scaled(
            THUMB_SIZE, THUMB_SIZE,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        item = QListWidgetItem(QIcon(thumb), "")
        item.setData(PIXMAP_ROLE, pixmap)
        item.setSizeHint(QSize(THUMB_SIZE, THUMB_SIZE))
        self.list.addItem(item)

    def remove_current_item(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)

    def remove_path(self, path) -> bool:
        """Удалить первый элемент с данным путём к файлу."""
        if not path:
            return False

        for row in range(self.list.count()):
            if self.list.item(row).data(PATH_ROLE) == path:
                self.list.takeItem(row)
                return True

        return False

    def remove_pixmap(self, pixmap) -> bool:
        """Удалить fallback-элемент, соответствующий данному pixmap."""
        if pixmap is None:
            return False

        key = pixmap.cacheKey()
        for row in range(self.list.count()):
            stored = self.list.item(row).data(PIXMAP_ROLE)
            if isinstance(stored, QPixmap) and stored.cacheKey() == key:
                self.list.takeItem(row)
                return True

        return False

    def remove_image(self, path=None, pixmap=None) -> bool:
        """Убрать элемент по пути или по fallback-pixmap (для undo-команд)."""
        if self.remove_path(path):
            return True
        if self.remove_pixmap(pixmap):
            return True

        logger.warning("remove_image: preview item was not found")
        return False

    def paths(self):
        """Пути всех элементов панели (None — если элемент без файла)."""
        return [
            self.list.item(row).data(PATH_ROLE)
            for row in range(self.list.count())
        ]

    def clear(self):
        self.list.clear()
