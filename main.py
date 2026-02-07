import sys
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from ui.start_dialog import StartCollageDialog

def main():
    app = QApplication(sys.argv)

    # 1) Стартовый диалог — ОБЯЗАТЕЛЬНЫЙ
    start_dialog = StartCollageDialog()
    if not start_dialog.exec():
        sys.exit(0)  # ← ВАЖНО

    # 2) Создаём окно
    window = MainWindow()

    # 3) Передаём выбор в окно
    data = start_dialog.result_data()
    window.create_new_collage_from_data(data)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

#pyinstaller --noconsole --onefile main.py