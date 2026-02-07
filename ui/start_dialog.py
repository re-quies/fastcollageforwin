from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QRadioButton,
    QSpinBox, QDialogButtonBox, QLabel,
    QComboBox, QHBoxLayout
)
from core.collage_mode import CollageMode
import i18n


CANVAS_PRESETS = {
    "1080×720": (1080, 720),
    "1920×1080": (1920, 1080),
    "2560×1440": (2560, 1440),
    "3200×1800": (3200, 1800),
    "3840×2160": (3840, 2160),
}


class StartCollageDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t('new_collage'))

        layout = QVBoxLayout(self)

        # --- Mode ---
        self.free_radio = QRadioButton(i18n.t('free_collage'))
        self.template_radio = QRadioButton(i18n.t('random_collage'))
        self.free_radio.setChecked(True)

        layout.addWidget(self.free_radio)
        layout.addWidget(self.template_radio)

        # --- Image count ---
        layout.addWidget(QLabel(i18n.t('image_count')))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 100)
        self.count_spin.setValue(6)
        self.count_spin.setEnabled(False)
        layout.addWidget(self.count_spin)

        # --- Canvas size ---
        layout.addWidget(QLabel(i18n.t('canvas_size_label')))

        size_layout = QHBoxLayout()

        self.preset_combo = QComboBox()
        self.preset_combo.addItems(CANVAS_PRESETS.keys())
        # Устанавливаем пресет по умолчанию 1920×1080
        self.preset_combo.setCurrentText("1920×1080")
        size_layout.addWidget(self.preset_combo)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(500, 10000)
        self.width_spin.setValue(1920)
        size_layout.addWidget(self.width_spin)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(500, 10000)
        self.height_spin.setValue(1080)
        size_layout.addWidget(self.height_spin)

        layout.addLayout(size_layout)

        # --- Enable / disable ---
        def toggle_template(enabled):
            # В template режиме активируем только выбор количества.
            # Размер холста доступен в обоих режимах.
            self.count_spin.setEnabled(enabled)

        self.template_radio.toggled.connect(toggle_template)
        toggle_template(False)

        # --- Preset logic ---
        self.preset_combo.currentTextChanged.connect(self._apply_preset)

        # --- Buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_preset(self, text):
        w, h = CANVAS_PRESETS[text]
        self.width_spin.setValue(w)
        self.height_spin.setValue(h)

    def result_data(self):
        # Всегда возвращаем выбранный размер холста.
        canvas_size = (self.width_spin.value(), self.height_spin.value())

        if self.free_radio.isChecked():
            return {
                "mode": CollageMode.FREE,
                "canvas_size": canvas_size
            }

        return {
            "mode": CollageMode.TEMPLATE,
            "count": self.count_spin.value(),
            "canvas_size": canvas_size
        }
