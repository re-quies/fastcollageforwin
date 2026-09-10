import json
import logging
import os
import traceback
import webbrowser

from PySide6.QtWidgets import (
    QMainWindow,
    QFileDialog,
    QGraphicsView,
    QLabel,
    QInputDialog,
    QMessageBox,
    QToolButton,
    QPushButton,
    QStyle,
    QHBoxLayout,
    QWidget,
)
from PySide6.QtGui import (
    QPixmap,
    QImage,
    QPainter,
    QIcon,
    QAction,
    QGuiApplication,
    QUndoStack,
    QDragEnterEvent,
    QDropEvent,
)
from PySide6.QtCore import (
    Qt,
    QEvent,
    QMimeData,
    QPointF,
    QRectF,
    QSize,
    QUrl,
)

from ui.preview_panel import PreviewPanel
from canvas.image_item import ImageItem
from canvas.slot_item import TemplateSlotItem
from undo.commands import (
    AddFromPreviewCommand,
    AddItemCommand,
    ChangeLayerCommand,
    ChangeLayoutStyleCommand,
    ContentViewCommand,
    DeleteItemsCommand,
    MirrorCommand,
    ResizeCanvasCommand,
    ReturnToPreviewCommand,
    TransformCommand,
    WheelTransformCommand,
)
from canvas.scene import CanvasScene
from ui.auto_canvas_dialog import AutoCanvasDialog
from ui.auto_canvas_settings_dialog import AutoCanvasSettingsDialog
from ui.canvas_size_dialog import CanvasSizeDialog
from ui.export_dialog import EXPORT_TILE_HEIGHT, ExportOptionsDialog
from ui.image_preloader import ImagePreloader
from core import auto_layout
from core.collage_mode import CollageMode
from core.version import APP_VERSION
from core.async_loader import AsyncImageLoader
from core import file_types
from core import image_cache
from core import keymap
from core import project_io
from core import settings
from core.resources import resource_path
from ui.layout_style_dialog import LayoutStyleDialog
from ui.shortcuts_dialog import ShortcutsDialog
from ui.start_dialog import StartCollageDialog
import i18n

logger = logging.getLogger(__name__)


