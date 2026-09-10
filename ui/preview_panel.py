import logging
import os

from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMenu,
    QMessageBox,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QToolButton,
    QWidget,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDrag,
    QFontMetrics,
    QIcon,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtCore import Qt, QMimeData, QRect, QSize, Signal

from core.async_loader import AsyncImageLoader
from core import file_types
from core import image_cache
import i18n

logger = logging.getLogger(__name__)

# Роли данных элемента списка:
# PATH_ROLE — путь к исходному файлу (str или None);
# PIXMAP_ROLE — полноразмерный QPixmap-fallback для элементов без файла.
PATH_ROLE = Qt.UserRole
PIXMAP_ROLE = Qt.UserRole + 1
# TOKEN_ROLE — номер фоновой загрузки миниатюры (None — уже готова)
TOKEN_ROLE = Qt.UserRole + 2

THUMB_SIZE = 96

# Геометрия элемента панели (см. PreviewItemDelegate):
# отступы, зазор между миниатюрой и подписью,
# сколько строк отводится на название файла
ITEM_PADDING = 4
ITEM_TEXT_GAP = 4
ITEM_NAME_LINES = 2

MIME_PREVIEW_MARKER = "application/x-preview-item"
MIME_PREVIEW_PATH = "application/x-preview-path"

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def mark_window_dirty(widget):
    """Пометить проект изменённым.

    ФИКС: содержимое панели сохраняется в .fcproj, но её правки
    не попадают в undo-стек, а значит и в QUndoStack.isClean(). Без
    этого вызова добавление и удаление фотографий в панели
    не считалось несохранённым изменением и терялось молча.
    """
    window = widget.window() if widget is not None else None

    if window is not None and hasattr(window, "mark_dirty"):
        window.mark_dirty()


def scaled_thumbnail(pixmap: QPixmap, size: int = THUMB_SIZE) -> QPixmap:
    """Миниатюра из уже загруженного пиксмапа (без чтения с диска)."""
    return pixmap.scaled(
        size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )


_placeholder_cache = None


def placeholder_pixmap(size: int = THUMB_SIZE) -> QPixmap:
    """Заглушка на время фоновой загрузки миниатюры.

    Картинка одна на все элементы и строится один раз: при добавлении
    сотни файлов перерисовывать её каждый раз было бы расточительно.
    """
    global _placeholder_cache

    if _placeholder_cache is None or _placeholder_cache.width() != size:
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(238, 238, 238))

        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor(198, 198, 198), 1))
        painter.drawRect(0, 0, size - 1, size - 1)
        painter.setPen(QPen(QColor(140, 140, 140), 1))
        painter.drawText(QRect(0, 0, size, size), Qt.AlignCenter, "...")
        painter.end()

        _placeholder_cache = pixmap

    return _placeholder_cache


def wrap_name(metrics: QFontMetrics, text: str, width: int, max_lines: int):
    """Разбить название файла на строки по ширине миниатюры.

    Перенос по словам здесь не помогает: в именах вроде
    "1PUfwjHNMqu2-DGEwfrdM9W1Kax2WAl92o8LA..." пробелов нет вообще,
    поэтому режем сами, а остаток сокращаем через «…».
    """
    lines = []
    rest = (text or "").strip()

    while rest and len(lines) < max_lines:
        if metrics.horizontalAdvance(rest) <= width:
            lines.append(rest)
            return lines

        if len(lines) == max_lines - 1:
            # В последней строке вырезаем середину: видно
            # и начало имени, и расширение файла
            lines.append(metrics.elidedText(rest, Qt.ElideMiddle, width))
            return lines

        # Максимальное число символов, влезающих в строку
        fit = 1
        low, high = 1, len(rest)
        while low <= high:
            middle = (low + high) // 2
            if metrics.horizontalAdvance(rest[:middle]) <= width:
                fit = middle
                low = middle + 1
            else:
                high = middle - 1

        lines.append(rest[:fit])
        rest = rest[fit:]

    return lines


