"""Сохранение и загрузка проекта коллажа в формате JSON.

Формат файла (версия 1):
- app / version — сигнатура и версия формата;
- mode, canvas, swap_delay_ms — общие настройки;
- slots — геометрия слотов шаблона и вложенные изображения;
- free_items — свободные изображения (позиция/масштаб/поворот/слой);
- preview — пути изображений в панели превью.

В проект сохраняются ПУТИ к исходным файлам, а не пиксели, поэтому
файл проекта лёгкий, но требует, чтобы исходные изображения оставались
на своих местах.
"""

import logging

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPixmap

from canvas.image_item import ImageItem
from canvas.scene import CanvasScene
from canvas.slot_item import TemplateSlotItem
from core.collage_mode import CollageMode

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
APP_ID = "FastCollageForWin"


def serialize(window):
    """Собрать состояние приложения в JSON-совместимый словарь.

    Возвращает (data, skipped): skipped — число изображений, которые
    не удалось сохранить (нет исходного файла на диске).
    """
    scene = window.scene
    skipped = 0

    data = {
        "app": APP_ID,
        "version": FORMAT_VERSION,
        "mode": "template" if scene.is_template_mode else "free",
        "canvas": {
            "width": scene.canvas_width,
            "height": scene.canvas_height,
        },
        "swap_delay_ms": window.swap_delay_ms,
        "template_image_count": scene.template_image_count,
        "slots": [],
        "free_items": [],
        "preview": [],
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
                record["image"] = _image_record(image)
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

        record = _image_record(item)
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
        else:
            skipped += 1
            logger.warning("Preview image has no source file; skipped")

    return data, skipped


def _image_record(item):
    return {
        "path": item.source_path,
        "view_state": item.view_state(),
    }


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
    width = int(canvas.get("width", 1920))
    height = int(canvas.get("height", 1080))
    is_template = data.get("mode") == "template"

    window.undo_stack.clear()
    window.swap_delay_ms = int(data.get("swap_delay_ms", window.swap_delay_ms))
    window.collage_mode = (
        CollageMode.TEMPLATE if is_template else CollageMode.FREE
    )

    scene = CanvasScene(width, height)
    scene.swap_delay_ms = window.swap_delay_ms
    scene.is_template_mode = is_template

    window.scene = scene
    window.view.setScene(scene)

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
                    slot.accept_image(item)

    # --- Свободные элементы ---
    for record in data.get("free_items", []):
        item = _create_item(record, scene, missing)
        if item is None:
            continue

        scene.addItem(item)

        pos = record.get("pos", [0.0, 0.0])
        item.setPos(QPointF(float(pos[0]), float(pos[1])))
        item.setScale(float(record.get("scale", 1.0)))
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

    pixmap = QPixmap(path)
    if pixmap.isNull():
        logger.warning("Missing or unreadable image: %s", path)
        missing.append(path)
        return None

    item = ImageItem(pixmap, path)
    item.apply_view_state(record.get("view_state") or {})

    delay = getattr(scene, "swap_delay_ms", None)
    if delay is not None:
        item._hover_timer.setInterval(int(delay))

    return item
