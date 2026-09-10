from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QSpinBox,
    QDialogButtonBox,
    QComboBox,
    QToolButton,
)
from core.canvas_presets import (
    CANVAS_PRESETS,
    DEFAULT_LARGE_PRESET_LABEL,
    MAX_SIDE,
    MIN_SIDE,
    preset_label,
)
import i18n


class CanvasSizeDialog(QDialog):
    def __init__(self, width, height, parent=None):
        super().__init__(parent)

        self.setWindowTitle(i18n.t('canvas_size'))

        # Общий список с диалогом создания коллажа
        self.presets = CANVAS_PRESETS

        self.preset_combo = QComboBox()
        for name in self.presets:
            self.preset_combo.addItem(name)

        # Если текущий размер совпадает с пресетом — показываем его.
        # Сигнал подключается ниже, поэтому спинбоксы не перезатираются.
        current_label = preset_label(width, height)
        if current_label in self.presets:
            self.preset_combo.setCurrentText(current_label)
        else:
            self.preset_combo.setCurrentText(DEFAULT_LARGE_PRESET_LABEL)

        self.preset_combo.currentTextChanged.connect(self._apply_preset)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(MIN_SIDE, MAX_SIDE)
        self.width_spin.setValue(width)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(MIN_SIDE, MAX_SIDE)
        self.height_spin.setValue(height)

        # Кнопка смены ориентации рядом со списком пресетов
        self.portrait = height > width
        self.orientation_button = QToolButton()
        self.orientation_button.setText("⇄")
        self.orientation_button.setToolTip(i18n.t('swap_orientation'))
        self.orientation_button.clicked.connect(self._toggle_orientation)

        preset_row = QHBoxLayout()
        preset_row.addWidget(self.preset_combo)
        preset_row.addWidget(self.orientation_button)

        layout = QFormLayout(self)
        layout.addRow(i18n.t('canvas_size_label'), preset_row)
        layout.addRow(i18n.t('width_px'), self.width_spin)
        layout.addRow(i18n.t('height_px'), self.height_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def get_size(self):
        return self.width_spin.value(), self.height_spin.value()

    def _toggle_orientation(self):
        """Поменять местами ширину и высоту (альбомная / книжная)."""
        self.portrait = not self.portrait

        width = self.width_spin.value()
        height = self.height_spin.value()
        self.width_spin.setValue(height)
        self.height_spin.setValue(width)

    def _apply_preset(self, text):
        if text in self.presets:
            w, h = self.presets[text]
            if self.portrait:
                w, h = h, w
            self.width_spin.setValue(w)
            self.height_spin.setValue(h)