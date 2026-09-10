from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QRadioButton,
    QSpinBox, QDialogButtonBox, QLabel,
    QComboBox, QHBoxLayout, QToolButton
)
from core.collage_mode import CollageMode
from core.canvas_presets import (
    CANVAS_PRESETS,
    DEFAULT_PRESET,
    DEFAULT_PRESET_LABEL,
    MAX_SIDE,
    MIN_SIDE,
)
import i18n


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
        # Устанавливаем пресет по умолчанию 1920 × 1080
        self.preset_combo.setCurrentText(DEFAULT_PRESET_LABEL)
        size_layout.addWidget(self.preset_combo)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(MIN_SIDE, MAX_SIDE)
        self.width_spin.setValue(DEFAULT_PRESET[0])
        size_layout.addWidget(self.width_spin)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(MIN_SIDE, MAX_SIDE)
        self.height_spin.setValue(DEFAULT_PRESET[1])
        size_layout.addWidget(self.height_spin)

        # Кнопка смены ориентации: 1920×1080 -> 1080×1920
        self.portrait = False
        self.orientation_button = QToolButton()
        self.orientation_button.setText("⇄")
        self.orientation_button.setToolTip(i18n.t('swap_orientation'))
        self.orientation_button.clicked.connect(self._toggle_orientation)
        size_layout.addWidget(self.orientation_button)

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

    def _toggle_orientation(self):
        """Поменять местами ширину и высоту (альбомная / книжная).

        Флаг self.portrait запоминает выбор, чтобы последующий выбор
        пресета тоже применялся в нужной ориентации.
        """
        self.portrait = not self.portrait

        width = self.width_spin.value()
        height = self.height_spin.value()
        self.width_spin.setValue(height)
        self.height_spin.setValue(width)

    def _apply_preset(self, text):
        preset = CANVAS_PRESETS.get(text)
        if preset is None:
            return

        w, h = preset
        if self.portrait:
            w, h = h, w

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
