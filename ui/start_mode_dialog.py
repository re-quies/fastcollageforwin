from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QPushButton, QLabel
)
from PySide6.QtCore import Qt


class StartModeDialog(QDialog):
    FREE = "free"
    RANDOM = "random"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Новый коллаж")
        self.setModal(True)
        self.setFixedSize(300, 180)

        self.result_mode = None

        layout = QVBoxLayout(self)

        title = QLabel("Выберите режим коллажа")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        free_btn = QPushButton("Свободный коллаж")
        random_btn = QPushButton("Случайный коллаж")

        free_btn.clicked.connect(self._choose_free)
        random_btn.clicked.connect(self._choose_random)

        layout.addWidget(free_btn)
        layout.addWidget(random_btn)

    def _choose_free(self):
        self.result_mode = self.FREE
        self.accept()

    def _choose_random(self):
        self.result_mode = self.RANDOM
        self.accept()
