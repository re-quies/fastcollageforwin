"""Сохранение и загрузка проекта коллажа в формате JSON.

Формат файла (версия 1):
- app / version — сигнатура и версия формата;
- mode, canvas, style, swap_delay_ms — общие настройки
  (style — отступы/скругления/фон, добавлен позже и может
  отсутствовать в ранних файлах);
- slots — геометрия слотов шаблона и вложенные изображения;
- free_items — свободные изображения (позиция/масштаб/поворот/слой);
- preview — пути изображений в панели превью.

В проект сохраняются ПУТИ к исходным файлам, а не пиксели, поэтому
файл проекта лёгкий, но требует, чтобы исходные изображения оставались
на своих местах.

Относительные пути
-------------------
Только абсолютных путей было недостаточно: стоило переименовать
папку, перенести её на другой диск или открыть проект на другом
компьютере — и весь коллаж оказывался пустым. Поэтому рядом с
абсолютным путём записывается путь относительно папки проекта:

- в записи изображения — ключ "rel";
- для панели превью — параллельный список "preview_rel".

Оба ключа НЕОБЯЗАТЕЛЬНЫЕ и добавлены без смены версии формата:
новые файлы открываются старой версией программы (она просто
не заметит лишние ключи), а старые файлы — новой (тогда работает
только абсолютный путь). При открытии пути приводятся к
актуальным через resolve_paths(), а потерянные файлы можно найти
в другой папке через relink().
"""

import logging
import os

from PySide6.QtCore import QPointF, QRectF

from canvas.image_item import ImageItem
from canvas.scene import CanvasScene
from canvas.slot_item import TemplateSlotItem
from core import file_types
from core import image_cache
from core.canvas_presets import DEFAULT_CANVAS_SIZE
from core.collage_mode import CollageMode

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
APP_ID = "FastCollageForWin"


def serialize(window, project_path=None):
    """Собрать состояние приложения в JSON-совместимый словарь.

    project_path — путь, по которому файл проекта будет сохранён.
    Он нужен, чтобы рядом с абсолютным путём каждой фотографии
    записать относительный: именно он позволяет перенести папку
    с проектом и фотографиями в другое место или на другой
    компьютер и открыть проект без потерь. Если путь не задан,
    записываются только абсолютные пути (как раньше).

    Возвращает (data, skipped): skipped — число изображений, которые
    не удалось сохранить (нет исходного файла на диске).
    """
    scene = window.scene
    skipped = 0
    base_dir = _base_dir(project_path)

    data = {
        "app": APP_ID,
        "version": FORMAT_VERSION,
        "mode": "template" if scene.is_template_mode else "free",
        "canvas": {
            "width": scene.canvas_width,
            "height": scene.canvas_height,
        },
        # Оформление: отступы, скругления, фон (см. CanvasScene)
        "style": scene.layout_style(),
        "swap_delay_ms": window.swap_delay_ms,
        "template_image_count": scene.template_image_count,
        "slots": [],
        "free_items": [],
        "preview": [],
        # Пути панели превью относительно папки проекта:
        # список идёт строго параллельно "preview"
        "preview_rel": [],
    }

    # --- Слоты шаблона ---
    for slot in scene.template_slots:
        record = {
            "index": slot.index,
            "x": slot.scenePos().x(),
            "y": slot.scenePos().y(),
            "w": slot.rect().width(),
            "h": slot.rect().height(),
            "z": slot.base_z(),
            "image": None,
        }

        image = slot.image_item
        if image is not None:
            if getattr(image, "source_path", None):
                record["image"] = _image_record(image, base_dir)
            else:
                skipped += 1
                logger.warning(
                    "Slot %s image has no source file; skipped", slot.index
                )

        data["slots"].append(record)

    # --- Свободные элементы ---
    for item in scene.items():
        if not isinstance(item, ImageItem):
            continue
        if isinstance(item.parentItem(), TemplateSlotItem):
            continue

        if not getattr(item, "source_path", None):
            skipped += 1
            logger.warning("Free image has no source file; skipped")
            continue

        record = _image_record(item, base_dir)
        record.update({
            "pos": [item.pos().x(), item.pos().y()],
            "scale": item.scale(),
            "rotation": item.rotation(),
            "z": item.zValue(),
        })
        data["free_items"].append(record)

    # --- Панель превью ---
    for path in window.preview_panel.paths():
        if path:
            data["preview"].append(path)
            data["preview_rel"].append(_relative_path(path, base_dir))
        else:
            skipped += 1
            logger.warning("Preview image has no source file; skipped")

    return data, skipped


