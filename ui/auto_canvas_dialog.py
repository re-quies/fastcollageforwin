"""Диалог подбора холста под фотографии из панели превью.

Показывает рассчитанный размер холста, схему будущей сетки и даёт
органы управления:
- режим подбора — точные пропорции (без обрезки, как было) либо
  «больше вариантов», где каждое фото можно уменьшить под ячейку;
- «другая сетка» — следующий по качеству вариант раскладки (только
  во втором режиме, где вариантов много);
- кнопка ⇄ — та же раскладка в другой ориентации (400×800 ↔ 800×400);
- выбор округления — «авто», до 10 px, до 100 px или точный размер.

Сам расчёт живёт в core.auto_layout — здесь только интерфейс.
"""

import logging

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import i18n
from core import auto_layout, settings


class LayoutPreviewWidget(QWidget):
    """Схема рассчитанной сетки (без самих фотографий)."""

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self._plan = plan
        self.setMinimumSize(280, 210)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_plan(self, plan):
        self._plan = plan
        self.update()

    def paintEvent(self, event):
        plan = self._plan
        if plan is None or plan.width <= 0 or plan.height <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        area = QRectF(self.rect()).adjusted(4.0, 4.0, -4.0, -4.0)
        scale = min(
            area.width() / float(plan.width),
            area.height() / float(plan.height),
        )

        width = plan.width * scale
        height = plan.height * scale
        left = area.left() + (area.width() - width) / 2.0
        top = area.top() + (area.height() - height) / 2.0

        # Подложка холста
        canvas_rect = QRectF(left, top, width, height)
        painter.fillRect(canvas_rect, QColor(250, 250, 250))
        painter.setPen(QPen(QColor(150, 150, 150), 1))
        painter.drawRect(canvas_rect)

        # Ячейки
        painter.setBrush(QBrush(QColor(208, 226, 248)))
        painter.setPen(QPen(QColor(70, 115, 175), 1))

        for _index, x, y, cell_width, cell_height in plan.cells:
            rect = QRectF(
                left + x * scale,
                top + y * scale,
                cell_width * scale,
                cell_height * scale,
            )
            painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        painter.end()


logger = logging.getLogger(__name__)

# Режимы подбора: точные пропорции и «больше вариантов» с обрезкой
MODE_EXACT = "exact"
MODE_CROP = "crop"


