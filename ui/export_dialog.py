"""Диалог параметров экспорта: масштаб, качество JPEG, оценка памяти.

Экспорт рендерит холст в один QImage целиком, поэтому счёт идёт на
сотни мегабайт: холст 10000×10000 в 32 бита — это 400 МБ одним
непрерывным блоком, и столько же потребуется кодировщику при
сохранении. Раньше это выяснялось только постфактум — QImage
получался пустым, а save() молча возвращал False.

Здесь размер и вес показываются до рендера, а заведомо
невыполнимый масштаб блокирует кнопку «ОК».

Оценка считает только целевой QImage. Исходные фотографии в оценку
не входят осознанно: рендер идёт горизонтальными полосами по
высоте EXPORT_TILE_HEIGHT (см. MainWindow._render_scene_to_image),
и полноразмерные оригиналы держатся в памяти только в пределах
одной полосы.
"""

import logging

from PySide6.QtWidgets import (
    QComboBox,
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

# Масштабы экспорта в процентах от размера холста.
#
# Источник один — core.settings.EXPORT_SCALES. Раньше здесь был свой
# список (25…200), а настройки разрешали хранить 10…400:
# запомненные 300 % при следующем экспорте молча становились 100 %.
SCALES = tuple(settings.EXPORT_SCALES)

# Высота одной полосы рендера, px.
#
# Сцена рисуется в целевое изображение не целиком, а полосами: на
# время экспорта каждое фото берётся с диска в полном разрешении,
# и раньше в памяти одновременно оказывались ВСЕ оригиналы.
# 2048 строк — компромисс: полос немного (меньше повторных чтений
# файлов), но в каждой лежит лишь часть фотографий коллажа.
EXPORT_TILE_HEIGHT = 2048

# Потолок стороны: Qt хранит QImage одним непрерывным блоком
# и сам отказывается от слишком больших сторон
MAX_SIDE = 32000

# За этим порогом выделение памяти практически обречено
MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 ГБ

# Предупреждаем заранее, если одна картинка съест больше этого
WARN_BYTES = 512 * 1024 * 1024  # 512 МБ


def bytes_per_pixel(is_jpeg: bool) -> int:
    """JPEG рендерится без альфы — три байта вместо четырёх."""
    return 3 if is_jpeg else 4


def scaled_size(width, height, percent):
    """Размер после масштабирования (минимум 1 пиксель по стороне)."""
    factor = max(1, int(percent)) / 100.0

    return (
        max(1, int(round(float(width) * factor))),
        max(1, int(round(float(height) * factor))),
    )


def estimate_bytes(width, height, is_jpeg: bool) -> int:
    return int(width) * int(height) * bytes_per_pixel(is_jpeg)


def is_feasible(width, height, is_jpeg: bool) -> bool:
    """Поместится ли такое изображение в память хотя бы теоретически."""
    if width > MAX_SIDE or height > MAX_SIDE:
        return False

    return estimate_bytes(width, height, is_jpeg) <= MAX_BYTES


class ExportOptionsDialog(QDialog):
    """Масштаб и качество будущего файла."""

    def __init__(
        self,
        width,
        height,
        is_jpeg=False,
        scale_percent=settings.DEFAULT_EXPORT_SCALE,
        quality=settings.DEFAULT_EXPORT_QUALITY,
        parent=None,
    ):
        super().__init__(parent)

        self._base_width = max(1, int(width))
        self._base_height = max(1, int(height))
        self._is_jpeg = bool(is_jpeg)

        self.setWindowTitle(i18n.t('export_options'))

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.scale_combo = QComboBox()
        for percent in SCALES:
            self.scale_combo.addItem("%d %%" % percent, percent)

        # Запомненный масштаб мог исчезнуть из списка после
        # обновления — тогда возвращаемся к значению по умолчанию
        index = self.scale_combo.findData(int(scale_percent))
        if index < 0:
            index = self.scale_combo.findData(settings.DEFAULT_EXPORT_SCALE)

        self.scale_combo.setCurrentIndex(max(0, index))

        form.addRow(i18n.t('export_scale'), self.scale_combo)

        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(
            settings.MIN_EXPORT_QUALITY, settings.MAX_EXPORT_QUALITY
        )
        self.quality_spin.setValue(
            max(
                settings.MIN_EXPORT_QUALITY,
                min(int(quality), settings.MAX_EXPORT_QUALITY),
            )
        )
        self.quality_spin.setSuffix(" %")

        # Качество есть только у JPEG: у PNG сжатие без потерь
        if self._is_jpeg:
            form.addRow(i18n.t('export_quality'), self.quality_spin)

        layout.addLayout(form)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.scale_combo.currentIndexChanged.connect(self._refresh)
        self._refresh()

    # ---------- Внутреннее ----------

    def _refresh(self):
        width, height = self.target_size()
        size_bytes = estimate_bytes(width, height, self._is_jpeg)
        megabytes = size_bytes / (1024.0 * 1024.0)

        text = i18n.t('export_result').format(
            width=width,
            height=height,
            size=(
                ("%.0f" % megabytes) if megabytes >= 10
                else ("%.1f" % megabytes)
            ),
        )

        feasible = is_feasible(width, height, self._is_jpeg)

        if not feasible:
            text = "%s\n%s" % (text, i18n.t('export_too_big'))
        elif size_bytes >= WARN_BYTES:
            text = "%s\n%s" % (text, i18n.t('export_heavy'))

        self.info_label.setText(text)

        # Невыполнимый масштаб лучше запретить сразу, чем дать
        # нажать «ОК» и получить ошибку после долгого рендера
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setEnabled(feasible)

    # ---------- Результат ----------

    def scale_percent(self) -> int:
        data = self.scale_combo.currentData()

        return int(data) if data else 100

    def quality(self) -> int:
        return int(self.quality_spin.value())

    def target_size(self):
        return scaled_size(
            self._base_width, self._base_height, self.scale_percent()
        )