def _image_record(item, base_dir=None):
    """Запись об изображении: абсолютный путь плюс относительный.

    Абсолютный путь остаётся главным ключом ради совместимости:
    файлы, сохранённые новой версией, открываются старой.
    Относительный ("rel") — необязательный, но при открытии
    проверяется ПЕРВЫМ (см. resolve_paths).
    """
    record = {
        "path": item.source_path,
        "view_state": item.view_state(),
    }

    rel = _relative_path(item.source_path, base_dir)
    if rel:
        record["rel"] = rel

    return record


def image_paths(data):
    """Пути к изображениям холста в проекте (без дублей).

    Нужны для фоновой предзагрузки: файлы читаются в рабочих
    потоках, а apply() затем берёт готовые пиксмапы из кэша.

    Содержимое панели превью сюда не входит: панель грузит свои
    миниатюры сама и тоже асинхронно.
    """
    if not isinstance(data, dict):
        return []

    paths = []
    seen = set()

    def _add(path):
        if not path or path in seen:
            return
        seen.add(path)
        paths.append(path)

    for record in data.get("slots") or []:
        image = (record or {}).get("image")
        if image:
            _add(image.get("path"))

    for record in data.get("free_items") or []:
        _add((record or {}).get("path"))

    return paths


def apply(window, data):
    """Восстановить состояние приложения из словаря проекта.

    Возвращает список путей к файлам, которые не удалось загрузить.
    Бросает ValueError, если файл не является проектом поддерживаемой
    версии (проверка выполняется ДО изменения состояния окна).
    """
    if not isinstance(data, dict) or data.get("app") != APP_ID:
        raise ValueError("Not a FastCollageForWin project file")

    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError):
        raise ValueError("Invalid project version")

    if version < 1 or version > FORMAT_VERSION:
        raise ValueError("Unsupported project version: %s" % version)

    canvas = data.get("canvas", {})
    # Размер по умолчанию — из общего источника, а не третье
    # независимое значение (было 1920×1080 здесь против 3840×2160
    # в CanvasScene)
    width = int(canvas.get("width", DEFAULT_CANVAS_SIZE[0]))
    height = int(canvas.get("height", DEFAULT_CANVAS_SIZE[1]))
    is_template = data.get("mode") == "template"

    window.undo_stack.clear()
    window.swap_delay_ms = int(data.get("swap_delay_ms", window.swap_delay_ms))
    window.collage_mode = (
        CollageMode.TEMPLATE if is_template else CollageMode.FREE
    )

    scene = CanvasScene(width, height)
    scene.swap_delay_ms = window.swap_delay_ms
    scene.is_template_mode = is_template

    # Старые проекты без блока "style" откроются с оформлением
    # по умолчанию (без отступов, белый фон)
    scene.apply_layout_style(data.get("style") or {})

    # ФИКС (утечка памяти): set_scene освобождает предыдущую сцену
    # вместе со всеми загруженными изображениями
    window.set_scene(scene)

    missing = []

    # --- Слоты шаблона ---
    if is_template:
        scene.template_image_count = int(
            data.get("template_image_count", len(data.get("slots", [])))
        )

        for record in data.get("slots", []):
            rect = QRectF(
                float(record["x"]),
                float(record["y"]),
                float(record["w"]),
                float(record["h"]),
            )
            slot = TemplateSlotItem(
                rect,
                int(record.get("index", len(scene.template_slots))),
            )
            slot.set_base_z(float(record.get("z", 0.0)))
            scene.addItem(slot)
            scene.template_slots.append(slot)

            image_record = record.get("image")
            if image_record:
                item = _create_item(image_record, scene, missing)
                if item is not None:
                    scene.addItem(item)
                    # ФИКС: keep_offset сохраняет панорамирование внутри
                    # слота. Изображение только что создано и родителя
                    # ещё не имеет (parentItem() is None), поэтому
                    # accept_image считал слот «новым» и обнулял
                    # slot_offset: проект открывался с фотографиями,
                    # заново отцентрированными по своим слотам.
                    slot.accept_image(item, keep_offset=True)

    # --- Свободные элементы ---
    for record in data.get("free_items", []):
        item = _create_item(record, scene, missing)
        if item is None:
            continue

        scene.addItem(item)

        pos = record.get("pos", [0.0, 0.0])
        item.setPos(QPointF(float(pos[0]), float(pos[1])))

        # Масштаб сохранён относительно того размера пиксмапа,
        # который был на момент сохранения. Сейчас на холст грузится
        # рабочая копия (core.image_cache), поэтому компенсируем разницу —
        # иначе старые проекты открывались бы с уехавшей геометрией.
        item.setScale(
            float(record.get("scale", 1.0))
            * getattr(item, "_project_base_scale", 1.0)
        )
        item.setRotation(float(record.get("rotation", 0.0)))
        item.setZValue(float(record.get("z", 0.0)))

    # --- Панель превью ---
    panel = window.preview_panel
    panel.clear()
    for path in data.get("preview", []):
        if not panel.add_path(path):
            missing.append(path)

    return missing


