import logging
import logging.handlers
import os
import sys
import tempfile
import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from ui.main_window import MainWindow
from ui.start_dialog import StartCollageDialog
from core.resources import project_root, resource_path
from core.version import APP_VERSION
from core import settings
import i18n


def log_path() -> str:
    """Путь к файлу лога.

    Сборка идёт с --noconsole, поэтому stderr никто не видит:
    без файла ошибка просто исчезала, а действие выглядело как
    «ничего не произошло».
    """
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    folder = os.path.join(base, "FastCollageForWin")

    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        folder = tempfile.gettempdir()

    return os.path.join(folder, "fastcollage.log")


# Лог пишется в каждый запуск и раньше рос без ограничений: обычный
# FileHandler только дописывает файл, поэтому за месяцы работы в
# профиле пользователя оставались десятки мегабайт (а с путями ко
# всем открытым фотографиям — ещё и лишние персональные данные).
# Пять файлов по 2 МБ — это история последних сеансов и предсказуемый
# объём на диске.
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def setup_logging():
    """Пункт 3: базовая настройка логирования вместо глушения ошибок."""
    handlers = [logging.StreamHandler()]

    try:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_path(),
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
        )
    except OSError:
        # Папка может быть только для чтения — лог не причина не запуститься
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )

    logging.getLogger("main").info("Log file: %s", log_path())


def log_environment():
    """Записать в лог, что именно запущено.

    В собранном exe не видно ни версии исходников, ни того,
    доехали ли ресурсы до бандла.
    """
    logger = logging.getLogger("main")

    logger.info("FastCollageForWin %s", APP_VERSION)
    logger.info(
        "Frozen: %s, resources: %s",
        bool(getattr(sys, "frozen", False)),
        project_root(),
    )

    for name in ("new_grid.svg", "auto_canvas.svg"):
        path = resource_path("assets", "icons", name)
        logger.info("Icon %s: %s", name, os.path.isfile(path))


def install_excepthook():
    """Показывать необработанные ошибки, а не терять их молча.

    Qt печатает исключение из обработчика сигнала в stderr и
    продолжает работу. В оконной сборке stderr нет, и любой сбой
    выглядит как неработающая кнопка.
    """
    logger = logging.getLogger("main")

    def hook(exc_type, exc_value, exc_tb):
        logger.error(
            "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
        )

        if QApplication.instance() is None:
            return

        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(i18n.t('error'))
        box.setText(i18n.t('unexpected_error'))
        box.setInformativeText(log_path())
        box.setDetailedText(
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        )
        box.exec()

    sys.excepthook = hook


def check_required_api():
    """Убедиться, что все модули из одного комплекта.

    Ровно этот случай уже был: exe собрался, окно открылось,
    а кнопка падала с AttributeError — часть файлов осталась
    от прежней версии. Проверка занимает миллисекунды и сразу
    называет файл, который не обновился.

    Возвращает список отсутствующих возможностей (пустой — всё в порядке).
    """
    from canvas.scene import CanvasScene
    from core import auto_layout, image_cache, settings
    from ui.export_dialog import ExportOptionsDialog
    from ui.main_window import MainWindow
    from ui.preview_panel import PreviewPanel
    from undo import commands as undo_commands

    required = (
        (PreviewPanel, "image_entries", "ui/preview_panel.py"),
        (CanvasScene, "build_template_from_rects", "canvas/scene.py"),
        (image_cache, "oriented_image_size", "core/image_cache.py"),
        (auto_layout, "plan_canvas", "core/auto_layout.py"),
        (MainWindow, "notify", "ui/main_window.py"),
        (MainWindow, "update_mode_indicator", "ui/main_window.py"),
        (MainWindow, "show_shortcuts", "ui/main_window.py"),
        (MainWindow, "set_warn_unsaved", "ui/main_window.py"),
        (undo_commands, "ContentViewCommand", "undo/commands.py"),
        (settings, "confirm_unsaved", "core/settings.py"),
        (ExportOptionsDialog, "target_size", "ui/export_dialog.py"),
        (settings, "export_scale", "core/settings.py"),
    )

    missing = [
        "{}: {}()".format(filename, name)
        for owner, name, filename in required
        if not hasattr(owner, name)
    ]

    logger = logging.getLogger("main")

    if missing:
        logger.error("Outdated modules: %s", "; ".join(missing))
    else:
        logger.info("API self-check passed")

    return missing


def warn_about_stale_build(missing):
    """Сказать прямо, какие файлы не обновились."""
    box = QMessageBox()
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(i18n.t('stale_build_title'))
    box.setText(i18n.t('stale_build_text'))
    box.setInformativeText(log_path())
    box.setDetailedText("\n".join(missing))
    box.exec()


def main():
    setup_logging()
    log_environment()

    app = QApplication(sys.argv)

    install_excepthook()

    # Язык раньше применялся только в главном окне, поэтому
    # стартовый диалог всегда открывался на русском
    i18n.set_lang(settings.language())

    # Арабский интерфейс раскладывается справа налево. Направление
    # задаём до стартового диалога, иначе он откроется слева
    # направо, а главное окно — уже справа налево
    app.setLayoutDirection(
        Qt.RightToLeft if i18n.is_rtl() else Qt.LeftToRight
    )

    stale = check_required_api()
    if stale:
        warn_about_stale_build(stale)

    # 1) Стартовый диалог — ОБЯЗАТЕЛЬНЫЙ
    start_dialog = StartCollageDialog()
    if not start_dialog.exec():
        sys.exit(0)

    # 2) Создаём окно
    window = MainWindow()

    # 3) Передаём выбор в окно
    data = start_dialog.result_data()
    window.create_new_collage_from_data(data)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

# pyinstaller --noconsole --onefile main.py
