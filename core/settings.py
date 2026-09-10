"""Пользовательские настройки приложения (QSettings).

Здесь собрано всё, что должно переживать перезапуск: язык
интерфейса, задержка обмена, геометрия окна и раскладка панелей,
последние папки диалогов, список недавних проектов и оформление
холста по умолчанию.

Хранилище — штатное для платформы (реестр в Windows, ~/.config в
других ОС); путь задаётся парой ORGANIZATION/APPLICATION — менять
их нельзя, иначе настройки пользователя «потеряются».

Значения из QSettings приходят строками, поэтому каждое читается
через явный конвертер с запасным значением: испорченный или
вручную отредактированный ключ не должен ронять запуск.
"""

import os

from PySide6.QtCore import QByteArray, QSettings

ORGANIZATION = "FastCollageForWin"
APPLICATION = "FastCollage"

# ФИКС: список языков был зашит тройкой ru/en/es, поэтому выбор
# китайского или арабского не сохранялся между запусками:
# set_language() молча отбрасывал код, а при старте интерфейс
# возвращался к русскому. Теперь список берётся из i18n
# (единый источник), а зашитый перечень остаётся страховкой.
try:
    import i18n as _i18n

    SUPPORTED_LANGUAGES = tuple(_i18n.SUPPORTED_LANGUAGES)
except Exception:
    SUPPORTED_LANGUAGES = ("ru", "en", "es", "zh", "ar")

DEFAULT_LANGUAGE = "ru"

MAX_RECENT_PROJECTS = 8
MAX_SWAP_DELAY_MS = 5000

# Параметры экспорта запоминаются между запусками: обычно
# человек выбирает масштаб один раз и дальше жмёт «ОК».
#
# ЕДИНЫЙ ИСТОЧНИК: раньше список масштабов был зашит в
# ui/export_dialog.py как (25, 50, 100, 150, 200), а границы
# сохраняемого значения жили здесь как 10…400. Значения вне списка
# (например, 300 %) успешно сохранялись, но диалог не находил их
# у себя и молча возвращал 100 %. Теперь и список, и границы —
# отсюда, а границы выводятся из самого списка.
EXPORT_SCALES = (10, 25, 50, 75, 100, 150, 200, 300, 400)

DEFAULT_EXPORT_SCALE = 100
MIN_EXPORT_SCALE = min(EXPORT_SCALES)
MAX_EXPORT_SCALE = max(EXPORT_SCALES)

DEFAULT_EXPORT_QUALITY = 92
MIN_EXPORT_QUALITY = 10
MAX_EXPORT_QUALITY = 100

# Глубина истории отмен. QUndoStack по умолчанию не ограничен, а
# команды держат живые элементы сцены вместе с их пиксмапами
# (см. undo/commands.py): за долгий сеанс история накапливала
# сотни мегабайт. Вытесненные команды Qt удаляет, и пиксмапы
# освобождаются вместе с ними.
DEFAULT_UNDO_LIMIT = 100
MIN_UNDO_LIMIT = 10
MAX_UNDO_LIMIT = 1000

# Папки файловых диалогов запоминаются по типам отдельно:
# проекты и готовые коллажи обычно лежат не там, где исходные фото
DIR_KINDS = ("project", "image", "export")

DEFAULT_STYLE = {
    "gutter": 0.0,
    "corner_radius": 0.0,
    "background": "#ffffff",
    "transparent": False,
}


def _settings() -> QSettings:
    return QSettings(ORGANIZATION, APPLICATION)


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value, default):
    """QSettings часто возвращает bool строкой "true"/"false"."""
    if isinstance(value, bool):
        return value

    if value is None:
        return default

    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no", ""):
        return False

    return default


def _same_path(first: str, second: str) -> bool:
    """Сравнение путей без учёта регистра (важно для Windows)."""
    try:
        return os.path.normcase(os.path.abspath(first)) == os.path.normcase(
            os.path.abspath(second)
        )
    except (TypeError, ValueError):
        return False


# ---------- Язык интерфейса ----------

def language() -> str:
    value = _settings().value("ui/language", DEFAULT_LANGUAGE)
    text = str(value or "").strip().lower()

    return text if text in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def set_language(lang: str):
    if lang in SUPPORTED_LANGUAGES:
        _settings().setValue("ui/language", lang)