def _create_item(record, scene, missing):
    path = record.get("path")
    if not path:
        return None

    # ФИКС (память): на холст грузится рабочая копия
    pixmap = image_cache.load_working_pixmap(path)
    if pixmap.isNull():
        logger.warning("Missing or unreadable image: %s", path)
        missing.append(path)
        return None

    view_state = dict(record.get("view_state") or {})

    # Проекты, сохранённые до появления рабочих копий, хранили
    # координаты в пикселях полноразмерного оригинала
    if "base_size" not in view_state:
        full = image_cache.image_size(path)
        if full.isValid():
            view_state["base_size"] = [full.width(), full.height()]

    item = ImageItem(pixmap, path)
    item.apply_view_state(view_state)

    # Коэффициент для восстановления масштаба свободных элементов
    item._project_base_scale = _base_scale(view_state, pixmap)

    delay = getattr(scene, "swap_delay_ms", None)
    if delay is not None:
        item._hover_timer.setInterval(int(delay))

    return item


def _base_scale(view_state, pixmap):
    """Отношение сохранённого размера картинки к текущему."""
    base = view_state.get("base_size")

    if (
        isinstance(base, (list, tuple))
        and len(base) == 2
        and float(base[0]) > 0
        and pixmap.width() > 0
    ):
        return float(base[0]) / pixmap.width()

    return 1.0


# ---------------------------------------------------------------------------
# Пути к файлам: относительные пути и переподключение потерянных
# ---------------------------------------------------------------------------

# Расширения, среди которых ищем потерянные файлы
# Список расширений один для всего приложения (core/file_types.py):
# раньше он дублировался здесь и в четырёх фильтрах диалогов
_IMAGE_EXTENSIONS = file_types.IMAGE_EXTENSIONS

# Предел обхода при поиске: указать корень диска вместо папки
# с фотографиями — лёгкая ошибка, а полный обход диска выглядит
# как зависание программы
MAX_RELINK_FILES = 20000


def _base_dir(project_path):
    """Папка файла проекта или None."""
    if not project_path:
        return None

    try:
        return os.path.dirname(os.path.abspath(str(project_path))) or None
    except (OSError, ValueError):
        return None


def _relative_path(path, base_dir):
    """Путь относительно папки проекта или None.

    None возвращается, когда относительного пути не существует:
    на Windows файл может лежать на другом диске (D:\\ при проекте
    на C:\\). Разделители всегда пишутся прямыми слешами:
    такой файл проекта читается и на других платформах, а
    os.path.join() на Windows прямые слеши понимает.
    """
    if not path or not base_dir:
        return None

    try:
        absolute = os.path.abspath(str(path))

        if (
            os.path.splitdrive(absolute)[0].lower()
            != os.path.splitdrive(base_dir)[0].lower()
        ):
            return None

        return os.path.relpath(absolute, base_dir).replace(os.sep, "/")
    except (OSError, ValueError):
        return None


def _resolve_path(path, rel, base_dir):
    """Актуальный путь к файлу или None, если файла нет.

    Относительный путь проверяется ПЕРВЫМ и это важно: если
    папку с проектом скопировали, то по абсолютному пути лежат
    СТАРЫЕ файлы, а редактировать надо те, что рядом с копией.
    """
    if rel and base_dir:
        candidate = os.path.normpath(os.path.join(base_dir, str(rel)))
        if os.path.isfile(candidate):
            return candidate

    if path and os.path.isfile(str(path)):
        return str(path)

    return None


