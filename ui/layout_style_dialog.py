"""Диалог оформления коллажа: отступы, скругления и фон холста."""

from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)
from PySide6.QtGui import QColor

import i18n

# Ограничения взяты с запасом: на холсте 4K отступ 500 px — это
# уже очень широкие поля, большее значение съело бы слоты целиком
MAX_GUTTER = 500
MAX_RADIUS = 300


class LayoutStyleDialog(QDialog):
    """Отступ между слотами, скругление углов и цвет фона."""

    def __init__(self, style: dict, parent=None):
        super().__init__(parent)

        style = style or {}

        self.setWindowTitle(i18n.t('layout_style'))
        self.setModal(True)

        self.color = QColor(str(style.get("background") or "#ffffff"))
        if not self.color.isValid():
            self.color = QColor("#ffffff")

        self.gutter_spin = QSpinBox()
        self.gutter_spin.setRange(0, MAX_GUTTER)
        self.gutter_spin.setValue(self._as_int(style.get("gutter"), 0))

        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(0, MAX_RADIUS)
        self.radius_spin.setValue(self._as_int(style.get("corner_radius"), 0))

        # Квадратик с текущим цветом + кнопка выбора
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(28, 20)

        self.color_button = QPushButton(i18n.t('choose_color'))
        self.color_button.clicked.connect(self._choose_color)

        color_row = QHBoxLayout()
        color_row.addWidget(self.color_preview)
        color_row.addWidget(self.color_button)
        color_row.addStretch(1)

        self.transparent_check = QCheckBox(i18n.t('transparent_background'))
        self.transparent_check.setChecked(bool(style.get("transparent", False)))
        self.transparent_check.toggled.connect(self._update_color_enabled)

        form = QFormLayout()
        form.addRow(i18n.t('gutter_px'), self.gutter_spin)
        form.addRow(i18n.t('corner_radius_px'), self.radius_spin)
        form.addRow(i18n.t('background_color'), color_row)
        form.addRow("", self.transparent_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self._update_preview()
        self._update_color_enabled(self.transparent_check.isChecked())

    @staticmethod
    def _as_int(value, default: int) -> int:
        """В проекте и QSettings значения хранятся как float."""
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return default

    def _choose_color(self):
        color = QColorDialog.getColor(
            self.color, self, i18n.t('background_color')
        )

        if color.isValid():
            self.color = color
            self._update_preview()

    def _update_preview(self):
        self.color_preview.setStyleSheet(
            "background-color: %s; border: 1px solid #808080;"
            % self.color.name()
        )

    def _update_color_enabled(self, transparent: bool):
        # При прозрачном фоне цвет ни на что не влияет
        self.color_button.setEnabled(not transparent)
        self.color_preview.setEnabled(not transparent)

    def get_style(self) -> dict:
        return {
            "gutter": float(self.gutter_spin.value()),
            "corner_radius": float(self.radius_spin.value()),
            "background": self.color.name(),
            "transparent": bool(self.transparent_check.isChecked()),
        }