# ---------- Задержка обмена ----------

def swap_delay_ms() -> int:
    value = _to_int(_settings().value("editor/swap_delay_ms", 0), 0)

    return max(0, min(value, MAX_SWAP_DELAY_MS))


def set_swap_delay_ms(value: int):
    value = max(0, min(_to_int(value, 0), MAX_SWAP_DELAY_MS))
    _settings().setValue("editor/swap_delay_ms", value)


# ---------- Предупреждение о несохранённых изменениях ----------

def confirm_unsaved() -> bool:
    """Спрашивать ли о сохранении при закрытии холста и приложения.

    По умолчанию включено: молча терять работу нельзя. Пункт
    «Настройки → Предупреждать о несохранённых изменениях» позволяет
    убрать вопрос — при быстрой сборке коллажей, которые не нужно
    хранить в .fcproj, он только мешает.
    """
    return _to_bool(_settings().value("editor/confirm_unsaved", True), True)


def set_confirm_unsaved(enabled: bool):
    _settings().setValue("editor/confirm_unsaved", bool(enabled))


# ---------- Окно и панели ----------

def window_geometry() -> QByteArray:
    value = _settings().value("window/geometry")

    return value if isinstance(value, QByteArray) else QByteArray()


def window_state() -> QByteArray:
    value = _settings().value("window/state")

    return value if isinstance(value, QByteArray) else QByteArray()


def save_window(geometry, state):
    store = _settings()
    store.setValue("window/geometry", geometry)
    store.setValue("window/state", state)


def preview_visible() -> bool:
    return _to_bool(_settings().value("window/preview_visible", True), True)


def set_preview_visible(visible: bool):
    _settings().setValue("window/preview_visible", bool(visible))


# ---------- Последние папки диалогов ----------

def last_dir(kind: str) -> str:
    """Папка последнего диалога; "" — поведение по умолчанию.

    Пустая строка возвращается и тогда, когда папка больше не
    существует (удалили, отключили флешку) — иначе диалог
    откроется в никуда.
    """
    if kind not in DIR_KINDS:
        return ""

    path = str(_settings().value("dirs/%s" % kind, "") or "")

    return path if path and os.path.isdir(path) else ""


def set_last_dir(kind: str, path: str):
    if kind not in DIR_KINDS or not path:
        return

    if os.path.isdir(path):
        _settings().setValue("dirs/%s" % kind, path)


# ---------- Последние проекты ----------

def recent_projects() -> list:
    value = _settings().value("projects/recent", [])

    # Список из одного элемента QSettings отдаёт просто строкой
    if isinstance(value, str):
        value = [value]

    paths = [str(p) for p in (value or []) if str(p).strip()]

    return paths[:MAX_RECENT_PROJECTS]


def add_recent_project(path: str):
    """Добавить проект в начало списка без дубликатов."""
    if not path:
        return

    paths = [p for p in recent_projects() if not _same_path(p, path)]
    paths.insert(0, path)

    _settings().setValue("projects/recent", paths[:MAX_RECENT_PROJECTS])


def remove_recent_project(path: str):
    """Убрать из списка (например, файл удалён или перемещён)."""
    if not path:
        return

    paths = [p for p in recent_projects() if not _same_path(p, path)]
    _settings().setValue("projects/recent", paths)


# ---------- Оформление холста по умолчанию ----------

def layout_style() -> dict:
    """Оформление для новых коллажей (последнее выбранное)."""
    store = _settings()

    return {
        "gutter": max(0.0, _to_float(
            store.value("style/gutter", DEFAULT_STYLE["gutter"]),
            DEFAULT_STYLE["gutter"],
        )),
        "corner_radius": max(0.0, _to_float(
            store.value("style/corner_radius", DEFAULT_STYLE["corner_radius"]),
            DEFAULT_STYLE["corner_radius"],
        )),
        "background": str(
            store.value("style/background", DEFAULT_STYLE["background"])
            or DEFAULT_STYLE["background"]
        ),
        "transparent": _to_bool(
            store.value("style/transparent", DEFAULT_STYLE["transparent"]),
            DEFAULT_STYLE["transparent"],
        ),
    }


