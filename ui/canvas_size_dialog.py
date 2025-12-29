from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QSpinBox,
    QDialogButtonBox,
    QComboBox,
)


class CanvasSizeDialog(QDialog):
    def __init__(self, width, height, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Размер холста")
        self.presets = {
            "1080 x 720px": (1080, 720),
            "1920 × 1080px": (1920, 1080),
            "2560 × 1440px": (2560, 1440),
            "3200 × 1800px": (3200, 1800),
            "3840 × 2160px": (3840, 2160),
            "5120 × 2880px": (5120, 2880),
        }
        self.preset_combo = QComboBox()
        for name in self.presets:
            self.preset_combo.addItem(name)
        self.preset_combo.setCurrentText("3840 × 2160px")

        self.preset_combo.currentTextChanged.connect(self._apply_preset)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(100, 10000)
        self.width_spin.setValue(width)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(100, 10000)
        self.height_spin.setValue(height)

        layout = QFormLayout(self)
        layout.addRow("Пресет:", self.preset_combo)
        layout.addRow("Ширина (px):", self.width_spin)
        layout.addRow("Высота (px):", self.height_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def get_size(self):
        return self.width_spin.value(), self.height_spin.value()

    def _apply_preset(self, text):
        if text in self.presets:
            w, h = self.presets[text]
            self.width_spin.setValue(w)
            self.height_spin.setValue(h)