class GraphicsView(QGraphicsView):
    # Серое поле вокруг холста — предел для колеса, стрелок и
    # полос прокрутки. Задаётся долей размера коллажа с каждой
    # стороны: 1.0 значит, что у коллажа 600 px будет по 600 px
    # серого поля слева, справа, сверху и снизу. Протяжка средней
    # кнопкой этим полем не ограничена: она добавляет место по
    # требованию, а MAX_SCROLL_SLACK лишь страхует полосы прокрутки
    # от переполнения.
    SCROLL_MARGIN_RATIO = 1.0
    MAX_SCROLL_SLACK = 1000000.0

    # Значения по умолчанию живут на уровне класса: сцена
    # присваивается ещё в конструкторе QGraphicsView
    _scroll_slack_x = 0.0
    _scroll_slack_y = 0.0
    _watched_scene = None

    def _push_command(self, command):
        """Отправить команду в undo-стек окна.

        ФИКС: жесты колеса меняли элемент напрямую — Ctrl+Z их
        не видел, а окно не знало о несохранённых изменениях.
        Если стека почему-то нет (вид без окна в тестах) —
        применяем изменение напрямую: лучше сработать без
        истории, чем не сработать вовсе.
        """
        stack = getattr(self.window(), "undo_stack", None)

        if stack is not None:
            stack.push(command)
            return

        logger.warning("Undo stack is not available; applying without undo")
        command.redo()

    def wheelEvent(self, event):
        scene = self.scene()
        if not scene:
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta == 0:
            # Горизонтальное колесо и тачпад: вертикального шага нет,
            # но вид всё равно должен прокручиваться
            self._wheel_scroll(event)
            return

        factor = 1.1 if delta > 0 else 0.9
        # Колесом вращаем/масштабируем только изображения —
        # выделенный СЛОТ не должен деформироваться этими жестами
        selected = [
            it for it in scene.selectedItems() if isinstance(it, ImageItem)
        ]

        # =========================
        # SHIFT + WHEEL → ROTATE ITEM
        # =========================
        if event.modifiers() & Qt.ShiftModifier and selected:
            item = selected[0]
            angle = 5 if delta > 0 else -5

            # В слоте крутится СОДЕРЖИМОЕ: картинка заново вписывается
            # по cover и попадает в историю одним шагом (раньше этот
            # жест крутил сам элемент мимо undo и рвал разметку шаблона).
            # Свободный элемент по-прежнему поворачивается целиком.
            if not (
                hasattr(item, "rotate_in_slot") and item.rotate_in_slot(angle)
            ):
                # ФИКС: поворот свободного элемента шёл мимо истории
                self._push_command(WheelTransformCommand(
                    item,
                    'rotation',
                    item.rotation(),
                    item.rotation() + angle,
                ))

            event.accept()
            return

        # =========================
        # CTRL + WHEEL
        # =========================
        if event.modifiers() & Qt.ControlModifier:

            # 1) ЕСТЬ выделенный объект → масштаб ОБЪЕКТА
            if selected:
                item = selected[0]
                # ФИКС: масштаб объекта теперь отменяется Ctrl+Z
                self._push_command(WheelTransformCommand(
                    item, 'scale', item.scale(), item.scale() * factor
                ))
                event.accept()
                return

            # 2) НЕТ выделения → масштаб ХОЛСТА (VIEW)
            self.set_zoom_percent(
                self.zoom_percent + (10 if delta > 0 else -10)
            )
            event.accept()
            return

        # =========================
        # Z-MODE + WHEEL → ZOOM CONTENT
        # =========================
        if self.content_zoom_mode and selected:
            item = selected[0]
            if hasattr(item, "zoom_content"):
                # ФИКС: зум содержимого пишем в историю; шаги
                # одного прокрута склеиваются в один Ctrl+Z
                old_zoom, old_center = item.content_view_state()
                item.zoom_content(factor)
                new_zoom, new_center = item.content_view_state()

                if (
                    abs(old_zoom - new_zoom) > 1e-6
                    or old_center != new_center
                ):
                    self._push_command(ContentViewCommand(
                        item, old_zoom, old_center, new_zoom, new_center
                    ))

                event.accept()
                return

        # =========================
        # DEFAULT — прокрутка вида
        # =========================
        self._wheel_scroll(event)

    # Шаг колеса в пикселях на один щелчок (120 единиц угла)
    WHEEL_STEP_PX = 60

    def _wheel_scroll(self, event):
        """Прокрутить вид колесом, не упираясь в границы холста.

        Стандартный super().wheelEvent() двигает вид строго внутри
        области прокрутки, поэтому колесом нельзя было выехать за
        холст. _scroll_by_pixels расширяет свободное поле на ходу.
        """
        pixels = event.pixelDelta()
        dx = pixels.x()
        dy = pixels.y()

        if not dx and not dy:
            # Обычная мышь присылает угол поворота: 120 единиц — один
            # щелчок колеса, переводим его в пиксели прокрутки
            angle = event.angleDelta()
            dx = int(round(angle.x() / 120.0 * self.WHEEL_STEP_PX))
            dy = int(round(angle.y() / 120.0 * self.WHEEL_STEP_PX))

        # Shift + колесо без выделенного фото — горизонтальная
        # прокрутка, как в остальных редакторах
        if event.modifiers() & Qt.ShiftModifier and not dx:
            dx, dy = dy, 0

        if dx or dy:
            # Прокрутка колесом ограничена серым полем вокруг коллажа
            self._scroll_by_pixels(dx, dy, limited=True)

        event.accept()

    def keyPressEvent(self, event):
        # Раскладка больше не важна: core.keymap сверяет скан-код
        # клавиши, Qt.Key и введённый символ. Раньше здесь были
        # зашиты только латиница и кириллица, поэтому на испанской,
        # немецкой или арабской раскладке режимы не включались.

        # ФИКС: сочетания с модификаторами (Ctrl+Z, Ctrl+C, Alt+...)
        # больше не включают режимы Z/C. Раньше Ctrl+Z (отмена)
        # взводил content_zoom_mode, а отпускание клавиши часто
        # перехватывала сама горячая клавиша — режим «залипал»
        # и колесо мыши неожиданно зумило содержимое картинки.
        if event.modifiers() & (
            Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier
        ):
            super().keyPressEvent(event)
            return

        if keymap.matches(event, 'z'):
            self.content_zoom_mode = True
        if keymap.matches(event, 'c'):
            self.slot_pan_mode = True
        if keymap.matches(event, 'r'):
            self.slot_rotate_mode = True

        # Режим включён — сразу показать это курсором и в строке
        # состояния: раньше нажатая Z была невидимой, и колесо
        # мыши, неожиданно начавшее зумить фото, выглядело поломкой
        self.sync_mode_feedback()

        if keymap.matches(event, 'x'):
            self._return_selected_item_to_preview()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if keymap.matches(event, 'z'):
            self.content_zoom_mode = False
        if keymap.matches(event, 'c'):
            self.slot_pan_mode = False
        if keymap.matches(event, 'r'):
            self.slot_rotate_mode = False

        self.sync_mode_feedback()
        super().keyReleaseEvent(event)

    def reset_input_modes(self):
        """Сбросить временные режимы клавиш Z, C и R."""
        self.content_zoom_mode = False
        self.slot_pan_mode = False
        self.slot_rotate_mode = False
        self.sync_mode_feedback()

    def focusOutEvent(self, event):
        # ФИКС: если фокус ушёл (диалог, Alt+Tab, меню), событие
        # отпускания клавиши до нас уже не дойдёт и режим
        # остался бы включённым навсегда
        self.reset_input_modes()
        super().focusOutEvent(event)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self._zoom = 1.0
        self.zoom_percent = 100
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)

        self.setRenderHints(
            QPainter.Antialiasing |
            QPainter.SmoothPixmapTransform
        )
        self.setAcceptDrops(True)
        self.content_zoom_mode = False
        # Режим панорамирования изображения внутри слота (зажата клавиша C)
        self.slot_pan_mode = False
        # Режим поворота содержимого внутри слота (зажата клавиша R)
        self.slot_rotate_mode = False

        # Панорамирование холста средней кнопкой мыши
        self._canvas_panning = False
        self._pan_origin = None
        self._pan_remainder = QPointF(0.0, 0.0)

        # Свободное поле вокруг холста: панорамирование
        # не обязано останавливаться на его границах
        self._scroll_slack_x = 0.0
        self._scroll_slack_y = 0.0
        self.attach_scene(self.scene())

        # Последний показанный режим: курсор и индикатор
        # обновляются только при реальной смене режима
        self._feedback_mode = None

    def dragEnterEvent(self, event: QDragEnterEvent):
        md = event.mimeData()

        if (
            md.hasUrls()
            or md.hasImage()
            or md.hasFormat("application/x-preview-path")
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def _template_drop_allowed(self, view_pos) -> bool:
        """В template режиме бросать можно только в слот."""
        from canvas.slot_item import TemplateSlotItem

        scene = self.scene()
        if not getattr(scene, "is_template_mode", False):
            return True

        scene_pos = self.mapToScene(view_pos.toPoint())
        return any(
            isinstance(it, TemplateSlotItem)
            for it in scene.items(scene_pos)
        )

    def _reject_drop(self, event):
        """Отказать в drop с объяснением.

        Молчаливый ignore() неотличим от зависания: фото просто
        не появляется на холсте, и причина нигде не видна.
        """
        window = self.window()
        if hasattr(window, "notify"):
            window.notify(i18n.t('drop_not_allowed'))
        else:
            logger.info("Drop rejected: outside of a template slot")

        event.ignore()

    def dropEvent(self, event: QDropEvent):
        md = event.mimeData()
        view_pos = event.position()

        # 1) Drag из проводника
        if md.hasUrls():
            if not self._template_drop_allowed(view_pos):
                self._reject_drop(event)
                return

            for url in md.urls():
                path = url.toLocalFile()
                if path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
                    self._add_image_from_path(path, view_pos)
            event.acceptProposedAction()
            return

        # 2) Drag из панели превью: передаётся ПУТЬ к файлу —
        # полноразмерное изображение загружается только здесь
        # (экономия ����амяти: превью не хранит полноразмерные картинки)
        if md.hasFormat("application/x-preview-path"):
            if not self._template_drop_allowed(view_pos):
                self._reject_drop(event)
                return

            path = bytes(md.data("application/x-preview-path")).decode("utf-8")

            # ФИКС: добавление на холст и удаление из панели превью —
            # О��НА атомарная undo-команда (AddFromPreviewCommand).
            # Раньше элемент удалялся из панели отдельно, мимо undo-
            # стека, и Ctrl+Z убирал фото с холста, не возвращая его
            # в панель: изображение терялось и там, и там.
            self._add_image_from_path(path, view_pos, from_preview=True)

            event.acceptProposedAction()
            return

        # 3) Fallback: превью-элемент без пути к файлу
        if md.hasImage():
            pixmap = md.imageData()
            if isinstance(pixmap, QPixmap) and not pixmap.isNull():
                if not self._template_drop_allowed(view_pos):
                    self._reject_drop(event)
                    return

                # ЕСЛИ drag пришёл из превью — удаление из панели
                # выполняется внутри AddFromPreviewCommand (по cacheKey
                # пиксмапа), чтобы Ctrl+Z возвращал фото обратно в панель
                self._add_image_from_pixmap(
                    pixmap,
                    view_pos,
                    from_preview=md.hasFormat("application/x-preview-item"),
                )

            event.acceptProposedAction()
            return

        event.ignore()

    def _add_image_from_path(self, path: str, view_pos, from_preview=False) -> bool:
        # ФИКС (память): на холст кладём уменьшенную рабочую копию;
        # оригинал подгружается только на экспорте (core.image_cache)
        pixmap = image_cache.load_working_pixmap(path)
        if pixmap.isNull():
            logger.warning("Failed to load image from %s", path)
            return False

        self._add_image_from_pixmap(
            pixmap, view_pos, source_path=path, from_preview=from_preview
        )
        return True

    def _make_add_command(
        self, scene, item, slot, from_preview, source_path, pixmap
    ):
        """Команда добавления изображения на холст.

        Для drag&drop из панели превью — AddFromPreviewCommand:
        удаление из панели и добавление на холст становятся одной
        атомарной операцией undo/redo.
        """
        if not from_preview:
            return AddItemCommand(scene, item, slot, self.window())

        return AddFromPreviewCommand(
            scene, item, slot, self.window(), source_path, pixmap
        )

    def _add_image_from_pixmap(
        self, pixmap: QPixmap, view_pos, source_path=None, from_preview=False
    ):
        from canvas.image_item import ImageItem
        from canvas.slot_item import TemplateSlotItem

        scene = self.scene()
        if scene is None:
            logger.warning("Drop ignored: the view has no scene")
            return

        # ФИКС (баг 1): drag&drop теперь добавляет изображение через
        # undo-стек (AddItemCommand), как и добавление через меню —
        # раньше перетащенную картинку нельзя было отменить через Ctrl+Z.
        window = self.window()
        undo_stack = getattr(window, "undo_stack", None)

        scene_pos = self.mapToScene(view_pos.toPoint())

        # Если мы в template mode — попытаемся положить изображение в слот
        if getattr(scene, "is_template_mode", False):
            slot = None
            for it in scene.items(scene_pos):
                if isinstance(it, TemplateSlotItem):
                    slot = it
                    break

            if slot is not None:
                # Снимаем предыдущее выделение,
                # чтобы не выделялись сразу все добавленные элементы
                scene.clearSelection()

                item = ImageItem(pixmap, source_path)
                self._apply_swap_delay(scene, item)

                # команда со слотом: redo добавляет на сцену и помещает
                # в слот, undo очищает ссылку слота
                command = self._make_add_command(
                    scene, item, slot, from_preview, source_path, pixmap
                )

                if undo_stack is not None:
                    undo_stack.push(command)
                else:
                    command.redo()

                item.setSelected(True)
                return

        # Обычное поведение — свободный ImageItem
        scene.clearSelection()

        item = ImageItem(pixmap, source_path)
        item.setPos(
            scene_pos
            - QPointF(pixmap.width() / 2, pixmap.height() / 2)
        )
        self._apply_swap_delay(scene, item)

        command = self._make_add_command(
            scene, item, None, from_preview, source_path, pixmap
        )

        if undo_stack is not None:
            undo_stack.push(command)
        else:
            command.redo()

        item.setSelected(True)

    @staticmethod
    def _apply_swap_delay(scene, item):
        # ФИКС (пункт 3/4 ревью): раньше здесь было getattr(self.scene, ...) —
        # обращение к МЕТОДУ scene, а не к сцене, поэтому задержка
        # никогда не ��рименялась (баг был скрыт try/except: pass).
        delay = getattr(scene, "swap_delay_ms", None)
        if delay is not None and hasattr(item, "_hover_timer"):
            item._hover_timer.setInterval(int(delay))

    # ФИКС (баг 2): раньше зум отслеживался двумя независимыми
    # переменными: _zoom (кнопки +/-) и zoom_percent (Ctrl+колесо,
    # диалог "Масштаб..."), которые не синхронизировались между
    # собой. Теперь единственный ис  очник истины — zoom_percent,
    # а все пути изменения масштаба проходят че����ез set_zoom_percent().

    def zoom_in(self):
        self.set_zoom_percent(round(self.zoom_percent * 1.1))

    def zoom_out(self):
        self.set_zoom_percent(round(self.zoom_percent / 1.1))

    def reset_zoom(self):
        self.set_zoom_percent(100)

    def set_zoom_percent(self, percent: int):
        percent = max(10, min(int(round(percent)), 800))

        self.resetTransform()
        factor = percent / 100.0
        self.scale(factor, factor)

        self.zoom_percent = percent
        self._zoom = factor

        # Масштаб поменял размер видимой области — значит,
        # и свободное поле вокруг холста тоже
        self.update_scroll_area()

        window = self.window()
        if hasattr(window, "update_zoom_label"):
            window.update_zoom_label(percent)

        if hasattr(window, "update_pan_buttons"):
            window.update_pan_buttons()

    # ---------- Панорамирование холста ----------

    # Шаг стрелок — доля видимой области. Фиксированный шаг в
    # пикселях сцены на сильном увеличении был бы почти незаметен,
    # а на уменьшении перебрасывал бы через весь коллаж.
    PAN_STEP_RATIO = 0.25

    def _visible_scene_rect(self) -> QRectF:
        """Видимая область в координатах сцены."""
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def _watch_scene(self, scene):
        """Следить за размером холста активной сцены."""
        old = self._watched_scene
        if old is scene:
            return

        if old is not None:
            try:
                old.sceneRectChanged.disconnect(self._on_scene_rect_changed)
            except (RuntimeError, TypeError):
                # Старая сцена уже удалена или сигнал не был подключён
                pass

        self._watched_scene = scene

        if scene is not None:
            scene.sceneRectChanged.connect(self._on_scene_rect_changed)

    def attach_scene(self, scene):
        """Подключиться к сцене и пересчитать поле вокруг холста."""
        self._watch_scene(scene)
        self.reset_scroll_area()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Размер окна изменился — запас вокруг холста считаем заново
        self.update_scroll_area()

    def _on_scene_rect_changed(self, _rect):
        # Размер холста менялся (Ctrl+Shift+C, открытие проекта):
        # накопленный запас больше не актуален
        self.reset_scroll_area()

    def reset_scroll_area(self):
        """Сбросить накопленный запас и пересчитать область прокрутки."""
        self._scroll_slack_x = 0.0
        self._scroll_slack_y = 0.0
        self.update_scroll_area()

    def update_scroll_area(self, extra_x: float = 0.0, extra_y: float = 0.0):
        """Область прокрутки = холст, серое поле и запас протяжки.

        Базовая область (_limit_rect) — предел для колеса и стрелок.
        Протяжка средней кнопкой добавляет к ней запас по
        требованию (extra_x/extra_y), поэтому мышью можно уехать
        сколь угодно далеко; когда вид вернётся к холсту, запас
        снимается сам (_shrink_scroll_area_if_possible).
        """
        scene = self.scene()
        if scene is None:
            return

        self._scroll_slack_x = min(
            self.MAX_SCROLL_SLACK, self._scroll_slack_x + max(0.0, extra_x)
        )
        self._scroll_slack_y = min(
            self.MAX_SCROLL_SLACK, self._scroll_slack_y + max(0.0, extra_y)
        )

        # К базовому пределу добавляется только запас, накопленный
        # протяжкой средней кнопкой мыши
        slack_x = self._scroll_slack_x
        slack_y = self._scroll_slack_y
        area = self._limit_rect().adjusted(
            -slack_x, -slack_y, slack_x, slack_y
        )

        # Значения полос прокрутки отсчитываются от левого верхнего
        # угла области: без возврата центра вид прыгал бы при
        # каждом пересчёте
        center = self._visible_scene_rect().center()
        self.setSceneRect(area)
        self.centerOn(center)

    def _limit_rect(self) -> QRectF:
        """Докуда можно доехать колесом и стрелками.

        Холст (плюс габариты фото, если какое-то из них лежит
        за его краем) и серое поле вокруг размером в
        SCROLL_MARGIN_RATIO от стороны коллажа. Дальше колесо и
        стрелки не пускают: чтобы увидеть больше, есть
        уменьшение масштаба по Ctrl+колесо.
        """
        scene = self.scene()
        if scene is None:
            return QRectF()

        canvas = scene.sceneRect()
        area = canvas
        items = scene.itemsBoundingRect()
        if not items.isNull():
            area = area.united(items)

        margin_x = canvas.width() * self.SCROLL_MARGIN_RATIO
        margin_y = canvas.height() * self.SCROLL_MARGIN_RATIO
        return area.adjusted(-margin_x, -margin_y, margin_x, margin_y)

    def _clamped_delta(self, dx: int, dy: int) -> tuple:
        """Обрезать шаг колеса или стрелки по границам серого поля."""
        if self.scene() is None:
            return 0, 0

        visible = self._visible_scene_rect()
        limit = self._limit_rect()
        transform = self.transform()
        # Пиксели вида в единицы сцены переводит текущий масштаб
        scale_x = transform.m11() or 1.0
        scale_y = transform.m22() or 1.0

        # Положительный dx уводит вид влево, поэтому запас слева —
        # это расстояние от края видимой области до края поля.
        # Ноль всегда допустим: если протяжка средней кнопкой увела
        # вид за поле, колесо не дёрнет его обратно рывком, но и
        # дальше уехать не даст.
        room_left = (visible.left() - limit.left()) * scale_x
        room_right = (visible.right() - limit.right()) * scale_x
        dx = max(min(0.0, room_right), min(float(dx), max(0.0, room_left)))

        room_up = (visible.top() - limit.top()) * scale_y
        room_down = (visible.bottom() - limit.bottom()) * scale_y
        dy = max(min(0.0, room_down), min(float(dy), max(0.0, room_up)))

        # int обрезает к нулю, так что за границу не перескочим
        return int(dx), int(dy)

    def _shrink_scroll_area_if_possible(self):
        """Снять запас протяжки, когда вид вернулся к холсту.

        Иначе после одной протяжки далеко от коллажа полосы
        прокрутки навсегда остались бы растянутыми.
        """
        if not self._scroll_slack_x and not self._scroll_slack_y:
            return

        if self._limit_rect().contains(self._visible_scene_rect()):
            self.reset_scroll_area()

    def _scroll_by_pixels(self, dx: int, dy: int, limited: bool = False):
        """Сдвинуть вид на dx/dy пикселей.

        limited=True — колесо и стрелки: дальше серого поля вокруг
        коллажа вид не уедет. limited=False — протяжка средней
        кнопкой: свободное место добавляется по требованию,
        границ нет.
        """
        if limited:
            dx, dy = self._clamped_delta(dx, dy)

        if not dx and not dy:
            return

        h_bar = self.horizontalScrollBar()
        v_bar = self.verticalScrollBar()

        if not limited:
            need_x = bool(dx) and not (
                h_bar.minimum() <= h_bar.value() - dx <= h_bar.maximum()
            )
            need_y = bool(dy) and not (
                v_bar.minimum() <= v_bar.value() - dy <= v_bar.maximum()
            )

            # Упёрлись в край — добавляем ещё одну видимую область
            # свободного места: протяжка не останавливается
            if need_x or need_y:
                visible = self._visible_scene_rect()
                self.update_scroll_area(
                    extra_x=visible.width() if need_x else 0.0,
                    extra_y=visible.height() if need_y else 0.0,
                )

        if dx:
            h_bar.setValue(h_bar.value() - dx)

        if dy:
            v_bar.setValue(v_bar.value() - dy)

        if limited:
            self._shrink_scroll_area_if_possible()

    def pan_by(self, dx_ratio: float = 0.0, dy_ratio: float = 0.0):
        """Сдвинуть видимую область на долю её размера."""
        viewport = self.viewport()
        dx = 0
        dy = 0

        if dx_ratio:
            step = max(1, int(viewport.width() * self.PAN_STEP_RATIO))
            dx = -int(round(step * dx_ratio))

        if dy_ratio:
            step = max(1, int(viewport.height() * self.PAN_STEP_RATIO))
            dy = -int(round(step * dy_ratio))

        # Стрелки, как и колесо, не выходят за серое поле
        self._scroll_by_pixels(dx, dy, limited=True)

    # *_ — clicked() передаёт аргумент checked, он здесь не нужен
    def pan_left(self, *_):
        self.pan_by(dx_ratio=-1.0)

    def pan_right(self, *_):
        self.pan_by(dx_ratio=1.0)

    def pan_up(self, *_):
        self.pan_by(dy_ratio=-1.0)

    def pan_down(self, *_):
        self.pan_by(dy_ratio=1.0)

    def pan_availability(self) -> dict:
        """Куда стрелки ещё могут сдвинуть вид.

        Стрелки ограничены серым полем вокруг коллажа, поэтому
        у его края гаснут. Если протяжка средней кнопкой увела
        вид за поле, активными останутся стрелки, ведущие назад.
        """
        if self.scene() is None:
            return {
                "left": False,
                "right": False,
                "up": False,
                "down": False,
            }

        visible = self._visible_scene_rect()
        limit = self._limit_rect()
        # Полпикселя запаса: при дробном масштабе края совпадают
        # не точно, и стрелка мигала бы впустую
        return {
            "left": visible.left() - limit.left() > 0.5,
            "right": limit.right() - visible.right() > 0.5,
            "up": visible.top() - limit.top() > 0.5,
            "down": limit.bottom() - visible.bottom() > 0.5,
        }

    # ---------- Панорамирование средней кнопкой мыши ----------

    def mousePressEvent(self, event):
        # Средняя кнопка перехватывается ДО сцены: QGraphicsItem по
        # умолчанию принимает все кнопки мыши, поэтому иначе нажатие
        # доставалось бы фотографии под курсором и холст не двигался
        # ровно там, где это нужнее всего — поверх коллажа.
        if event.button() == Qt.MiddleButton:
            self._begin_canvas_pan(event.position())
            event.accept()
            return

        # Во время протяжки остальные кнопки игнорируются: случайный
        # клик левой не должен сбивать выделение или тащить фото
        if self._canvas_panning:
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Быстрый повторный щелчок приходит как double click:
        # без перехвата он ушёл бы в элемент сцены, и каждое
        # второе нажатие средней кнопкой не начинало бы сдвиг.
        if event.button() == Qt.MiddleButton:
            self._begin_canvas_pan(event.position())
            event.accept()
            return

        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self._canvas_panning:
            self._update_canvas_pan(event.position())
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._canvas_panning:
            if event.button() == Qt.MiddleButton:
                self._end_canvas_pan()

            event.accept()
            return

        super().mouseReleaseEvent(event)

    def _begin_canvas_pan(self, position):
        self._canvas_panning = True
        self._pan_origin = QPointF(position)
        self._pan_remainder = QPointF(0.0, 0.0)
        self.sync_mode_feedback()

    def _update_canvas_pan(self, position):
        if self._pan_origin is None:
            return

        # Дробный остаток копится между событиями: у тачпада и на
        # экранах с масштабированием сдвиг за одно событие бывает
        # меньше пикселя, и округление съедало бы медленное движение
        delta = QPointF(position) - self._pan_origin + self._pan_remainder
        dx = int(delta.x())
        dy = int(delta.y())

        self._pan_origin = QPointF(position)
        self._pan_remainder = QPointF(delta.x() - dx, delta.y() - dy)

        # Протяжка больше не упирается в границы холста:
        # поле вокруг расширяется на ходу
        self._scroll_by_pixels(dx, dy)

    def _end_canvas_pan(self):
        self._canvas_panning = False
        self._pan_origin = None
        self.sync_mode_feedback()

    # ---------- Индикация активного режима ----------

    # Курсор — вторая половина индикации: видно сразу
    # в той точке, где человек работает, а не только внизу окна
    MODE_CURSORS = {
        "canvas_pan": Qt.ClosedHandCursor,
        "content_zoom": Qt.SizeVerCursor,
        "slot_pan": Qt.OpenHandCursor,
        "slot_rotate": Qt.CrossCursor,
    }

    def current_input_mode(self) -> str:
        """Активный режим ввода — для индикатора и курсора."""
        # Мышь важнее клавиш: пока холст тянут средней кнопкой,
        # перетаскивание уже занято панорамированием
        if self._canvas_panning:
            return "canvas_pan"
        if self.content_zoom_mode:
            return "content_zoom"
        if self.slot_pan_mode:
            return "slot_pan"
        if self.slot_rotate_mode:
            return "slot_rotate"

        return "normal"

    def sync_mode_feedback(self):
        """Обнов��т�� курсор и индикатор в строке состояния."""
        mode = self.current_input_mode()

        # Клавиша с автоповтором присылает десятки событий в
        # секунду — перерисовываем только при смене режима
        if mode == self._feedback_mode:
            return

        self._feedback_mode = mode

        cursor = self.MODE_CURSORS.get(mode)
        if cursor is None:
            self.viewport().unsetCursor()
        else:
            self.viewport().setCursor(cursor)

        window = self.window()
        if hasattr(window, "update_mode_indicator"):
            window.update_mode_indicator(mode)

    def _return_selected_item_to_preview(self):
        window = self.window()

        def complain(key):
            """Нажатие впустую должно объяснять себя, а не молчать."""
            if hasattr(window, "notify"):
                window.notify(i18n.t(key))
            else:
                logger.info("Return to preview: %s", key)

        scene = self.scene()
        if not scene:
            logger.warning("Return to preview: the view has no scene")
            return

        items = scene.selectedItems()
        if not items:
            complain('nothing_selected')
            return

        item = items[0]

        # Нас интересуют только ImageItem
        if not hasattr(item, "original_pixmap"):
            complain('nothing_selected')
            return

        pixmap = item.original_pixmap
        if pixmap.isNull():
            logger.warning("Return to preview: the item has no pixmap")
            return

        if not hasattr(window, "preview_panel"):
            logger.warning("Return to preview: no preview panel in the window")
            return

        # ФИКС (баг 3): возврат в превью и удаление с холста — одна
        # атомарная undo-команда. Раньше в превью добавляли напрямую,
        # и после Ctrl+Z изображение возвращалось на холст, но его
        # копия оставалась в панели превью.
        if hasattr(window, "undo_stack"):
            window.undo_stack.push(
                ReturnToPreviewCommand(scene, window, item)
            )
        else:
            window.preview_panel.add_pixmap(
                pixmap, getattr(item, "source_path", None)
            )
            scene.removeItem(item)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # Настройки читаем ДО создания меню: язык влияет на все
        # подписи интерфейса (см. core/settings.py)
        i18n.set_lang(settings.language())
        self._apply_layout_direction()

        # Задержка обмена: 0 мс — слот готов сразу, без удержания
        # и обратного отсчёта. Значение переживает перезапуск.
        self.swap_delay_ms = settings.swap_delay_ms()

        # Текущий файл проекта и «грязное» состояние для диалога
        # «сохранить изменения?» при закрытии
        self.project_path = None
        self._dirty = False

        # Предупреждение о несохранённых изменениях можно выключить:
        # Настройки → Предупреждать о несохранённых изменениях
        self.warn_unsaved = settings.confirm_unsaved()
        self.warn_unsaved_action = None

        self.undo_stack = QUndoStack(self)
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)

        # Глубина истории ограничена: команды держат живые элементы
        # сцены и их пиксмапы, поэтому неограниченный стек за долгий
        # сеанс съедал сотни мегабайт. Вытесненные команды Qt
        # удаляет — вместе с ними освобождаются и пиксмапы
        self.undo_stack.setUndoLimit(settings.undo_limit())

        # Фоновая загрузка изображений холста: файлы декодируются
        # в рабочих потоках и появляются на сцене по мере готовности
        self._image_loader = AsyncImageLoader(self)
        self._image_loader.loaded.connect(self._on_canvas_image_loaded)

        # Предзагрузка файлов проекта с прогрессом и без вложенного
        # цикла событий (см. ui/image_preloader.py)
        self._preloader = ImagePreloader(self)

        self.scene = CanvasScene()
        self.scene.swap_delay_ms = self.swap_delay_ms
        self.view = GraphicsView(self.scene)

        self.setCentralWidget(self.view)
        self.view.setAcceptDrops(True)

        # Индикатор активного режима (Z, C, R, панорама мышью):
        # без него включённый режим невидим, а «��алипшая» клавиша
        # выглядит как поломка колеса мыши
        self.mode_label = QLabel(self)
        self.mode_label.setObjectName("mode_label")
        self._current_mode = None
        self.statusBar().addPermanentWidget(self.mode_label)
        self.update_mode_indicator("normal")

        self.zoom_label = QLabel("100%")
        self.statusBar().addPermanentWidget(self.zoom_label)

        # Добавляем кно  ки масштаба справа снизу
        self.zoom_minus = QPushButton("-", self)
        self.zoom_minus.setFixedSize(24, 24)
        self.zoom_minus.clicked.connect(lambda: self.view.zoom_out())
        self.statusBar().addPermanentWidget(self.zoom_minus)

        self.zoom_plus = QPushButton("+", self)
        self.zoom_plus.setFixedSize(24, 24)
        self.zoom_plus.clicked.connect(lambda: self.view.zoom_in())
        self.statusBar().addPermanentWidget(self.zoom_plus)

        # Стрелки перемеще��ия по холсту: у большого коллажа край
        # уходит за пределы окна, а колесо мыши занято масштабом
        self._create_pan_buttons()

        # Кнопка для регенерации случайной сетки в TEMPLATE режиме
        self.regen_grid_btn = QToolButton(self)

        # ФИКС: путь к иконке был относительным (зависел от текущей
        # рабочей директории), а в сборке PyInstaller ресурсы лежат
        # во временной папке sys._MEIPASS — кнопка оставалась пустой
        # и выглядела как невидимый квадратик в строке состояния.
        icon = QIcon(resource_path("assets", "icons", "new_grid.svg"))
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_BrowserReload)

        self.regen_grid_btn.setIcon(icon)
        self.regen_grid_btn.setIconSize(QSize(18, 18))

        self.regen_grid_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.regen_grid_btn.clicked.connect(self.regenerate_template_grid)
        self.regen_grid_btn.setToolTip(i18n.t('new_grid_tooltip'))

        self.statusBar().addPermanentWidget(self.regen_grid_btn)

        # Кнопка подбора холста под фотографии из панели превью
        self.auto_canvas_btn = QToolButton(self)

        auto_icon = QIcon(
            resource_path("assets", "icons", "auto_canvas.svg")
        )
        if auto_icon.isNull():
            auto_icon = self.style().standardIcon(
                QStyle.SP_FileDialogContentsView
            )

        self.auto_canvas_btn.setIcon(auto_icon)
        self.auto_canvas_btn.setIconSize(QSize(18, 18))

        # Текст рядом с иконкой: если SVG вдруг не отрисуется,
        # кнопка не превратится в невидимый квадратик в строке состояния
        self.auto_canvas_btn.setText(i18n.t('auto_canvas_action'))
        self.auto_canvas_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.auto_canvas_btn.clicked.connect(self.auto_fit_canvas)
        self.auto_canvas_btn.setToolTip(i18n.t('auto_canvas_tooltip'))

        self.statusBar().addPermanentWidget(self.auto_canvas_btn)

        self.preview_panel = PreviewPanel(self)
        # Имя нужно QMainWindow.saveState(): без него раскладка
        # панелей не сохраняется между запусками
        self.preview_panel.setObjectName("preview_panel")
        self.addDockWidget(Qt.LeftDockWidgetArea, self.preview_panel)

        # ФИКС: галочка «Панель изображений» рассинхронизировалась,
        # если панель закрывали крестиком: пункт меню оставался
        # отмеченным, и первый клик по нему ничего не делал.
        self.toggle_preview_action = None
        self.preview_panel.visibilityChanged.connect(
            self._on_preview_visibility_changed
        )

        self.collage_mode = CollageMode.FREE
        self._create_menu()

        # Геомет��ия окна и раскладка панелей — из прошлого сеанса
        self._restore_window_state()
        self._update_title()

    # ---------- Menu ----------
    def _create_menu(self):
        self.menuBar().clear()
        file_menu = self.menuBar().addMenu(i18n.t('file'))
        new_action = QAction(i18n.t('new_collage'), self)
        new_action.triggered.connect(self.create_new_collage)
        file_menu.addAction(new_action)

        open_project_action = QAction(i18n.t('open_project'), self)
        open_project_action.setShortcut("Ctrl+Shift+O")
        open_project_action.triggered.connect(self.open_project)
        file_menu.addAction(open_project_action)

        save_project_action = QAction(i18n.t('save_project'), self)
        save_project_action.setShortcut("Ctrl+S")
        save_project_action.triggered.connect(self.save_project)
        file_menu.addAction(save_project_action)

        save_as_action = QAction(i18n.t('save_project_as'), self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self.save_project_as)
        file_menu.addAction(save_as_action)

        # Список последних проектов (хранится в QSettings)
        self.recent_menu = file_menu.addMenu(i18n.t('recent_projects'))
        self._refresh_recent_menu()

        add_image_action = QAction(i18n.t('add_image'), self)
        add_image_action.setShortcut("Ctrl+O")
        add_image_action.triggered.connect(self.add_image)
        file_menu.addAction(add_image_action)
        add_to_panel_action = QAction(i18n.t('load_to_panel'), self)
        add_to_panel_action.triggered.connect(self.add_images_to_panel)
        file_menu.addAction(add_to_panel_action)

        export_action = QAction(i18n.t('export'), self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export_image)
        file_menu.addAction(export_action)

        edit_menu = self.menuBar().addMenu(i18n.t('edit'))

        undo_action = self.undo_stack.createUndoAction(self, i18n.t('undo'))
        undo_action.setShortcut("Ctrl+Z")
        edit_menu.addAction(undo_action)
        redo_action = self.undo_stack.createRedoAction(self, i18n.t('redo'))
        redo_action.setShortcut("Ctrl+Y")
        edit_menu.addAction(redo_action)

        delete_action = QAction(i18n.t('delete'), self)
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(self.delete_selected)
        edit_menu.addAction(delete_action)

        edit_menu.addSeparator()

        copy_action = QAction(i18n.t('copy'), self)
        copy_action.setShortcut("Ctrl+C")
        copy_action.triggered.connect(self.copy_selection)
        edit_menu.addAction(copy_action)

        paste_action = QAction(i18n.t('paste'), self)
        paste_action.setShortcut("Ctrl+V")
        paste_action.triggered.connect(self.paste_from_clipboard)
        edit_menu.addAction(paste_action)

        duplicate_action = QAction(i18n.t('duplicate'), self)
        duplicate_action.setShortcut("Ctrl+D")
        duplicate_action.triggered.connect(self.duplicate_selection)
        edit_menu.addAction(duplicate_action)

        layer_menu = self.menuBar().addMenu(i18n.t('layers'))

        bring_front = QAction(i18n.t('bring_front'), self)
        bring_front.setShortcut("Ctrl+]")
        bring_front.triggered.connect(self.bring_to_front)
        send_back = QAction(i18n.t('send_back'), self)
        send_back.setShortcut("Ctrl+[")
        send_back.triggered.connect(self.send_to_back)

        layer_menu.addAction(bring_front)
        layer_menu.addAction(send_back)

        canvas_menu = self.menuBar().addMenu(i18n.t('canvas'))

        resize_action = QAction(i18n.t('canvas_size'), self)
        resize_action.setShortcut("Ctrl+Shift+C")
        resize_action.triggered.connect(self.change_canvas_size)
        canvas_menu.addAction(resize_action)

        style_action = QAction(i18n.t('layout_style'), self)
        style_action.triggered.connect(self.change_layout_style)
        canvas_menu.addAction(style_action)

        # То же действие, что и кнопка в строке состояния
        auto_canvas_action = QAction(i18n.t('auto_canvas_action'), self)
        auto_canvas_action.setShortcut("Ctrl+Shift+A")
        auto_canvas_action.triggered.connect(self.auto_fit_canvas)
        canvas_menu.addAction(auto_canvas_action)

        # Добавляем новые действия для зеркалирования
        mirror_menu = self.menuBar().addMenu(i18n.t('image_menu'))

        horizontal_mirror_action = QAction(i18n.t('mirror_h'), self)
        horizontal_mirror_action.setShortcut("Ctrl+Shift+H")
        horizontal_mirror_action.triggered.connect(self.horizontal_mirror)
        mirror_menu.addAction(horizontal_mirror_action)

        vertical_mirror_action = QAction(i18n.t('mirror_v'), self)
        vertical_mirror_action.setShortcut("Ctrl+Shift+V")
        vertical_mirror_action.triggered.connect(self.vertical_mirror)
        mirror_menu.addAction(vertical_mirror_action)

        mirror_menu.addSeparator()

        rotate_left_action = QAction(i18n.t('rotate_left'), self)
        rotate_left_action.setShortcut("Ctrl+Shift+L")
        rotate_left_action.triggered.connect(lambda: self.rotate_selected(-90))
        mirror_menu.addAction(rotate_left_action)

        rotate_right_action = QAction(i18n.t('rotate_right'), self)
        rotate_right_action.setShortcut("Ctrl+Shift+R")
        rotate_right_action.triggered.connect(lambda: self.rotate_selected(90))
        mirror_menu.addAction(rotate_right_action)

        reset_rotation_action = QAction(i18n.t('reset_rotation'), self)
        reset_rotation_action.triggered.connect(self.reset_rotation)
        mirror_menu.addAction(reset_rotation_action)

        developer_menu = self.menuBar().addMenu(i18n.t('developer'))
        developer_action = QAction(i18n.t('open_github'), self)
        developer_action.triggered.connect(self.open_github)
        developer_menu.addAction(developer_action)

        view_menu = self.menuBar().addMenu(i18n.t('view'))

        toggle_preview = QAction(i18n.t('toggle_preview'), self)
        toggle_preview.setCheckable(True)
        toggle_preview.setChecked(self.preview_panel.isVisible())
        toggle_preview.triggered.connect(
            lambda checked: self.preview_panel.setVisible(checked)
        )
        self.toggle_preview_action = toggle_preview

        view_menu.addAction(toggle_preview)

        zoom_action = QAction(i18n.t('zoom'), self)
        zoom_action.setShortcut("Ctrl+M")
        zoom_action.triggered.connect(self.set_exact_zoom)
        view_menu.addAction(zoom_action)

        # --- Settings menu ---
        settings_menu = self.menuBar().addMenu(i18n.t('settings'))

        # Справка по горячим клавишам: жесты Z, C, R, X и
        # средняя кнопка мыши нигде в интерфейсе не подписаны
        shortcuts_action = QAction(i18n.t('shortcuts_action'), self)
        shortcuts_action.setShortcut("F1")
        shortcuts_action.triggered.connect(self.show_shortcuts)
        settings_menu.addAction(shortcuts_action)

        settings_menu.addSeparator()

        # Переключатель вопроса о сохранении: при быстрой сборке
        # коллажей, которые не нужно хранить, он только мешает
        warn_action = QAction(i18n.t('warn_unsaved'), self)
        warn_action.setCheckable(True)
        warn_action.setChecked(self.warn_unsaved)
        warn_action.setToolTip(i18n.t('warn_unsaved_hint'))
        warn_action.toggled.connect(self.set_warn_unsaved)
        settings_menu.addAction(warn_action)
        self.warn_unsaved_action = warn_action

        swap_delay_action = QAction(i18n.t('swap_delay'), self)
        swap_delay_action.triggered.connect(self.change_swap_delay)
        settings_menu.addAction(swap_delay_action)

        # Параметры второго режима «Холста под фото»: были
        # константами в core/auto_layout.py, теперь видны в интерфейсе
        auto_canvas_settings_action = QAction(
            i18n.t('auto_canvas_settings'), self
        )
        auto_canvas_settings_action.triggered.connect(
            self.change_auto_canvas_settings
        )
        settings_menu.addAction(auto_canvas_settings_action)

        # Список языков берётся из i18n: раньше ru/en/es были
        # зашиты здесь, и четвёртый язык требовал правки кода
        language_menu = settings_menu.addMenu(i18n.t('language'))
        for code in i18n.available_languages():
            action = QAction(i18n.language_name(code), self)
            action.setCheckable(True)
            action.setChecked(code == i18n.current_lang)
            action.triggered.connect(
                lambda checked=False, c=code: self.set_language(c)
            )
            language_menu.addAction(action)

    def change_auto_canvas_settings(self):
        """Настройки второго режима подбора холста под фото.

        Значения читаются в момент открытия диалога подбора,
        так что перезапуск приложения не нужен.
        """
        dialog = AutoCanvasSettingsDialog(self)
        if not dialog.exec():
            return

        self.statusBar().showMessage(
            i18n.t('auto_canvas_settings_saved'), 4000
        )

    def _on_preview_visibility_changed(self, visible: bool):
        """Синхронизировать галочку меню с реальным состоянием панели."""
        action = getattr(self, "toggle_preview_action", None)
        if action is None:
            return

        action.blockSignals(True)
        action.setChecked(bool(visible))
        action.blockSignals(False)

    def _refresh_recent_menu(self):
        """Пересобрать подменю «Последние проекты» из QSettings."""
        menu = getattr(self, "recent_menu", None)
        if menu is None:
            return

        menu.clear()
        paths = settings.recent_projects()

        if not paths:
            empty = QAction(i18n.t('recent_empty'), self)
            empty.setEnabled(False)
            menu.addAction(empty)
            return

        for path in paths:
            action = QAction(path, self)
            # default-аргумент фиксирует путь: без него все пункты
            # ссылались бы на последний элемент цикла
            action.triggered.connect(
                lambda checked=False, p=path: self.open_project_path(p)
            )
            menu.addAction(action)

    def add_images_to_panel(self):
        """Загрузка в панель идёт мимо undo.

        ФИКС: флаг несохранённых изменений ставит теперь сама
        панель — и только если фотографии действительно добавились.
        Раньше mark_dirty() вызывался даже после отмены диалога
        выбора файлов, и приложение спрашивало о сохранении
        на ровном месте.
        """
        self.preview_panel.add_images_from_files()

    # ---------- Настройки и состояние окна ----------

    def _restore_window_state(self):
        """Вернуть размер окна и р��складку панелей про����лого сеанса."""
        geometry = settings.window_geometry()
        state = settings.window_state()

        if geometry:
            self.restoreGeometry(geometry)
        else:
            # Первый запуск — как раньше: развёрнутое окно
            self.resize(1200, 800)
            self.showMaximized()

        if state:
            self.restoreState(state)
        else:
            self.preview_panel.setVisible(settings.preview_visible())

    def _save_settings(self):
        """Сохранить настройки при выходе."""
        settings.save_window(self.saveGeometry(), self.saveState())
        settings.set_preview_visible(self.preview_panel.isVisible())
        settings.set_swap_delay_ms(self.swap_delay_ms)
        settings.set_language(i18n.current_lang)

    # ---------- Несохранённые изменения ----------

    def is_dirty(self) -> bool:
        """Есть ли изменения, которых нет в файле проекта.

        Основной источник истины — undo-стек (QUndoStack.isClean()),
        плюс флаг для правок мимо истории: панель превью,
        регенерация сетки, смена задержки обмена.
        """
        return self._dirty or not self.undo_stack.isClean()

    def notify(self, message: str, timeout: int = 4000):
        """Короткое сообщение в строке состояния и след в логе.

        Нужна там, где действие законно ничего не делает: молчание
        неотличимо от сломавшейся кнопки.
        """
        logger.info("Notify: %s", message)

        bar = self.statusBar()
        if bar is not None:
            bar.showMessage(message, timeout)

    def mark_dirty(self, dirty: bool = True):
        self._dirty = bool(dirty)
        self._update_title()

    def _mark_saved(self):
        """Считать текущее состояние сохранённым."""
        self._dirty = False
        self.undo_stack.setClean()
        self._update_title()

    def _on_clean_changed(self, clean: bool):
        self._update_title()

    def _update_title(self):
        name = (
            os.path.basename(self.project_path)
            if self.project_path
            else i18n.t('untitled')
        )

        # [*] — место для звёздочки «изменено» (setWindowModified)
        # Версия в заголовке: сразу видно, какая сборка запущена
        self.setWindowTitle(
            "%s — %s %s[*]" % (name, i18n.t('app_title'), APP_VERSION)
        )
        self.setWindowModified(self.is_dirty())

    def set_warn_unsaved(self, enabled: bool):
        """Включить/выключить вопрос о несохранённых изменениях.

        Настройка запоминается между запусками и действует во всех
        случаях: закрытие прило��ени��, новый коллаж, открытие
        проекта (в том числе из списка недавних) и автоподбор
        размера холста.
        """
        self.warn_unsaved = bool(enabled)
        settings.set_confirm_unsaved(self.warn_unsaved)

        # Галочка меню и состояние должны совпадать даже при
        # программном вызове метода
        action = getattr(self, "warn_unsaved_action", None)
        if action is not None and action.isChecked() != self.warn_unsaved:
            action.setChecked(self.warn_unsaved)

        self.notify(i18n.t(
            'warn_unsaved_on' if self.warn_unsaved else 'warn_unsaved_off'
        ))

    def _confirm_discard(self) -> bool:
        """Спросить про несохранённые изменения.

        True — можно продолжать (сохранили, отказались или терять
        нечего), False — пользователь отменил операцию.
        """
        if not self.is_dirty():
            return True

        # Вопрос можно выключить в настройках: тогда закрытие
        # холста, открытие проекта и выход из приложения проходят
        # молча. След остаётся в строке состояния и в логе —
        # чтобы потерянная работа не выглядела как баг.
        if not self.warn_unsaved:
            logger.info("Unsaved changes discarded: the warning is disabled")
            self.notify(i18n.t('unsaved_discarded'))
            return True

        box = QMessageBox(self)
        box.setWindowTitle(i18n.t('confirm_save_title'))
        box.setText(i18n.t('confirm_save_text'))
        box.setIcon(QMessageBox.Warning)

        save = box.addButton(i18n.t('save'), QMessageBox.AcceptRole)
        discard = box.addButton(
            i18n.t('discard'), QMessageBox.DestructiveRole
        )
        box.addButton(i18n.t('cancel'), QMessageBox.RejectRole)
        box.setDefaultButton(save)
        box.exec()

        clicked = box.clickedButton()

        if clicked is save:
            # ��тмена в диалоге выбора файла = отмена всей операции
            return self.save_project()

        # Esc закрывает окно как «Отмена»
        return clicked is discard

    def closeEvent(self, event):
        """ФИКС: раньше окно закрывалось молча и работа терялась."""
        if not self._confirm_discard():
            event.ignore()
            return

        # Фоновые потоки не должны пережить окно: иначе при выходе
        # возможен крах на сигнале от уже удалённого виджета
        self._preloader.cancel()
        self._image_loader.cancel_all()
        self._image_loader.wait_for_done()
        self._preloader.wait_for_done()
        self.preview_panel.cancel_pending_loads()

        self._save_settings()
        super().closeEvent(event)

    # ---------- Оформление холста ----------

    def change_layout_style(self):
        """Отступы между слотами, скругление углов и фон холста."""
        old_style = self.scene.layout_style()

        dialog = LayoutStyleDialog(old_style, self)
        if not dialog.exec():
            return

        new_style = dialog.get_style()
        if new_style == old_style:
            return

        self.undo_stack.push(
            ChangeLayoutStyleCommand(self.scene, old_style, new_style)
        )

        # Запоминаем как значения по умолчанию для новых коллажей
        settings.set_layout_style(new_style)

    def changeEvent(self, event):
        # ФИКС: при переключении на другое окно (Alt+Tab) событие
        # отпускания клавиши уходит другому приложению,
        # поэтому режимы Z/C сбрасываем явно
        if (
            event.type() == QEvent.ActivationChange
            and not self.isActiveWindow()
        ):
            view = getattr(self, "view", None)
            if view is not None:
                view.reset_input_modes()

        super().changeEvent(event)

    def set_scene(self, scene):
        """Переключить активну�� сцену и освободить старую.

        ФИКС (утечка памяти): раньше окно просто присваивало новую
        сцену, а старая оставалась в памяти вместе со всеми
        загруженными пиксмапами: после нескольких «Новых коллажей»
        или открытых проектов память не возвращалась.

        Вызывать только после undo_stack.clear(): команды истории
        ссылаются на элементы старой сцены.
        """
        old_scene = getattr(self, "scene", None)

        self.scene = scene
        self.view.setScene(scene)

        # Вид заново подписывается на размер холста и
        # пересчитывает свободное поле вокруг него
        self.view.attach_scene(scene)

        if old_scene is not None and old_scene is not scene:
            old_scene.clear()
            old_scene.deleteLater()

        return scene

    # ---------- Helpers ----------

    def open_github(self):
        """Открывает страницу GitHub в браузере."""
        url = "https://github.com/re-quies/fastcollageforwin"  # Замените на ссылку вашего репозитория
        if not url:
            logger.warning("GitHub URL is not configured; nothing to open")
            return
        webbrowser.open(url)

    def _selected_item(self):
        items = self.scene.selectedItems()
        return items[0] if items else None

    # ---------- Поворот изо��ражения ----------

    def rotate_selected(self, delta_degrees):
        """Повернуть выбранное изображение на фиксированный угол."""
        item = self._selected_item()
        if not isinstance(item, ImageItem):
            self.notify(i18n.t('nothing_selected'))
            return

        # В слоте крути��ся содержимое (с пересчётом cover-масштаба),
        # свободный элемент по��орачивается целиком
        if item.rotate_in_slot(delta_degrees):
            return

        self.undo_stack.push(
            TransformCommand(
                item,
                item.pos(),
                item.scale(),
                item.rotation(),
                item.pos(),
                item.scale(),
                item.rotation() + float(delta_degrees),
            )
        )

    def reset_rotation(self):
        """Сбросить поворот выбранного изображения."""
        item = self._selected_item()
        if not isinstance(item, ImageItem):
            self.notify(i18n.t('nothing_selected'))
            return

        if item.reset_slot_rotation() or abs(item.rotation()) < 1e-6:
            return

        self.undo_stack.push(
            TransformCommand(
                item,
                item.pos(),
                item.scale(),
                item.rotation(),
                item.pos(),
                item.scale(),
                0.0,
            )
        )

    # ---------- Actions ----------
    def add_image(self):
        # Можно выбрать сразу несколько файлов: раньше каждое фото
        # приходилось добавлять отдельным вызовом диалога
        files, _ = QFileDialog.getOpenFileNames(
            self,
            i18n.t('choose_image'),
            settings.last_dir('image'),
            file_types.images_filter()
        )

        if not files:
            return

        settings.set_last_dir('image', os.path.dirname(files[0]))

        # Файлы декодируются в рабочих потоках (core.async_loader):
        # окно остаётся отзывчивым, а картинки появляются на холсте
        # по мере готовности
        self._load_images_to_canvas(files)

    def _on_canvas_image_loaded(self, token, path, image):
        """Фоновая загрузка завершена — кладём изображение на холст."""
        if not isinstance(token, dict):
            return

        # Пока файл читался, пользователь мог начать новый коллаж
        # или открыть другой проект — в чужую сцену ничего не кладём
        scene = token.get("scene")
        if scene is not self.scene:
            return

        if image is None or image.isNull():
            logger.warning("Failed to load image from %s", path)
            return

        # ФИКС (память): см. core.image_cache — на холсте живёт
        # рабочая копия, а не полноразмерное фото
        item = ImageItem(QPixmap.fromImage(image), path)

        # Несколько файлов кладём «лесенкой», иначе они лягут стопкой
        # ровно друг на друге и будут выглядеть как одна картинка
        offset = 30 * int(token.get("index", 0) or 0)
        item.setPos(offset, offset)
        item.setSelected(True)

        # Устанавливаем задержку swap для нового элемента
        delay = getattr(scene, 'swap_delay_ms', None)
        if delay is not None and hasattr(item, '_hover_timer'):
            item._hover_timer.setInterval(int(delay))

        self.undo_stack.push(AddItemCommand(scene, item))

    def _load_images_to_canvas(self, paths):
        """Прочитать файлы в фоне и разложить их по холсту «лесенкой».

        Общий код для меню «Добавить фото» и вставки файлов из
        буфера обмена: раньше цикл жил только внутри add_image.
        """
        for index, file_path in enumerate(paths):
            self._image_loader.submit(
                file_path,
                token={"scene": self.scene, "index": index},
            )

    def _place_new_item(self, item, center=True):
        """Положить новый ImageItem на холст через undo-стек."""
        if center:
            # Вставляем в центр видимой области, а не в угол сцены:
            # при зуме и прокрутке угол бывает далеко за экраном
            view_center = self.view.mapToScene(
                self.view.viewport().rect().center()
            )
            rect = item.boundingRect()
            item.setPos(
                view_center.x() - rect.width() / 2,
                view_center.y() - rect.height() / 2,
            )

        # Задержка обмена такая же, как у остальных элементов сцены
        delay = getattr(self.scene, 'swap_delay_ms', None)
        if delay is not None and hasattr(item, '_hover_timer'):
            item._hover_timer.setInterval(int(delay))

        item.setSelected(True)
        self.undo_stack.push(AddItemCommand(self.scene, item))

    def _selected_image_item(self):
        """Выбранное фото: сам ImageItem или содержимое слота."""
        selected = self.scene.selectedItems()
        for item in selected:
            if isinstance(item, ImageItem):
                return item

        # В режиме шаблона выделяется слот, а фото лежит внутри него
        for item in selected:
            inner = getattr(item, "image_item", None)
            if isinstance(inner, ImageItem):
                return inner
        return None

    def copy_selection(self):
        """Ctrl+C: положить выбранное фото в буфер обмена."""
        item = self._selected_image_item()
        if item is None:
            self.notify(i18n.t('nothing_selected'))
            return

        mime = QMimeData()

        # Кладём и картинку, и путь к файлу: картинку поймёт любой
        # редактор, а путь позволяет вставить фото обратно в проект
        # без потери ссылки на исходник
        pixmap = getattr(item, "original_pixmap", None)
        if pixmap is not None and not pixmap.isNull():
            mime.setImageData(pixmap.toImage())

        path = getattr(item, "source_path", None)
        if path and os.path.exists(path):
            mime.setUrls([QUrl.fromLocalFile(path)])

        QGuiApplication.clipboard().setMimeData(mime)
        self.notify(i18n.t('copy_done'))

    def paste_from_clipboard(self):
        """Ctrl+V: вставить изображение из буфера обмена на холст."""
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()

        # Сначала пробуем файлы: у них есть путь, поэтому проект
        # сохранится со ссылкой, а не с потерянной картинкой
        paths = []
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                local = url.toLocalFile()
                if local and file_types.is_image_path(local):
                    paths.append(local)

        if paths:
            self._load_images_to_canvas(paths)
            return

        image = clipboard.image()
        if image is None or image.isNull():
            self.notify(i18n.t('paste_empty'))
            return

        # Скриншот из буфера бывает огромным: приводим его к тому же
        # рабочему размеру, что и файлы (см. core.image_cache)
        max_side = image_cache.MAX_WORKING_SIDE
        if max(image.width(), image.height()) > max_side:
            image = image.scaled(
                max_side,
                max_side,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )

        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self.notify(i18n.t('paste_failed'))
            return

        self._place_new_item(ImageItem(pixmap, None))

    def duplicate_selection(self):
        """Ctrl+D: копия выбранного фото со сдвигом."""
        item = self._selected_image_item()
        if item is None:
            self.notify(i18n.t('nothing_selected'))
            return

        clone = ImageItem(item.original_pixmap, item.source_path)

        # Копия повторяет кадрирование, зеркала, поворот и масштаб:
        # иначе «дублировать» давало бы просто исходное фото
        try:
            clone.apply_view_state(item.view_state())
        except Exception:
            logger.exception("Duplicate: failed to copy the view state")

        clone.setRotation(item.rotation())
        clone.setScale(item.scale())
        clone.setPos(item.pos() + QPointF(40, 40))
        self._place_new_item(clone, center=False)

    def _apply_layout_direction(self):
        """Арабский интерфейс раскладывается справа налево."""
        QGuiApplication.setLayoutDirection(
            Qt.RightToLeft if i18n.is_rtl() else Qt.LeftToRight
        )

        # Сам холст всегда остаётся слева направо: зеркалить
        # координаты сцены нельзя — иначе перетаскивание фото и
        # кнопки прокрутки начнут работать в обратную сторону
        view = getattr(self, "view", None)
        if view is not None:
            view.setLayoutDirection(Qt.LeftToRight)

    def _preload_project_images(self, paths, on_done):
        """Прочитать изображения проекта в фоне, показывая прогресс.

        ФИКС: раньше здесь работал вложенный QEventLoop. Он держал
        стек вызовов открытым и пропускал в приложение любые
        события — включая закрытие окна и повторное открытие
        проекта, — то есть сборка сцены могла продолжиться поверх
        уже разрушенной. Теперь ожидание асинхронное: продолжение
        вызывается колбеком on_done(cancelled) из обычного цикла
        событий Qt.
        """
        self._preloader.start(paths, on_done)

    def _layer_target(self):
        """Элемент, к которому применяются команды слоёв.

        Если выбран слот шаблона ИЛИ изображение внутри слота —
        слоями управляем на уровне слота (изображение — его часть
        и поднимается/опускае��ся вместе со слотом).
        """
        item = self._selected_item()
        if item is None:
            return None

        if isinstance(item, TemplateSlotItem):
            return item

        parent = item.parentItem()
        if isinstance(parent, TemplateSlotItem):
            return parent

        return item

    def _change_layer(self, target, old_z, new_z):
        """Сменить слой чере�� undo-стек.

        ФИКС: раньше setZValue/set_base_z вызывались напрямую:
        Ctrl+Z не возвращал прежний порядок, а окно не считало
        проект изменённым.
        """
        if abs(float(old_z) - float(new_z)) < 1e-6:
            return

        self.undo_stack.push(ChangeLayerCommand(target, old_z, new_z))

    def bring_to_front(self):
        item = self._layer_target()
        if not item:
            self.notify(i18n.t('nothing_selected'))
            return

        # Слоты сравниваем только между собой (по базовому слою,
        # без учёта временной подсветки при перетаскивании)
        if isinstance(item, TemplateSlotItem):
            slots = getattr(self.scene, "template_slots", [])
            max_z = max((s.base_z() for s in slots), default=0.0)
            self._change_layer(item, item.base_z(), max_z + 1)
            return

        max_z = max(
            (i.zValue() for i in self.scene.items()
             if isinstance(i, ImageItem)),
            default=0,
        )
        self._change_layer(item, item.zValue(), max_z + 1)

    def send_to_back(self):
        item = self._layer_target()
        if not item:
            self.notify(i18n.t('nothing_selected'))
            return

        if isinstance(item, TemplateSlotItem):
            slots = getattr(self.scene, "template_slots", [])
            min_z = min((s.base_z() for s in slots), default=0.0)
            self._change_layer(item, item.base_z(), min_z - 1)
            return

        min_z = min(
            (i.zValue() for i in self.scene.items()
             if isinstance(i, ImageItem)),
            default=0,
        )
        self._change_layer(item, item.zValue(), min_z - 1)

    def export_image(self):
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            i18n.t('export_image'),
            settings.last_dir('export'),
            file_types.export_filter()
        )

        if not file_path:
            logger.info("Export: cancelled in the file dialog")
            return

        # ФИКС (баг 5): если пользователь не указал расширение —
        # подставляем его из выбранного фильтра, иначе QImage.save
        # не сможет определить форм��т и молча откажет.
        # Сравнивать фильтр со словом "JPEG" было нельзя: после
        # перевода фильтров проверка молча давала бы .png
        file_path = file_types.ensure_extension(
            file_path,
            file_types.extension_for_filter(selected_filter),
            allowed=file_types.EXPORT_EXTENSIONS,
        )
        ext = os.path.splitext(file_path)[1].lower()

        settings.set_last_dir('export', os.path.dirname(file_path))

        rect = self.scene.sceneRect()
        is_jpeg = ext in (".jpg", ".jpeg")

        options = ExportOptionsDialog(
            rect.width(),
            rect.height(),
            is_jpeg,
            settings.export_scale(),
            settings.export_quality(),
            self,
        )

        if not options.exec():
            logger.info("Export: cancelled in the options dialog")
            return

        scale_percent = options.scale_percent()
        quality = options.quality()
        settings.set_export_scale(scale_percent)
        settings.set_export_quality(quality)

        width, height = options.target_size()

        logger.info(
            "Export: %dx%d at %d%% -> %s",
            width,
            height,
            scale_percent,
            file_path,
        )

        # У JPEG альфа-канала нет в принципе, а RGB888 занимает на
        # четверть меньше памяти: на большом холсте это решает,
        # состоится экспорт или нет
        image = QImage(
            width,
            height,
            QImage.Format_RGB888 if is_jpeg else QImage.Format_ARGB32,
        )

        # При нехватке памяти QImage молча получается пустым —
        # раньше это всплывало лишь отказом save() в самом конц��
        if image.isNull():
            logger.error(
                "Export: failed to allocate a %dx%d image", width, height
            )
            QMessageBox.critical(
                self, i18n.t('error'), i18n.t('export_no_memory')
            )
            return
        # Прозрачный фон умеет только PNG: у JPEG нет альф��-канала,
        # и без белой подложки фон стал бы чёрным
        transparent = (
            bool(getattr(self.scene, "transparent_background", False))
            and ext == ".png"
        )
        image.fill(Qt.transparent if transparent else Qt.white)

        # Скрыть в��е визуальные маркеры/выделения на время рендера
        prev_suppress = getattr(self.scene, 'suppress_visuals', False)
        slots = getattr(self.scene, 'template_slots', [])
        prev_highlights = [
            bool(getattr(s, '_highlighted', False)) for s in slots
        ]

        # ФИКС: сбой рендера (нехватка памяти, битый файл)
        # раньше оставлял сцену без ручек и подсветки до
        # перезапуска — восстановление перенесено в finally
        try:
            self.scene.suppress_visuals = True

            # Удаляем визуальные индикаторы hover у ImageItem'ов
            for it in list(self.scene.items()):
                if hasattr(it, '_clear_hover_indicator'):
                    try:
                        it._clear_hover_indicator()
                    except Exception:
                        logger.exception(
                            "Failed to clear hover indicator before export"
                        )

            for slot in slots:
                slot.set_highlight(False)
                slot._update_handles()

            self.scene.update()

            self._render_scene_to_image(image, rect)
        finally:
            # Восстановим состояние визуализации
            for slot, prev in zip(slots, prev_highlights):
                slot.set_highlight(prev)
                slot._update_handles()

            self.scene.suppress_visuals = prev_suppress
            self.scene.update()

            # ФИКС (память): полноразмерные оригиналы нужны только на
            # время рендера — освобождаем кеш в любом случае
            image_cache.clear_full_cache()

        if not image.save(file_path, None, quality if is_jpeg else -1):
            # ФИКС (баг 5): сообщаем об ошибке пользователю,
            # а не только в лог
            logger.error("Failed to save exported image to %s", file_path)
            QMessageBox.critical(
                self, i18n.t('error'), i18n.t('export_failed')
            )
            return

        logger.info("Export: saved %s", file_path)
        self.notify(
            i18n.t('export_done').format(width=width, height=height)
        )

    def _render_scene_to_image(self, image, scene_rect):
        """Отрисовать сцену в image горизонтальными полосами.

        ФИКС (память): раньше сцена рисовалась одним вызовом
        render(), и к концу кадра в кеше лежали полноразмерные
        оригиналы ВСЕХ фотографий разом. Теперь после каждой
        полосы кеш очищается, и пик памяти зависит от высоты
        полосы, а не от числа фотографий на холсте.
        """
        width = image.width()
        height = image.height()

        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            # Масштаб экспорта: пикселей картинки на единицу сцены
            scale_y = (
                height / scene_rect.height() if scene_rect.height() else 1.0
            )

            y = 0
            while y < height:
                band = min(EXPORT_TILE_HEIGHT, height - y)

                target = QRectF(0, y, width, band)
                source = QRectF(
                    scene_rect.x(),
                    scene_rect.y() + y / scale_y,
                    scene_rect.width(),
                    band / scale_y,
                )

                self.scene.render(
                    painter, target, source, Qt.IgnoreAspectRatio
                )

                # Оригиналы, прочитанные для этой полосы, больше не нужны
                image_cache.clear_full_cache()

                y += band
        finally:
            painter.end()

    def change_canvas_size(self):
        dialog = CanvasSizeDialog(
            self.scene.canvas_width,
            self.scene.canvas_height,
            self
        )

        if dialog.exec():
            width, height = dialog.get_size()
            old_size = (self.scene.canvas_width, self.scene.canvas_height)

            if (int(width), int(height)) == (int(old_size[0]), int(old_size[1])):
                return

            # ФИКС: смена размера холста теперь отменяется через Ctrl+Z
            self.undo_stack.push(
                ResizeCanvasCommand(self.scene, old_size, (width, height))
            )

    def horizontal_mirror(self):
        """Обработчик горизонтального зеркалирования"""
        self._mirror_selected('horizontal')

    def vertical_mirror(self):
        """Обработчик вертикального зеркалирования"""
        self._mirror_selected('vertical')

    def _mirror_selected(self, axis: str):
        """Зеркалирование выделенных изображений через undo-стек.

        ФИКС: раньше mirror_image вызывался напрямую, поэтому
        жест не отменялся Ctrl+Z и не помечал проект изменённым.
        Заодно появилась подсказка, когда ничего не выбрано.
        """
        scene = self.get_active_scene()
        items = [
            it for it in (scene.selectedItems() if scene else [])
            if isinstance(it, ImageItem)
        ]

        if not items:
            self.notify(i18n.t('nothing_selected'))
            return

        self.undo_stack.push(MirrorCommand(items, axis))

    def get_active_scene(self):
        """Получаем активную сцену"""
        return self.scene

    def delete_selected(self):
        # Пункт 4: удаление через undo-стек + очистка ссыло�� слотов.
        # Слоты шаблона клавишей Delete не удаляются — только изображения
        items = [
            it for it in self.scene.selectedItems()
            if not isinstance(it, TemplateSlotItem)
        ]
        if not items:
            self.notify(i18n.t('nothing_selected'))
            return

        self.undo_stack.push(DeleteItemsCommand(self.scene, items))

    def change_swap_delay(self):
        value, ok = QInputDialog.getInt(
            self,
            i18n.t('swap_delay'),
            i18n.t('swap_delay_hint'),
            self.swap_delay_ms,
            0,      # было 100 — теперь задержку можно выключить насовсем
            5000,
            100,
        )

        if ok:
            self.swap_delay_ms = value
            self.scene.swap_delay_ms = value

            # Значение переживает перезапуск и попадает в проект
            settings.set_swap_delay_ms(value)
            self.mark_dirty()

            # ��бновляем существующие ImageItem'ы
            for it in self.scene.items():
                if hasattr(it, '_hover_timer'):
                    it._hover_timer.setInterval(value)

    def set_language(self, lang: str):
        i18n.set_lang(lang)
        # Выбранный язык переживает перезапуск (core/settings.py)
        settings.set_language(lang)

        # Арабский интерфейс раскладывается справа налево
        self._apply_layout_direction()

        # Пересобираем меню и обновляем тексты
        self._update_title()
        self._create_menu()
        # Обновляем заголовок панели превью и подсказку кнопки сетки
        self.preview_panel.setWindowTitle(i18n.t('images'))
        self.regen_grid_btn.setToolTip(i18n.t('new_grid_tooltip'))
        self.auto_canvas_btn.setToolTip(i18n.t('auto_canvas_tooltip'))
        self.auto_canvas_btn.setText(i18n.t('auto_canvas_action'))
        self.retranslate_pan_buttons()

        # Индикатор режима перерисовывается принудительно: сам
        # режим не менялся, а подпись на новом языке уже другая
        self._current_mode = None
        self.update_mode_indicator(self.view.current_input_mode())

    # ---------- Кнопки перемещения по холсту ----------

    # Порядок кнопок в строке состояния: ← ↑ ↓ →
    PAN_BUTTONS = (
        ("left", QStyle.SP_ArrowLeft, 'pan_left'),
        ("up", QStyle.SP_ArrowUp, 'pan_up'),
        ("down", QStyle.SP_ArrowDown, 'pan_down'),
        ("right", QStyle.SP_ArrowRight, 'pan_right'),
    )

    def _create_pan_buttons(self):
        """Четыре стрелки в строке состояния для сдвига по холсту."""
        self.pan_buttons = {}

        self.pan_widget = QWidget(self)
        layout = QHBoxLayout(self.pan_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        style = self.style()

        for name, icon_id, tip_key in self.PAN_BUTTONS:
            button = QToolButton(self)
            button.setIcon(style.standardIcon(icon_id))
            button.setIconSize(QSize(14, 14))
            button.setFixedSize(24, 24)
            button.setToolTip(i18n.t(tip_key))

            # Кнопка не забирает фокус: иначе после клика перестают
            # ра��отать клавиатурные режимы холста (Z, C, R, X)
            button.setFocusPolicy(Qt.NoFocus)

            # Удержание кнопки прокручивает холст непрерывно
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(300)
            button.setAutoRepeatInterval(60)

            button.clicked.connect(getattr(self.view, "pan_%s" % name))

            layout.addWidget(button)
            self.pan_buttons[name] = button

        self.statusBar().addPermanentWidget(self.pan_widget)

        # Стрелка гаснет, когда в эту сторону двигаться уже некуда
        for bar in (
            self.view.horizontalScrollBar(),
            self.view.verticalScrollBar(),
        ):
            bar.valueChanged.connect(self.update_pan_buttons)
            bar.rangeChanged.connect(self.update_pan_buttons)

        self.update_pan_buttons()

    def update_pan_buttons(self, *_):
        """Обновить активность стрелок по текущему положению вида."""
        buttons = getattr(self, "pan_buttons", None)
        if not buttons:
            return

        available = self.view.pan_availability()

        for name, button in buttons.items():
            button.setEnabled(bool(available.get(name)))

    def retranslate_pan_buttons(self):
        """Обновить подсказки стрелок при смене языка."""
        buttons = getattr(self, "pan_buttons", None)
        if not buttons:
            return

        for name, _icon_id, tip_key in self.PAN_BUTTONS:
            button = buttons.get(name)
            if button is not None:
                button.setToolTip(i18n.t(tip_key))

    # ---------- Индикатор активного режима ----------

    MODE_LABELS = (
        ("normal", 'mode_normal'),
        ("canvas_pan", 'mode_canvas_pan'),
        ("content_zoom", 'mode_content_zoom'),
        ("slot_pan", 'mode_slot_pan'),
        ("slot_rotate", 'mode_slot_rotate'),
    )

    def update_mode_indicator(self, mode: str = "normal"):
        """Показать активный режим ввода в строке состояния."""
        label = getattr(self, "mode_label", None)
        if label is None or mode == self._current_mode:
            return

        self._current_mode = mode

        titles = dict(self.MODE_LABELS)
        active = mode != "normal"

        label.setText(
            ("● " if active else "○ ")
            + i18n.t(titles.get(mode, 'mode_normal'))
        )
        label.setToolTip(i18n.t('mode_hint'))

        # Выключенный режим не должен спорить за внимание
        # с сообщениями в строке состояния
        label.setStyleSheet(
            "color: #c05621; font-weight: bold;" if active
            else "color: gray;"
        )

        self._update_mode_label_width()

    def _update_mode_label_width(self):
        """Зафиксировать ширину по самому длинному названию.

        Иначе при каждом нажатии Z строка состояния дёргала бы
        все кнопки вправо — масштаб, стрелки и «Холст под фото».
        """
        label = getattr(self, "mode_label", None)
        if label is None:
            return

        metrics = label.fontMetrics()
        width = max(
            metrics.horizontalAdvance("● " + i18n.t(key))
            for _mode, key in self.MODE_LABELS
        )

        # Запас на жирное начертание активного режима
        label.setMinimumWidth(width + 16)

    def show_shortcuts(self):
        """Настройки → Горячие клавиши (или F1)."""
        # Режимы сбрасываются: диалог забирает фокус, и
        # отпускание клавиши до холста уже не дойдёт
        self.view.reset_input_modes()

        dialog = ShortcutsDialog(self)
        dialog.exec()

    def update_zoom_label(self, percent):
        # percent может быть float (масштаб 1.0) или int (проценты)
        if isinstance(percent, float):
            value = int(round(percent * 100))
        else:
            value = int(round(percent))

        self.zoom_label.setText(f"{value}%")

    def set_exact_zoom(self):
        value, ok = QInputDialog.getInt(
            self,
            i18n.t('zoom'),
            i18n.t('enter_zoom'),
            self.view.zoom_percent,
            10,
            800,
            10
        )

        if ok:
            self.view.set_zoom_percent(value)

    # ---------- Project save / load ----------

    def save_project(self) -> bool:
        """Ctrl+S: тихо перезаписать текущий файл проекта.

        Раньше каждое сохранение снова спрашивал�� имя файла.
        Возвращает True, если проект действительно записан —
        это важно для диалога «сохранить изменения?».
        """
        if self.project_path:
            return self._write_project(self.project_path)

        return self.save_project_as()

    def save_project_as(self) -> bool:
        """Ctrl+Shift+S: спросить имя файла и сохранить."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            i18n.t('save_project_as'),
            settings.last_dir('project'),
            file_types.project_save_filter()
        )
        if not file_path:
            return False

        # Без расширения файл потом не найдётся в диалоге открытия
        file_path = file_types.ensure_extension(
            file_path,
            file_types.PROJECT_EXTENSION,
            allowed=file_types.PROJECT_EXTENSIONS + file_types.JSON_EXTENSIONS,
        )

        return self._write_project(file_path)

    def _write_project(self, file_path: str) -> bool:
        # Пути к фото пишем и относительно файла проекта: папку
        # с проектом и фотографиями теперь можно переносить
        data, skipped = project_io.serialize(self, file_path)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.exception("Failed to save project to %s", file_path)
            QMessageBox.critical(
                self, i18n.t('error'), i18n.t('project_save_failed')
            )
            return False

        self.project_path = file_path
        settings.set_last_dir('project', os.path.dirname(file_path))
        settings.add_recent_project(file_path)
        self._refresh_recent_menu()

        # С этого момента терять нечего: сбрасываем признак правок
        self._mark_saved()

        if skipped:
            QMessageBox.warning(
                self, i18n.t('app_title'), i18n.t('unsaved_images')
            )

        return True

    def open_project(self):
        # Несохранённая работа не должна пропадать при открытии другого
        if not self._confirm_discard():
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            i18n.t('open_project'),
            settings.last_dir('project'),
            file_types.project_open_filter()
        )
        if not file_path:
            return

        self.open_project_path(file_path, confirmed=True)

    def open_project_path(self, file_path: str, confirmed: bool = False):
        """Открыть конкретный файл (также из «Последних проектов»)."""
        if not confirmed and not self._confirm_discard():
            return

        # Пункт списка мог устареть: файл удалили или переместили
        if not os.path.exists(file_path):
            settings.remove_recent_project(file_path)
            self._refresh_recent_menu()
            QMessageBox.warning(
                self,
                i18n.t('app_title'),
                i18n.t('recent_missing') + "\n" + file_path,
            )
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            logger.exception("Failed to load project from %s", file_path)
            QMessageBox.critical(
                self, i18n.t('error'), i18n.t('project_load_failed')
            )
            return

        # Незавершённые фоновые загрузки относятся к прежней сцене
        self._image_loader.cancel_all()

        # ФИКС: сначала пробуем путь относительно файла проекта,
        # потом абсолютный: так папка «проект + фотографии»
        # переживает перемещение, копирование и другую букву диска
        missing_files = project_io.resolve_paths(data, file_path)

        # А если файлы всё же потерялись — предложим поиск в папке
        if missing_files and not self._relink_missing(
            data, file_path, missing_files
        ):
            logger.info("Open project: cancelled at the relink prompt")
            return

        # Файлы читаем заранее и в несколько потоков: на тяжёлом
        # проекте окно больше не выглядит зависшим
        self._preload_project_images(
            project_io.image_paths(data),
            lambda cancelled: self._finish_open_project(
                cancelled, file_path, data
            ),
        )

    def _relink_missing(self, data, project_path, missing):
        """Предложить поиск потерянных файлов в другой папке.

        Возвращает False, если пользователь отменил открытие.
        """
        answer = QMessageBox.question(
            self,
            i18n.t('app_title'),
            i18n.tn('relink_prompt', len(missing)).format(count=len(missing))
            + "\n\n"
            + "\n".join(missing[:5]),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )

        if answer == QMessageBox.Cancel:
            return False

        if answer != QMessageBox.Yes:
            # Открываем как есть: слоты потерянных фото останутся пустыми
            return True

        folder = QFileDialog.getExistingDirectory(
            self,
            i18n.t('relink_folder'),
            os.path.dirname(project_path) or settings.last_dir('image'),
        )

        if not folder:
            return True

        fixed, still_missing = project_io.relink(data, folder)
        logger.info(
            "Relink: %d restored, %d still missing", fixed, len(still_missing)
        )

        if fixed:
            self.notify(i18n.tn('relink_done', fixed).format(count=fixed))
        else:
            QMessageBox.information(
                self, i18n.t('app_title'), i18n.t('relink_none')
            )

        return True

    def _finish_open_project(self, cancelled, file_path, data):
        """Собрать сцену проекта после предзагрузки файлов."""
        if cancelled:
            logger.info("Open project: image loading cancelled by the user")
            image_cache.clear_working_cache()
            return

        try:
            missing = project_io.apply(self, data)
        except ValueError:
            logger.exception("Invalid project file %s", file_path)
            QMessageBox.critical(
                self, i18n.t('error'), i18n.t('project_load_failed')
            )
            return
        finally:
            # Предзагруженные копии нужны только на время сборки
            # сцены: дальше пиксмапы живут в самих элементах
            image_cache.clear_working_cache()

        self.project_path = file_path
        settings.set_last_dir('project', os.path.dirname(file_path))
        settings.add_recent_project(file_path)
        self._refresh_recent_menu()

        # Только что загруженный проект совпадает с файлом на диске
        self._mark_saved()

        if missing:
            QMessageBox.warning(
                self,
                i18n.t('app_title'),
                i18n.t('missing_images') + "\n" + "\n".join(missing[:10]),
            )

    def create_new_collage(self):
        # Новый коллаж затирает текущую сцену — сначала спрашиваем
        if not self._confirm_discard():
            return

        dialog = StartCollageDialog(self)
        if not dialog.exec():
            return

        self.create_new_collage_from_data(dialog.result_data())

    def create_new_collage_from_data(self, data):
        self.collage_mode = data["mode"]

        # Картинки, которые ещё читаются, относились к прежней сцене
        self._image_loader.cancel_all()

        # Пункт 2: очищаем историю undo — старые команды ссылаются на
        # элементы прежней сцены, и Ctrl+Z после нового коллажа мог бы
        # менять уже несуществующую сцену.
        self.undo_stack.clear()

        # --- TEMPLATE MODE ---
        if self.collage_mode == CollageMode.TEMPLATE:
            w, h = data["canvas_size"]

            scene = CanvasScene(w, h)
            scene.swap_delay_ms = self.swap_delay_ms
            scene.is_template_mode = True
            scene.template_image_count = data["count"]
            scene.build_template()

        # --- FREE MODE ---
        else:
            # В свободном режиме также используем выбранный размер холста
            w, h = data.get("canvas_size", (1920, 1080))
            scene = CanvasScene(w, h)
            scene.swap_delay_ms = self.swap_delay_ms

        # Оформление (отступы/скругления/фон) — последнее выбранное
        scene.apply_layout_style(settings.layout_style())

        # ВАЖНО: setScene ОДИН раз; set_scene ещё и освобождает старую сцену
        self.set_scene(scene)

        # Новый коллаж ещё ни разу не сохранялся
        self.project_path = None
        self._mark_saved()

    def regenerate_template_grid(self):
        """Обработчик кнопки: регенерация сетки только в TEMPLATE режиме."""
        if self.collage_mode != CollageMode.TEMPLATE:
            self.notify(i18n.t('not_template_mode'))
            return

        scene = self.get_active_scene()
        if not getattr(scene, 'is_template_mode', False):
            self.notify(i18n.t('not_template_mode'))
            return

        # Есть ли изображения на холсте или в слотах
        images_present = any(
            hasattr(it, 'original_pixmap') for it in scene.items()
        ) or any(
            getattr(slot, 'image_item', None) is not None
            for slot in getattr(scene, 'template_slots', [])
        )

        # Если есть изо��раж��ния — попросим подтверждение перед удалением
        if images_present:
            msg = QMessageBox(self)
            msg.setWindowTitle(i18n.t('confirm_regen_title'))
            msg.setText(i18n.t('confirm_regen_text'))
            msg.setIcon(QMessageBox.Warning)
            yes = msg.addButton(i18n.t('confirm'), QMessageBox.AcceptRole)
            msg.addButton(i18n.t('cancel'), QMessageBox.RejectRole)
            msg.exec()
            if msg.clickedButton() is not yes:
                return

        # Перестроим шаблон (CanvasScene.build_template отвечает за очистку)
        scene.build_template()

        # История undo може�� ссылаться на удалённые слоты/изображения (пункт 2)
        self.undo_stack.clear()

        # Сетка пересобрана мимо undo — это несохранённое изменение
        self.mark_dirty()

    # ---------- Подбор холста под фотографии ----------

    def auto_fit_canvas(self):
        """Обработчик кнопки и пункта меню.

        Всё тело завёрнуто в try/except осознанно: оконная сборка
        идёт с --noconsole, Qt печатает исключение из обработчика
        сигнала в stderr и продолжает работу — без этого любой сбой
        выглядит как «кнопка не работает».
        """
        logger.info("Auto canvas: requested")

        # Видимый признак того, что нажатие дошло до обработчика:
        # если сообщения нет, запущена старая сборка без этой функции
        self.statusBar().showMessage(i18n.t('auto_canvas_action'), 2000)

        try:
            self._auto_fit_canvas()
        except Exception:
            logger.exception("Auto canvas failed")

            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle(i18n.t('error'))
            box.setText(i18n.t('auto_canvas_error'))
            box.setDetailedText(traceback.format_exc())
            box.exec()

    def _auto_fit_canvas(self):
        """Расчёт холста под фотографии из панели.

        Размер холста и сетка ячеек считаются по реальным пропорциям
        фотографий (core.auto_layout), поэтому кадры встают в ячейки
        почти без обрезки.
        """
        entries = self.preview_panel.image_entries()
        if not entries:
            QMessageBox.information(
                self, i18n.t('app_title'), i18n.t('auto_canvas_empty')
            )
            return

        # Файлы, размер которых прочитать не удалось, в расчёте
        # не участвуют — о них предупредим в диалоге
        usable = [entry for entry in entries if entry.get("size")]
        skipped = len(entries) - len(usable)

        logger.info(
            "Auto canvas: %d preview items, %d with known size",
            len(entries),
            len(usable),
        )

        plan = None
        if usable:
            plan = auto_layout.plan_canvas(
                [entry["size"] for entry in usable]
            )

        if plan is None:
            QMessageBox.warning(
                self, i18n.t('error'), i18n.t('auto_canvas_failed')
            )
            return

        # Новый холст заменяет текущий коллаж — как «Новый коллаж»
        if not self._confirm_discard():
            return

        # Размеры нужны диалогу для второго режима подбора:
        # варианты сетки он считает сам, а первый режим остаётся как был
        dialog = AutoCanvasDialog(
            plan,
            skipped,
            self,
            sizes=[entry["size"] for entry in usable],
        )
        if not dialog.exec():
            logger.info("Auto canvas: cancelled in dialog")
            return

        self._build_auto_collage(dialog.result_plan(), usable)

    def _build_auto_collage(self, plan, entries):
        """Собрать холст по рассчитанному плану и разложить фото."""
        paths = [entry["path"] for entry in entries if entry["path"]]

        logger.info(
            "Auto canvas: canvas %dx%d, %d cells, %d file(s)",
            plan.width,
            plan.height,
            plan.cell_count,
            len(paths),
        )

        # Самое долгое — чтение файлов — делаем заранее и с прогрессом
        self._preload_project_images(
            paths,
            lambda cancelled: self._finish_auto_collage(
                cancelled, plan, entries
            ),
        )

    def _finish_auto_collage(self, cancelled, plan, entries):
        """Собрать сцену после того, как файлы прочитаны."""
        if cancelled:
            logger.info("Auto canvas: loading cancelled")
            image_cache.clear_working_cache()
            return

        # Картинки, которые ещё читаются, относились к прежней сцене
        self._image_loader.cancel_all()

        # История undo ссылается на элементы прежней сцены
        self.undo_stack.clear()

        scene = CanvasScene(plan.width, plan.height)
        scene.swap_delay_ms = self.swap_delay_ms
        scene.build_template_from_rects([
            QRectF(float(x), float(y), float(width), float(height))
            for _index, x, y, width, height in plan.cells
        ])

        # Оформление (отступы/��кругления/фон) — последнее выбранное
        scene.apply_layout_style(settings.layout_style())

        self.collage_mode = CollageMode.TEMPLATE
        self.set_scene(scene)

        placed = 0

        # Слоты созданы в том же порядке, что и ячейки плана,
        # а первое число ячейки — номер фотографии для неё
        for slot, cell in zip(scene.template_slots, plan.cells):
            entry = entries[cell[0]]

            pixmap = self._auto_pixmap(entry)
            if pixmap is None or pixmap.isNull():
                logger.warning(
                    "Auto canvas: unreadable image %s", entry["path"]
                )
                continue

            item = ImageItem(pixmap, entry["path"])
            if self.swap_delay_ms is not None:
                item._hover_timer.setInterval(int(self.swap_delay_ms))

            scene.addItem(item)
            slot.accept_image(item)

            # Фотография переехала из панели на холст
            self.preview_panel.remove_image(
                path=entry["path"], pixmap=entry["pixmap"]
            )
            placed += 1

        logger.info(
            "Auto canvas: placed %d of %d photo(s)", placed, len(plan.cells)
        )

        # Рабочие копии нужны были только на время сборки сцены
        image_cache.clear_working_cache()

        # Коллаж собран заново и ещё ни разу не сохранялся
        self.project_path = None
        self.mark_dirty()

        self.statusBar().showMessage(
            i18n.t('auto_canvas_done').format(
                width=plan.width, height=plan.height, count=placed
            ),
            5000,
        )

    @staticmethod
    def _auto_pixmap(entry):
        """Рабочая копия фотографии для холста."""
        path = entry.get("path")

        if path:
            pixmap = image_cache.load_working_pixmap(path)
            if not pixmap.isNull():
                return pixmap

        # Элементы без файла (вставка из буфера) живут только в памяти
        return entry.get("pixmap")