class PreviewItemDelegate(QStyledItemDelegate):
    """Миниатюра слева, название файла под ней.

    ФИКС: штатная отрисовка IconMode центрирует миниатюру по
    ширине элемента, а ширину задаёт подпись. У файлов с
    длинными именами элемент разрастался, и фото уезжало за
    правый край панели — чтобы его увидеть, панель приходилось
    растягивать почти на половину окна. Теперь ширина элемента
    фиксирована (по миниатюре), фото прижато к левому краю,
    а имя файла переносится и обрезается под её ширину.
    """

    def sizeHint(self, option, index):
        metrics = QFontMetrics(option.font)

        return QSize(
            THUMB_SIZE + ITEM_PADDING * 2,
            THUMB_SIZE
            + ITEM_TEXT_GAP
            + metrics.height() * ITEM_NAME_LINES
            + ITEM_PADDING * 2,
        )

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        text = opt.text

        # Фон, выделение и рамку фокуса оставляем теме, а
        # содержимое (миниатюра и подпись) размещаем сами
        opt.text = ""
        opt.icon = QIcon()

        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        left = opt.rect.left() + ITEM_PADDING
        top = opt.rect.top() + ITEM_PADDING

        painter.save()

        icon = index.data(Qt.DecorationRole)
        if isinstance(icon, QIcon):
            pixmap = icon.pixmap(QSize(THUMB_SIZE, THUMB_SIZE))
            if not pixmap.isNull():
                # Учитываем HiDPI: на экранах с масштабом 125-200%
                # pixmap физически больше своего логического размера
                ratio = pixmap.devicePixelRatio() or 1.0
                height = int(pixmap.height() / ratio)

                # По горизонтали — строго по левому краю (как и
                # подпись), по вертикали — по центру места под фото
                painter.drawPixmap(
                    left,
                    top + max(0, (THUMB_SIZE - height) // 2),
                    pixmap,
                )

        if text:
            metrics = QFontMetrics(opt.font)
            role = (
                QPalette.HighlightedText
                if opt.state & QStyle.State_Selected
                else QPalette.Text
            )
            painter.setPen(opt.palette.color(role))

            line_top = top + THUMB_SIZE + ITEM_TEXT_GAP
            for line in wrap_name(
                metrics, text, THUMB_SIZE, ITEM_NAME_LINES
            ):
                painter.drawText(
                    QRect(left, line_top, THUMB_SIZE, metrics.height()),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    line,
                )
                line_top += metrics.height()

        painter.restore()


class PreviewList(QListWidget):
    # Сколько миниатюр ещё читается с диска (для индикатора в заголовке)
    loadingChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Фоновая загрузка миниатюр (см. core.async_loader)
        self._loader = AsyncImageLoader(self)
        self._loader.loaded.connect(self._on_thumbnail_loaded)
        self._loader.progressChanged.connect(self.loadingChanged)
        self._token_counter = 0

        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSpacing(8)

        # Миниатюра слева, название под ней: ширина элемента
        # больше не зависит от длины имени файла
        self.setItemDelegate(PreviewItemDelegate(self))
        self.setUniformItemSizes(True)
        self.setTextElideMode(Qt.ElideMiddle)

        # ВАЖНО
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setSelectionMode(QListWidget.SingleSelection)

    # ---------- Items ----------

    def add_path_item(self, path: str, thumbnail: QPixmap = None) -> bool:
        """Добавить элемент по пути к файлу (в памяти — только миниатюра).

        Файл читается в фоне: раньше добавление полусотни фотографий
        полностью блокировало окно на несколько секунд. До готовности
        показывается серый плейсхолдер; если файл оказался нечитаемым,
        элемент уби��ается из списка.

        `thumbnail` передают, когда изображение уже есть в памяти
        (возврат с холста) — тогда читать диск второй раз незачем.
        """
        if not path or not os.path.isfile(path):
            logger.warning("Image file is not available: %s", path)
            return False

        item = QListWidgetItem(os.path.basename(path))
        item.setData(PATH_ROLE, path)
        # Подпись обрезана по ширине миниатюры, поэтому
        # полное имя с путём остаётся во всплывающей подсказке
        item.setToolTip(path)

        if thumbnail is not None and not thumbnail.isNull():
            item.setIcon(QIcon(scaled_thumbnail(thumbnail)))
            self.addItem(item)
            return True

        self._token_counter += 1
        token = self._token_counter

        item.setIcon(QIcon(placeholder_pixmap()))
        item.setData(TOKEN_ROLE, token)
        self.addItem(item)

        self._loader.submit(path, token=token, max_side=THUMB_SIZE)
        return True

    def _item_by_token(self, token):
        """Найти элемент по токену загрузки.

        Именно поиск, а не словарь со ссылками: элемент могли удалить,
        пока файл читался, а обращение к удалённому QListWidgetItem
        уронило бы приложение.
        """
        for row in range(self.count()):
            item = self.item(row)
            if item is not None and item.data(TOKEN_ROLE) == token:
                return item

        return None

    def _on_thumbnail_loaded(self, token, path, image):
        """Фоновая загрузка завершилась (вызывается в GUI-потоке)."""
        item = self._item_by_token(token)
        if item is None:
            return

        if image is None or image.isNull():
            logger.warning("Failed to load image from %s", path)
            row = self.row(item)
            if row >= 0:
                self.takeItem(row)
            return

        item.setIcon(QIcon(QPixmap.fromImage(image)))
        item.setData(TOKEN_ROLE, None)

    def cancel_pending_loads(self):
        """Снять незавершённые фоновые загрузки."""
        self._loader.cancel_all()

    def clear(self):
        # Миниатюры для удалённых элементов больше не нужны
        self.cancel_pending_loads()
        super().clear()

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
                mark_window_dirty(self)

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

        added = 0
        for url in md.urls():
            path = url.toLocalFile()
            if not path.lower().endswith(IMAGE_EXTENSIONS):
                continue

            if self.add_path_item(path):
                added += 1

        if added:
            mark_window_dirty(self)

        event.acceptProposedAction()


class PreviewTitleBar(QWidget):
    """Собственный заголовок панели превью.

    Штатный заголовок QDockWidget не позволяет добавлять свои кнопки,
    поэтому собираем его вручную: название + кнопка очистки,
    кнопка открепления и крестик. Сам виджет не перехватывает
    события ��ыши, поэтому панель по-прежнему перетаскивается
    за заголовок.
    """

    def __init__(self, dock: QDockWidget):
        super().__init__(dock)

        self.dock = dock

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 2, 2)
        layout.setSpacing(2)

        self.title_label = QLabel(dock.windowTitle(), self)
        layout.addWidget(self.title_label)
        layout.addStretch(1)

        style = self.style()

        # Кнопка очистки панели (рядом с откреплением и крестиком)
        self.clear_button = self._make_button(
            style.standardIcon(QStyle.SP_DialogResetButton)
        )
        self.clear_button.clicked.connect(dock.clear_with_confirmation)
        layout.addWidget(self.clear_button)

        # Штатные кнопки воссоздаём вручную — со своим заголовком
        # QDockWidget их больше не рисует
        self.float_button = self._make_button(
            style.standardIcon(QStyle.SP_TitleBarNormalButton)
        )
        self.float_button.clicked.connect(self._toggle_floating)
        layout.addWidget(self.float_button)

        self.close_button = self._make_button(
            style.standardIcon(QStyle.SP_TitleBarCloseButton)
        )
        self.close_button.clicked.connect(dock.close)
        layout.addWidget(self.close_button)

        self._apply_features()
        self.retranslate()

    def _make_button(self, icon) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(icon)
        button.setIconSize(QSize(14, 14))
        button.setFixedSize(QSize(20, 20))
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)
        return button

    def _toggle_floating(self):
        self.dock.setFloating(not self.dock.isFloating())

    def _apply_features(self):
        """Скрыть кнопки, если соответствующие возможности отключены."""
        features = self.dock.features()
        self.float_button.setVisible(
            bool(features & QDockWidget.DockWidgetFloatable)
        )
        self.close_button.setVisible(
            bool(features & QDockWidget.DockWidgetClosable)
        )

    def retranslate(self):
        """Обновить тексты при смене языка."""
        self.title_label.setText(self.dock.windowTitle())
        self.clear_button.setToolTip(i18n.t('clear_panel'))
        self.float_button.setToolTip(i18n.t('float_panel'))
        self.close_button.setToolTip(i18n.t('close_panel'))


