import logging
import sys

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.start_dialog import StartCollageDialog


def setup_logging():
    """Пункт 3: базовая настройка логирования вместо глушения ошибок."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def main():
    setup_logging()

    app = QApplication(sys.argv)

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
