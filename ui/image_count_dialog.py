from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel,
    QSpinBox, QDialogButtonBox
)
from PySide6.QtCore import Qt


class ImageCountDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Количество изображений")
        self.setModal(True)
        self.setFixedSize(260, 150)

        layout = QVBoxLayout(self)

        label = QLabel("Выберите количество изображений")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

        self.spin = QSpinBox()
        self.spin.setRange(1, 50)
        self.spin.setValue(6)
        self.spin.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_count(self) -> int:
        return self.spin.value()