class PreviewPanel(QDockWidget):
    def __init__(self, parent=None):
        super().__init__(i18n.t('images'), parent)

        self.setAcceptDrops(True)   # ВАЖНО

        self.list = PreviewList(self)
        self.setWidget(self.list)

        # Индикатор фоновой загрузки миниатюр в заголовке панели
        self._pending_loads = 0
        self.list.loadingChanged.connect(self._on_loading_changed)

        self.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        )

        # Собственный заголовок с кнопкой очистки панели
        self.title_bar = PreviewTitleBar(self)
        self.setTitleBarWidget(self.title_bar)

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

    def setWindowTitle(self, title: str):
        # Заголовок рисует наш виджет, поэтому при смене языка
        # обновляем его тексты вместе с заголовком окна
        super().setWindowTitle(title)

        title_bar = getattr(self, "title_bar", None)
        if title_bar is not None:
            title_bar.retranslate()

    def clear_with_confirmation(self):
        """Очистить панель от всех изображений после подтверждения.

        Затрагивает только панель: изображения, уже размещённые
        на холсте или в слотах шаблона, остаются на месте.
        """
        if self.list.count() == 0:
            return

        msg = QMessageBox(self)
        msg.setWindowTitle(i18n.t('confirm_clear_title'))
        msg.setText(i18n.t('confirm_clear_text'))
        msg.setIcon(QMessageBox.Warning)
        yes = msg.addButton(i18n.t('confirm'), QMessageBox.AcceptRole)
        msg.addButton(i18n.t('cancel'), QMessageBox.RejectRole)
        msg.exec()

        if msg.clickedButton() is yes:
            self.clear()
            mark_window_dirty(self)

    def set_drop_highlight(self, on: bool):
        """Подсветить панель как цель для броска с холста.

        Используется при обратном drag&drop (холст → превью): мышь
        в этот момент захвачена QGraphicsView, поэтому штатные
        drag-события сюда не доходят и подсветкой управляет ImageItem.
        """
        self.list.setStyleSheet(
            "QListWidget { border: 2px solid #2d7ff9; }" if on else ""
        )

    # ---------- Public API ----------

    def add_images_from_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            i18n.t('add_images'),
            "",
            file_types.images_filter()
        )

        added = 0
        for path in files:
            if self.add_path(path):
                added += 1

        # Отменённый диалог не должен делать проект «грязным»
        if added:
            mark_window_dirty(self)

    def add_path(self, path: str) -> bool:
        """Добавить изображение по пути к файлу."""
        return self.list.add_path_item(path)

    def add_pixmap(self, pixmap: QPixmap, path: str = None):
        """Добавить изображение, вернувшееся с холста.

        Если известен путь к исходному файлу — храним только путь и
        миниатюру (экономия памяти). Иначе — fallback с полноразмерным
        QPixmap, чтобы не потерять данные.
        """
        # Миниатюру берём из уже загруженного пиксмапа: повторно
        # читать тот же файл с диска незачем
        if path and self.list.add_path_item(path, thumbnail=pixmap):
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
        # Размер элемента задаёт PreviewItemDelegate: свой
        # sizeHint ломал бы единую сетку панели
        self.list.addItem(item)

    def remove_current_item(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            mark_window_dirty(self)

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

    def image_entries(self):
        """Данные всех элементов панели для подбора холста.

        На каждый элемент: путь к файлу, fallback-pixmap и размер
        фотографии с учётом EXIF-поворота. Размер равен None,
        если файл прочитать не удалось — такой кадр в расчёте
        не участвует.
        """
        entries = []

        for row in range(self.list.count()):
            item = self.list.item(row)
            if item is None:
                continue

            path = item.data(PATH_ROLE)
            pixmap = item.data(PIXMAP_ROLE)

            size = None

            if isinstance(pixmap, QPixmap) and not pixmap.isNull():
                # Элемент без файла (вставка из буфера) —
                # размер известен без обращения к диску
                size = (pixmap.width(), pixmap.height())
            elif path:
                # Миниатюра годится как ориентир только когда
                # уже загрузилась: у плейсхолдера своя форма
                hint = None
                if item.data(TOKEN_ROLE) is None:
                    icon = item.icon()
                    if not icon.isNull():
                        hint = icon.pixmap(THUMB_SIZE, THUMB_SIZE)

                size = image_cache.oriented_image_size(path, hint)

            entries.append(
                {"path": path, "pixmap": pixmap, "size": size}
            )

        return entries

    def paths(self):
        """Пути всех элементов панели (None — если элемент без файла)."""
        return [
            self.list.item(row).data(PATH_ROLE)
            for row in range(self.list.count())
        ]

    def clear(self):
        self.list.clear()

    def cancel_pending_loads(self):
        """Снять фоновые загрузки (закрытие окна, смена проекта)."""
        self.list.cancel_pending_loads()

    # ---------- Индикатор загрузки ----------

    def _on_loading_changed(self, pending: int):
        self._pending_loads = max(0, int(pending))
        self.refresh_title()

    def refresh_title(self):
        """Заголовок панели: название плюс счётчик читаемых файлов."""
        title = i18n.t('images')

        if getattr(self, "_pending_loads", 0) > 0:
            title = "%s — %s %d" % (
                title, i18n.t('loading'), self._pending_loads
            )

        self.setWindowTitle(title)