def _image_records(data):
    """Записи изображений холста (слоты и свободные элементы)."""
    records = []

    for record in data.get("slots") or []:
        image = (record or {}).get("image")
        if image:
            records.append(image)

    for record in data.get("free_items") or []:
        if record:
            records.append(record)

    return records


def resolve_paths(data, project_path=None):
    """Привести пути в проекте к актуальным для этой машины.

    Словарь правится на месте ДО того, как его увидит apply():
    так предзагрузка и сборка сцены работают с одними и теми
    же файлами.

    Возвращает список путей, которые найти не удалось (без дублей).
    """
    if not isinstance(data, dict):
        return []

    base_dir = _base_dir(project_path)
    missing = []
    seen = set()

    def _remember_missing(path):
        if not path or path in seen:
            return
        seen.add(path)
        missing.append(path)

    for record in _image_records(data):
        path = record.get("path")
        found = _resolve_path(path, record.get("rel"), base_dir)

        if found is None:
            _remember_missing(path)
        else:
            record["path"] = found

    # Панель превью: два параллельных списка, причём "preview_rel"
    # может отсутствовать вовсе (файл от старой версии)
    preview = list(data.get("preview") or [])
    rels = list(data.get("preview_rel") or [])

    for index, path in enumerate(preview):
        rel = rels[index] if index < len(rels) else None
        found = _resolve_path(path, rel, base_dir)

        if found is None:
            _remember_missing(path)
        else:
            preview[index] = found

    data["preview"] = preview

    if missing:
        logger.warning("Project: %d image file(s) not found", len(missing))

    return missing


def _build_name_index(folder, max_files=MAX_RELINK_FILES):
    """Индекс «имя файла → путь» по указанной папке и вложенным."""
    index = {}
    count = 0

    for root, _dirs, files in os.walk(folder):
        for name in files:
            if os.path.splitext(name)[1].lower() not in _IMAGE_EXTENSIONS:
                continue

            # Первое встретившееся имя выигрывает: обход идёт сверху,
            # и файл в корне вероятнее нужного, чем одноимённый
            # из глубокой подпапки
            index.setdefault(name.lower(), os.path.join(root, name))
            count += 1

            if count >= max_files:
                logger.warning(
                    "Relink: stopped after %d file(s) in %s", count, folder
                )
                return index

    return index


def relink(data, folder):
    """Найти потерянные файлы в другой папке — по имени файла.

    Типичные случаи: фотографии переложили в другую папку,
    скопировали с флешки с другой буквой диска или принесли
    проект на другой компьютер. Раньше оставалось только
    сообщение со списком потерянного и пустые слоты.

    Возвращает (сколько восстановлено, список так и не найденных).
    """
    if not isinstance(data, dict) or not folder:
        return 0, []

    index = _build_name_index(folder)

    fixed = 0
    missing = []
    seen = set()

    def _lookup(path):
        """Новый путь для потерянного файла или None."""
        if not path:
            return None

        candidate = index.get(os.path.basename(str(path)).lower())
        if candidate and os.path.isfile(candidate):
            return candidate

        return None

    def _remember_missing(path):
        if not path or path in seen:
            return
        seen.add(path)
        missing.append(path)

    for record in _image_records(data):
        path = record.get("path")

        # Найденные файлы не трогаем: resolve_paths() уже привёл их
        # к актуальным, и поиск по имени мог бы испортить верный путь
        if path and os.path.isfile(str(path)):
            continue

        found = _lookup(path)
        if found is None:
            _remember_missing(path)
            continue

        record["path"] = found
        # Старый относительный путь теперь врёт — его пересчитает
        # следующее сохранение
        record.pop("rel", None)
        fixed += 1

    preview = list(data.get("preview") or [])
    rels = list(data.get("preview_rel") or [])

    for position, path in enumerate(preview):
        if path and os.path.isfile(str(path)):
            continue

        found = _lookup(path)
        if found is None:
            _remember_missing(path)
            continue

        preview[position] = found
        if position < len(rels):
            rels[position] = None
        fixed += 1

    data["preview"] = preview
    if rels:
        data["preview_rel"] = rels

    logger.info(
        "Relink: %d file(s) restored from %s, %d still missing",
        fixed,
        folder,
        len(missing),
    )

    return fixed, missing
