"""Настройки подбора холста под фотографии.

У второго режима «Холста под фото» два параметра: сколько от
стороны фотографии разрешено срезать и сколько раскладок
держать для кнопки «Другая сетка». Раньше это были константы
в core/auto_layout.py, теперь — пункт в меню «Настройки».

Первый режим («точные пропорции») эти настройки не затрагивают.
Значения живут в QSettings (core/settings.py) и переживают перезапуск.
"""

import logging

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from core import settings
import i18n

logger = logging.getLogger(__name__)


class AutoCanvasSettingsDialog(QDialog):
    """Два параметра второго режима подбора холста."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t('auto_canvas_settings_title'))

        layout = QVBoxLayout(self)

        form = QFormLayout()

        # Бюджет обрезки: 0% — тоже допустимое значение, тогда второй
        # режим даёт варианты без обрезки вообще
        self.crop_spin = QSpinBox(self)
        self.crop_spin.setRange(
            settings.MIN_AUTO_CROP_PERCENT, settings.MAX_AUTO_CROP_PERCENT
        )
        self.crop_spin.setSuffix(" %")
        self.crop_spin.setValue(settings.auto_crop_percent())
        form.addRow(i18n.t('auto_canvas_settings_crop'), self.crop_spin)

        self.variants_spin = QSpinBox(self)
        self.variants_spin.setRange(
            settings.MIN_AUTO_VARIANTS, settings.MAX_AUTO_VARIANTS
        )
        self.variants_spin.setValue(settings.auto_variant_limit())
        form.addRow(
            i18n.t('auto_canvas_settings_variants'), self.variants_spin
        )

        layout.addLayout(form)

        for key in (
            'auto_canvas_settings_crop_hint',
            'auto_canvas_settings_variants_hint',
            'auto_canvas_settings_note',
        ):
            hint = QLabel(i18n.t(key), self)
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #666666;")
            layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok
            | QDialogButtonBox.Cancel
            | QDialogButtonBox.RestoreDefaults,
            self,
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        restore = buttons.button(QDialogButtonBox.RestoreDefaults)
        if restore is not None:
            restore.clicked.connect(self._restore_defaults)

        layout.addWidget(buttons)

    def _restore_defaults(self):
        """Вернуть заводские 20% и 12 вариантов."""
        self.crop_spin.setValue(settings.DEFAULT_AUTO_CROP_PERCENT)
        self.variants_spin.setValue(settings.DEFAULT_AUTO_VARIANTS)

    def _save(self):
        crop = self.crop_spin.value()
        variants = self.variants_spin.value()

        settings.set_auto_crop_percent(crop)
        settings.set_auto_variant_limit(variants)

        logger.info(
            "Auto canvas settings: crop %d%%, %d variant(s)", crop, variants
        )

        self.accept()