def set_layout_style(style: dict):
    style = style or {}
    store = _settings()

    store.setValue(
        "style/gutter", max(0.0, _to_float(style.get("gutter"), 0.0))
    )
    store.setValue(
        "style/corner_radius",
        max(0.0, _to_float(style.get("corner_radius"), 0.0)),
    )
    store.setValue(
        "style/background",
        str(style.get("background") or DEFAULT_STYLE["background"]),
    )
    store.setValue("style/transparent", bool(style.get("transparent", False)))


# ---------- Экспорт ----------

def export_scale() -> int:
    """Масштаб экспорта в процентах (последний выбранный)."""
    value = _to_int(
        _settings().value("export/scale", DEFAULT_EXPORT_SCALE),
        DEFAULT_EXPORT_SCALE,
    )

    return max(MIN_EXPORT_SCALE, min(value, MAX_EXPORT_SCALE))


def set_export_scale(value: int):
    value = max(
        MIN_EXPORT_SCALE,
        min(_to_int(value, DEFAULT_EXPORT_SCALE), MAX_EXPORT_SCALE),
    )
    _settings().setValue("export/scale", value)


def export_quality() -> int:
    """Качество JPEG; для PNG не используется."""
    value = _to_int(
        _settings().value("export/quality", DEFAULT_EXPORT_QUALITY),
        DEFAULT_EXPORT_QUALITY,
    )

    return max(MIN_EXPORT_QUALITY, min(value, MAX_EXPORT_QUALITY))


def set_export_quality(value: int):
    value = max(
        MIN_EXPORT_QUALITY,
        min(_to_int(value, DEFAULT_EXPORT_QUALITY), MAX_EXPORT_QUALITY),
    )
    _settings().setValue("export/quality", value)


def undo_limit() -> int:
    """Максима��ьное число шагов в истории отмен.

    Ключ читается из QSettings, но в интерфейсе не показывается:
    это аварийный вентиль для машин с малым объёмом ОЗУ,
    а не очередная настройка в меню.
    """
    value = _to_int(
        _settings().value("editor/undo_limit", DEFAULT_UNDO_LIMIT),
        DEFAULT_UNDO_LIMIT,
    )

    return max(MIN_UNDO_LIMIT, min(value, MAX_UNDO_LIMIT))


# ---------- Подбор холста под фото ----------

# Второй режим «Холста под фото»: бюджет обрезки в процентах стороны
# и число раскладок для кнопки «Другая сетка». Раньше это были
# константы в core/auto_layout.py — теперь их видно в интерфейсе.
DEFAULT_AUTO_CROP_PERCENT = 20
MIN_AUTO_CROP_PERCENT = 0
MAX_AUTO_CROP_PERCENT = 50

DEFAULT_AUTO_VARIANTS = 12
MIN_AUTO_VARIANTS = 2
MAX_AUTO_VARIANTS = 48


def auto_crop_percent() -> int:
    """Сколько процентов стороны фото разрешено срезать (режим 2)."""
    value = _to_int(
        _settings().value(
            "auto_canvas/crop_percent", DEFAULT_AUTO_CROP_PERCENT
        ),
        DEFAULT_AUTO_CROP_PERCENT,
    )

    return max(MIN_AUTO_CROP_PERCENT, min(value, MAX_AUTO_CROP_PERCENT))


def set_auto_crop_percent(value: int):
    value = max(
        MIN_AUTO_CROP_PERCENT,
        min(_to_int(value, DEFAULT_AUTO_CROP_PERCENT), MAX_AUTO_CROP_PERCENT),
    )
    _settings().setValue("auto_canvas/crop_percent", value)


def auto_variant_limit() -> int:
    """Сколько вариантов сетки держать для перебора (режим 2)."""
    value = _to_int(
        _settings().value("auto_canvas/variants", DEFAULT_AUTO_VARIANTS),
        DEFAULT_AUTO_VARIANTS,
    )

    return max(MIN_AUTO_VARIANTS, min(value, MAX_AUTO_VARIANTS))


def set_auto_variant_limit(value: int):
    value = max(
        MIN_AUTO_VARIANTS,
        min(_to_int(value, DEFAULT_AUTO_VARIANTS), MAX_AUTO_VARIANTS),
    )
    _settings().setValue("auto_canvas/variants", value)
