"""Загрузка изображений с ограничением рабочего разрешения.

Полноразмерная фотография в QPixmap стоит дорого: снимок 24 Мп
(6000×4000) занимает около 96 МБ. Десяток таких изображений на холсте —
уже почти гигабайт, хотя экран всё равно показывает максимум несколько
мегапикселей.

Поэтому:
- на холст грузится «рабочая» копия, уменьшенная до MAX_WORKING_SIDE;
- полноразмерный оригинал читается с диска только на время экспорта
  (см. ImageItem.paint), поэтому качество итогового файла не страдает.

QImageReader.setScaledSize декодирует файл сразу в нужном размере,
то есть полноразмерная копия в памяти при загрузке не создаётся вовсе.
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader, QPixmap

logger = logging.getLogger(__name__)

# Максимальная сторона рабочей копии изображения, px.
# 3200 px хватает для экранного редактирования и зума содержимого,
# при этом расход памяти падает примерно в 3-4 раза на больших фото.
MAX_WORKING_SIDE = 3200

# Полноразмерные изображения, загруженные на время экспорта: {путь: QPixmap}
_full_cache = {}

# «Тёплый» кэш рабочих копий: {(путь, max_side): QPixmap}.
# Заполняется фоновой предзагрузкой (см. core.async_loader) и живёт
# ровно до момента, пока проект не собран — потом очищается.
_working_cache = {}


def read_scaled_image(path: str, max_side: int = MAX_WORKING_SIDE):
    """Декодировать файл в QImage (можно вызывать из рабочего потока).

    QPixmap привязан к GUI-потоку, поэтому фоновые загрузчики
    (см. core.async_loader) работают именно с QImage, а конвертацию
    выполняет уже главный поток.
    """
    reader = QImageReader(path)
    reader.setAutoTransform(True)

    size = reader.size()
    if (
        max_side
        and size.isValid()
        and max(size.width(), size.height()) > max_side
    ):
        reader.setScaledSize(
            size.scaled(max_side, max_side, Qt.KeepAspectRatio)
        )

    image = reader.read()
    if image.isNull():
        logger.warning(
            "Failed to decode image %s: %s", path, reader.errorString()
        )

    return image


def load_working_pixmap(path: str, max_side: int = MAX_WORKING_SIDE) -> QPixmap:
    """Рабочая копия изображения (уменьшенная, если оригинал большой).

    Возвращает пустой QPixmap, если файл не удалось прочитать —
    вызывающая сторона проверяет это через isNull().
    """
    cached = _working_cache.get((path, max_side))
    if cached is not None and not cached.isNull():
        return cached

    image = read_scaled_image(path, max_side)
    if image.isNull():
        # Формат, который QImageReader не осилил с масштабированием —
        # пробуем обычную загрузку, чтобы не потерять изображение.
        pixmap = QPixmap(path)
        if pixmap.isNull():
            logger.warning("Failed to load image from %s", path)
        return pixmap

    return QPixmap.fromImage(image)


def put_working_pixmap(
    path: str, pixmap: QPixmap, max_side: int = MAX_WORKING_SIDE
):
    """Положить в «тёплый» кэш копию, прочитанную заранее (в фоне).

    Используется при открытии проекта: файлы декодируются в рабочих
    потоках, а сборка сцены забирает готовые пиксмапы вместо повторного
    чтения с диска.
    """
    if pixmap is not None and not pixmap.isNull():
        _working_cache[(path, max_side)] = pixmap


def clear_working_cache():
    """Очистить «тёплый» кэш (после того как проект собран)."""
    _working_cache.clear()


def image_size(path: str):
    """Размер изображения на диске без загрузки его в память.

    Нужен для старых файлов проекта, где геометрия сохранена
    в пикселях полноразмерного оригинала.
    """
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    return reader.size()


def oriented_image_size(path: str, hint: QPixmap = None):
    """Размер фотографии с учётом EXIF-поворота: (ширина, высота).

    QImageReader.size() отдаёт размер до применения поворота из EXIF,
    поэтому вертикальные снимки с телефона считались бы
    горизонтальными и вся раскладка ехала бы.

    Основной ориентир — готовая миниатюра (`hint`): её делал
    read(), то есть поворот в ней уже учтён. Если миниатюры нет,
    смотрим флаг преобразования — это дешевле, чем декодировать
    файл целиком.

    Возвращает None, если размер определить не удалось.
    """
    if not path:
        return None

    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()

    if not size.isValid() or size.width() <= 0 or size.height() <= 0:
        # Формат, по которому размер без чтения не узнать
        pixmap = load_working_pixmap(path)
        if pixmap.isNull():
            logger.warning("Cannot determine image size: %s", path)
            return None

        return pixmap.width(), pixmap.height()

    width = size.width()
    height = size.height()

    if hint is not None and not hint.isNull():
        # Квадратная миниатюра (плейсхолдер) об ориентации
        # ничего не говорит — сверяемся только по неквадратным
        if hint.width() != hint.height() and width != height:
            if (hint.width() > hint.height()) != (width > height):
                width, height = height, width

        return width, height

    try:
        # Значения от 4 и выше — варианты с поворотом на 90°/270°
        if int(reader.transformation()) >= 4:
            width, height = height, width
    except (TypeError, ValueError):
        # Нет информации о повороте — берём размер как есть
        logger.debug("No EXIF transformation info for %s", path)

    return width, height


def load_full_pixmap(path: str) -> QPixmap:
    """Полноразмерное изображение (кэшируется до clear_full_cache())."""
    cached = _full_cache.get(path)
    if cached is not None and not cached.isNull():
        return cached

    pixmap = QPixmap(path)
    if pixmap.isNull():
        logger.warning("Failed to load full-size image from %s", path)
        return pixmap

    _full_cache[path] = pixmap
    return pixmap


def clear_full_cache():
    """Освободить полноразмерные изображения, загруженные для экспорта."""
    _full_cache.clear()