class AutoCanvasDialog(QDialog):
    """Подтверждение рассчитанного холста перед его созданием."""

    def __init__(self, plan, skipped=0, parent=None, sizes=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t('auto_canvas_title'))

        # Режим 1 показывается ровно тем планом, который пришёл извне:
        # его расчёт и поведение не меняются.
        self._exact_plan = plan
        self._plan = plan

        # Размеры фотографий нужны только режиму 2: варианты сетки он
        # считает сам. Без них переключатель режимов не появляется.
        self._sizes = list(sizes or [])
        self._variants = None
        self._variant_index = 0

        # Бюджет обрезки и число вариантов задаются в меню
        # «Настройки → Подбор холста под фото» (core/settings.py)
        self._crop_percent = settings.auto_crop_percent()
        self._variant_limit = settings.auto_variant_limit()

        percent = self._crop_percent

        layout = QVBoxLayout(self)

        # --- Режим подбора ---
        self.mode_widget = QWidget(self)

        mode_row = QHBoxLayout(self.mode_widget)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.addWidget(
            QLabel(i18n.t('auto_canvas_mode'), self.mode_widget)
        )

        self.mode_combo = QComboBox(self.mode_widget)
        self.mode_combo.addItem(i18n.t('auto_canvas_mode_exact'), MODE_EXACT)
        self.mode_combo.addItem(
            i18n.t('auto_canvas_mode_crop').format(percent=percent), MODE_CROP
        )
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_combo, 1)

        self.mode_widget.setVisible(bool(self._sizes))
        layout.addWidget(self.mode_widget)

        # --- Схема раскладки ---
        self.preview = LayoutPreviewWidget(plan, self)
        layout.addWidget(self.preview)

        # --- Размер + смена ориентации ---
        size_row = QHBoxLayout()

        self.size_label = QLabel(self)
        font = self.size_label.font()
        font.setBold(True)
        self.size_label.setFont(font)
        size_row.addWidget(self.size_label)
        size_row.addStretch(1)

        self.orientation_button = QToolButton(self)
        self.orientation_button.setText("⇄")
        self.orientation_button.setToolTip(i18n.t('swap_orientation'))
        self.orientation_button.clicked.connect(self._toggle_orientation)
        self.orientation_button.setEnabled(plan.alternative is not None)
        size_row.addWidget(self.orientation_button)

        layout.addLayout(size_row)

        # --- Округление ---
        rounding_row = QHBoxLayout()
        rounding_row.addWidget(QLabel(i18n.t('auto_canvas_rounding'), self))

        self.rounding_combo = QComboBox(self)
        self.rounding_combo.addItem(
            i18n.t('auto_canvas_round_auto'), auto_layout.ROUNDING_AUTO
        )
        self.rounding_combo.addItem(i18n.t('auto_canvas_round_10'), 10)
        self.rounding_combo.addItem(i18n.t('auto_canvas_round_100'), 100)
        self.rounding_combo.addItem(
            i18n.t('auto_canvas_round_exact'), auto_layout.ROUNDING_EXACT
        )
        self.rounding_combo.currentIndexChanged.connect(self._apply_rounding)
        rounding_row.addWidget(self.rounding_combo, 1)

        layout.addLayout(rounding_row)

        # --- Пояснения ---
        self.cells_label = QLabel(self)
        layout.addWidget(self.cells_label)

        self.crop_label = QLabel(self)
        layout.addWidget(self.crop_label)

        if skipped:
            skipped_label = QLabel(
                i18n.t('auto_canvas_skipped').format(count=skipped), self
            )
            skipped_label.setStyleSheet("color: #b04a00;")
            layout.addWidget(skipped_label)

        # --- Перебор вариантов (только режим 2) ---
        variant_row = QHBoxLayout()

        self.variant_label = QLabel(self)
        variant_row.addWidget(self.variant_label)
        variant_row.addStretch(1)

        self.refresh_button = QToolButton(self)
        self.refresh_button.setText(i18n.t('auto_canvas_refresh'))
        self.refresh_button.clicked.connect(self._next_variant)
        variant_row.addWidget(self.refresh_button)

        layout.addLayout(variant_row)

        self.hint_label = QLabel(self)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #666666;")
        layout.addWidget(self.hint_label)

        # --- Кнопки ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh()

    # ---------- Логика ----------

    def _mode(self):
        """Текущий режим подбора."""
        data = self.mode_combo.currentData()
        return data if data in (MODE_EXACT, MODE_CROP) else MODE_EXACT

    def _ensure_variants(self):
        """Посчитать варианты режима 2 — один раз за диалог."""
        if self._variants is not None:
            return self._variants

        try:
            self._variants = auto_layout.plan_canvas_variants(
                self._sizes,
                rounding=self._plan.rounding,
                tolerance=self._crop_percent / 100.0,
                limit=self._variant_limit,
            )
        except Exception:
            # Сбой поиска не должен ломать диалог: останемся в режиме 1
            logger.exception("Crop-mode layout search failed")
            self._variants = []

        return self._variants

    def _on_mode_changed(self):
        """Переключение между точным режимом и режимом с обрезкой."""
        if self._mode() == MODE_EXACT:
            self._plan = self._exact_plan
            self._apply_rounding()
            return

        variants = self._ensure_variants()
        if not variants:
            # Посчитать не получилось — возвращаемся в первый режим
            self.mode_combo.setCurrentIndex(0)
            return

        self._variant_index = 0
        self._plan = variants[0]
        self._apply_rounding()

    def _next_variant(self):
        """Следующий по качеству вариант сетки.

        Список отсортирован по оценке, поэтому первое нажатие даёт
        вторую по качеству расстановку, второе — третью и так далее;
        после последнего варианта список начинается сначала.
        """
        if self._mode() != MODE_CROP:
            return

        variants = self._ensure_variants()
        if len(variants) < 2:
            return

        self._variant_index = (self._variant_index + 1) % len(variants)
        self._plan = variants[self._variant_index]
        self._apply_rounding()

    def _toggle_orientation(self):
        """Показать ту же раскладку в другой ориентации.

        Это не просто обмен ширины и высоты: берётся заранее
        рассчитанный лучший вариант с другой ориентацией, иначе
        пропорции ячеек перестали бы совпадать с фотографиями.
        """
        alternative = self._plan.alternative
        if alternative is None:
            return

        alternative.set_rounding(self._plan.rounding)
        self._plan = alternative

        # В режиме с обрезкой альтернатива — тоже вариант из списка,
        # так что счётчик должен за ней переехать
        for position, plan in enumerate(self._variants or []):
            if plan is alternative:
                self._variant_index = position
                break

        self._refresh()

    def _apply_rounding(self):
        mode = self.rounding_combo.currentData()
        if mode is None:
            return

        self._plan.set_rounding(mode)
        self._refresh()

    def _refresh(self):
        plan = self._plan
        crop_mode = self._mode() == MODE_CROP
        variants = self._variants or []

        self.preview.set_plan(plan)
        self.size_label.setText("%d × %d px" % (plan.width, plan.height))
        self.cells_label.setText(
            i18n.t('auto_canvas_cells').format(count=plan.cell_count)
        )
        self.orientation_button.setEnabled(plan.alternative is not None)

        if plan.max_crop < 0.001:
            self.crop_label.setText(i18n.t('auto_canvas_no_crop'))
        else:
            self.crop_label.setText(
                i18n.t('auto_canvas_crop').format(
                    percent=("%.1f" % (plan.max_crop * 100))
                )
            )

        # Счётчик и кнопка перебора нужны только во втором режиме
        self.variant_label.setVisible(crop_mode)
        self.refresh_button.setVisible(crop_mode)
        self.refresh_button.setEnabled(crop_mode and len(variants) > 1)

        if crop_mode and len(variants) > 1:
            self.variant_label.setText(
                i18n.t('auto_canvas_variant').format(
                    index=self._variant_index + 1, total=len(variants)
                )
            )
        elif crop_mode:
            self.variant_label.setText(i18n.t('auto_canvas_no_variants'))

        if crop_mode:
            self.hint_label.setText(
                i18n.t('auto_canvas_crop_hint').format(
                    percent=self._crop_percent
                )
            )
        else:
            self.hint_label.setText(i18n.t('auto_canvas_hint'))

    # ---------- Результат ----------

    def result_plan(self):
        """Выбранный пользователем вариант холста."""
        return self._plan
