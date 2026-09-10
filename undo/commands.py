import logging

from PySide6.QtGui import QUndoCommand
from PySide6.QtCore import QPointF, QRectF

import i18n
from canvas.slot_item import TemplateSlotItem

logger = logging.getLogger(__name__)


def _preview_panel(window=None, scene=None):
    """Панель превью главного окна (или None, если недоступна)."""
    if window is None and scene is not None and scene.views():
        window = scene.views()[0].window()

    if window is not None and hasattr(window, "preview_panel"):
        return window.preview_panel

    return None


class AddItemCommand(QUndoCommand):
    """
    Undo/Redo для добавления изображения на сцену.

    Если указан `slot` — изображение сразу помещается в слот шаблона
    (drag&drop в template режиме), а undo корректно очищает ссылку
    slot.image_item, чтобы не оставалась висячая ссылка.

    ФИКС: если слот был занят, раньше accept_image перезаписывал
    slot.image_item, а старое изображение оставалось дочерним элементом
    слота — невидимый "призрак", который нельзя было ни выбрать,
    ни вернуть. Теперь вытесненное изображение уходит в панель превью
    (как при обмене в MoveImageToSlotCommand), а undo возвращает его обратно
    в слот вместе с панорамированием.
    """

    def __init__(self, scene, item, slot=None, window=None):
        super().__init__(i18n.t('undo_add'))
        self.scene = scene
        self.item = item
        self.slot = slot
        self.window = window

        # Изображение, вытесненное из занятого слота (если было)
        self._displaced = None
        self._displaced_offset = None

    def _panel(self):
        return _preview_panel(self.window, self.scene)

    def _displace_slot_image(self):
        """Занятый слот: прежнее изображение возвращаем в панель превью."""
        if self.slot is None:
            return

        other = self.slot.image_item
        if other is None or other is self.item:
            return

        self._displaced = other
        self._displaced_offset = QPointF(
            getattr(other, "slot_offset", QPointF(0, 0))
        )

        panel = self._panel()
        if panel is not None:
            panel.add_pixmap(
                other.original_pixmap,
                getattr(other, "source_path", None),
            )
        else:
            logger.warning(
                "Preview panel is not available; "
                "displaced image is removed from scene"
            )

        self.slot.image_item = None
        other.setParentItem(None)
        if other.scene() is self.scene:
            self.scene.removeItem(other)

    def _restore_displaced(self):
        """Вернуть вытесненное изображение из панели обратно в слот."""
        other = self._displaced
        if other is None or self.slot is None:
            return

        panel = self._panel()
        if panel is not None:
            panel.remove_image(
                path=getattr(other, "source_path", None),
                pixmap=other.original_pixmap,
            )

        if other.scene() is not self.scene:
            self.scene.addItem(other)

        self.slot.accept_image(other)

        if self._displaced_offset is not None:
            other.slot_offset = QPointF(self._displaced_offset)
            self.slot.position_image(other)

        self._displaced = None
        self._displaced_offset = None

    def redo(self):
        self._displace_slot_image()

        if self.item.scene() is not self.scene:
            self.scene.addItem(self.item)
        if self.slot is not None:
            self.slot.accept_image(self.item)

    def undo(self):
        if self.slot is not None:
            if self.slot.image_item is self.item:
                self.slot.image_item = None
            self.item.setParentItem(None)
        if self.item.scene() is self.scene:
            self.scene.removeItem(self.item)

        self._restore_displaced()


class AddFromPreviewCommand(AddItemCommand):
    """
    Undo/Redo для drag&drop изображения ИЗ панели превью НА холст.

    ФИКС (основной баг): раньше добавление на холст и удаление из
    панели были ДВУМЯ независимыми действиями: в undo-стек попадала
    только AddItemCommand, а из панели элемент удалялся напрямую.
    В итоге Ctrl+Z убирал изображение с холста, но не возвращал его
    в панель — фотография терялась и там, и там.

    Теперь это одна атомарная команда:
    - redo: убрать из панели + добавить на холст;
    - undo: убрать с холста + вернуть в панель (как по клавише X).
    """

    def __init__(
        self,
        scene,
        item,
        slot=None,
        window=None,
        preview_path=None,
        preview_pixmap=None,
    ):
        super().__init__(scene, item, slot, window)

        self.preview_path = preview_path
        self.preview_pixmap = (
            preview_pixmap
            if preview_pixmap is not None
            else item.original_pixmap
        )

    def redo(self):
        panel = self._panel()
        if panel is not None:
            panel.remove_image(
                path=self.preview_path,
                pixmap=self.preview_pixmap,
            )

        super().redo()

    def undo(self):
        super().undo()

        panel = self._panel()
        if panel is not None:
            panel.add_pixmap(self.preview_pixmap, self.preview_path)
        else:
            logger.warning(
                "Preview panel is not available; "
                "image cannot be returned to it"
            )


class TransformCommand(QUndoCommand):
    """
    Undo/Redo для перемещения, масштабирования и поворота объекта
    """

    def __init__(
        self,
        item,
        old_pos,
        old_scale,
        old_rotation,
        new_pos,
        new_scale,
        new_rotation,
    ):
        super().__init__(i18n.t('undo_transform'))

        self.item = item

        self.old_pos = old_pos
        self.old_scale = old_scale
        self.old_rotation = old_rotation

        self.new_pos = new_pos
        self.new_scale = new_scale
        self.new_rotation = new_rotation

    def undo(self):
        self.item.setPos(self.old_pos)
        self.item.setScale(self.old_scale)
        self.item.setRotation(self.old_rotation)

    def redo(self):
        self.item.setPos(self.new_pos)
        self.item.setScale(self.new_scale)
        self.item.setRotation(self.new_rotation)


class DeleteItemsCommand(QUndoCommand):
    """
    Undo/Redo для удаления элементов с холста, в том числе из слотов
    шаблона (пункт 4). Очищает ссылку slot.image_item, чтобы не оставалась
    висячая ссылка, и умеет восстанавливать элемент обратно в слот.
    """

    def __init__(self, scene, items):
        super().__init__(i18n.t('delete'))
        self.scene = scene
        self._entries = []

        for item in items:
            parent = item.parentItem()
            slot = parent if isinstance(parent, TemplateSlotItem) else None
            self._entries.append({
                "item": item,
                "slot": slot,
                "pos": QPointF(item.pos()),
                "scale": item.scale(),
                "rotation": item.rotation(),
                # Панорамирование внутри слота восстанавливается
                # вместе с изображением
                "slot_offset": QPointF(
                    getattr(item, "slot_offset", QPointF(0, 0))
                ),
            })

    def redo(self):
        for entry in self._entries:
            item = entry["item"]
            slot = entry["slot"]

            if slot is not None and slot.image_item is item:
                slot.image_item = None

            if item.scene() is self.scene:
                self.scene.removeItem(item)

    def undo(self):
        for entry in self._entries:
            item = entry["item"]
            slot = entry["slot"]

            if item.scene() is not self.scene:
                self.scene.addItem(item)

            if slot is not None:
                # ФИКС: keep_offset — иначе Ctrl+Z возвращал фото в слот,
                # но панорамирование внутри слота сбрасывалось в центр
                slot.accept_image(item, keep_offset=True)

                offset = entry.get("slot_offset")
                if offset is not None:
                    item.slot_offset = QPointF(offset)
                    slot.position_image(item)
            else:
                item.setPos(entry["pos"])
                item.setScale(entry["scale"])
                item.setRotation(entry["rotation"])


class ReturnToPreviewCommand(QUndoCommand):
    """
    Undo/Redo для возврата изображения с холста в панель превью
    (клавиша X).

    ФИКС: раньше в превью добавляли напрямую, а удаляли с холста
    отдельной DeleteItemsCommand — после Ctrl+Z изображение возвращалось
    на холст, но его копия оставалась в превью. Теперь оба действия —
    одна атомарная команда: undo убирает элемент из превью и
    восстанавливает изображение на прежнее место (в слот шаблона с
    сохранением панорамирования или в свободную позицию).
    """

    def __init__(
        self,
        scene,
        window,
        item,
        restore_pos=None,
        restore_scale=None,
        restore_rotation=None,
    ):
        super().__init__(i18n.t('delete'))
        self.scene = scene
        self.window = window
        self.item = item

        parent = item.parentItem()
        self.slot = parent if isinstance(parent, TemplateSlotItem) else None

        # При возврате перетаскиванием важно восстановить положение ДО
        # начала жеста, а не ту точку, где кнопку отпустили
        # (она находится над панелью, то есть вне холста).
        self._pos = QPointF(restore_pos if restore_pos is not None else item.pos())
        self._scale = (
            restore_scale if restore_scale is not None else item.scale()
        )
        self._rotation = (
            restore_rotation
            if restore_rotation is not None
            else item.rotation()
        )
        self._slot_offset = QPointF(
            getattr(item, "slot_offset", QPointF(0, 0))
        )

    def _panel(self):
        if self.window is not None and hasattr(self.window, "preview_panel"):
            return self.window.preview_panel
        return None

    def redo(self):
        panel = self._panel()
        if panel is not None:
            panel.add_pixmap(
                self.item.original_pixmap,
                getattr(self.item, "source_path", None),
            )
        else:
            logger.warning(
                "Preview panel is not available; image is removed from scene"
            )

        if self.slot is not None and self.slot.image_item is self.item:
            self.slot.image_item = None

        if self.item.scene() is self.scene:
            self.scene.removeItem(self.item)

    def undo(self):
        panel = self._panel()
        if panel is not None and not panel.remove_image(
            path=getattr(self.item, "source_path", None),
            pixmap=self.item.original_pixmap,
        ):
            logger.warning("Preview copy of restored image was not found")

        if self.item.scene() is not self.scene:
            self.scene.addItem(self.item)

        if self.slot is not None:
            self.slot.accept_image(self.item)
            # Восстанавливаем панорамирование внутри слота
            self.item.slot_offset = QPointF(self._slot_offset)
            self.slot.position_image(self.item)
        else:
            self.item.setPos(self._pos)
            self.item.setScale(self._scale)
            self.item.setRotation(self._rotation)


class MoveImageToSlotCommand(QUndoCommand):
    """
    Undo/Redo для перемещения изображения в слот шаблона (пункт 4),
    включая обмен (swap) с изображением, которое уже находится в слоте.

    Сценарии:
    - item перемещается в пустой слот;
    - item вытесняет other: other уходит в старый слот item'а
      или (если item не был в слоте) возвращается в панель превью.
    """

    def __init__(self, scene, window, item, new_slot, old_slot, other):
        super().__init__(i18n.t('undo_transform'))
        self.scene = scene
        self.window = window
        self.item = item
        self.new_slot = new_slot
        self.old_slot = old_slot
        self.other = other

        # Состояние item до начала перетаскивания —
        # нужно, если item не был в слоте (свободный элемент)
        self._item_free_pos = QPointF(getattr(item, "_old_pos", item.pos()))
        self._item_free_scale = getattr(item, "_old_scale", item.scale())
        self._item_free_rotation = getattr(item, "_old_rotation", item.rotation())

        # Смещения внутри слотов (панорамирование) — чтобы undo возвращал
        # изображения точно на прежние места, а не в центр слота
        self._item_old_offset = QPointF(
            getattr(item, "slot_offset", QPointF(0, 0))
        )
        self._other_old_offset = (
            QPointF(getattr(other, "slot_offset", QPointF(0, 0)))
            if other is not None
            else None
        )

    def _preview_panel(self):
        if self.window is not None and hasattr(self.window, "preview_panel"):
            return self.window.preview_panel
        return None

    def redo(self):
        # Сначала помещаем item в новый слот: accept_image корректно сниме��
        # ссылку старого слота (см. проверку prev.image_item is image_item).
        self.new_slot.accept_image(self.item)

        if self.other is not None:
            if self.old_slot is not None:
                # Обмен: вытесненное изображение — в старый слот item'а
                self.old_slot.accept_image(self.other)
            else:
                # item пришёл не из слота — other возвращается в превью
                panel = self._preview_panel()
                if panel is not None:
                    panel.add_pixmap(
                        self.other.original_pixmap,
                        getattr(self.other, "source_path", None),
                    )
                else:
                    logger.warning(
                        "Preview panel is not available; "
                        "displaced image is removed from scene"
                    )
                if self.other.scene() is self.scene:
                    self.scene.removeItem(self.other)

    def undo(self):
        # 1) Возвращаем item на прежнее место
        if self.old_slot is not None:
            self.old_slot.accept_image(self.item)
            # Восстанавливаем панорамирование внутри старого слота
            self.item.slot_offset = QPointF(self._item_old_offset)
            self.old_slot.position_image(self.item)
        else:
            if self.new_slot.image_item is self.item:
                self.new_slot.image_item = None
            self.item.setParentItem(None)
            if self.item.scene() is not self.scene:
                self.scene.addItem(self.item)
            self.item.setTransformOriginPoint(self.item.boundingRect().center())
            self.item.setPos(self._item_free_pos)
            self.item.setScale(self._item_free_scale)
            self.item.setRotation(self._item_free_rotation)

        # 2) Возвращаем вытесненное изображение в его слот
        if self.other is not None:
            if self.old_slot is None:
                # other был отправлен в превью — забираем обратно
                panel = self._preview_panel()
                if panel is not None and not panel.remove_image(
                    path=getattr(self.other, "source_path", None),
                    pixmap=self.other.original_pixmap,
                ):
                    logger.warning(
                        "Displaced image preview was not found in the panel"
                    )
                if self.other.scene() is not self.scene:
                    self.scene.addItem(self.other)
            self.new_slot.accept_image(self.other)
            # Восстанавливаем панорамирование other внутри его слота
            if self._other_old_offset is not None:
                self.other.slot_offset = QPointF(self._other_old_offset)
                self.new_slot.position_image(self.other)


class ResizeSlotCommand(QUndoCommand):
    """Undo/Redo для изменения размера слота боковыми ползунками.

    ФИКС: раньше ресайз слота вообще не попадал в undo-стек —
    случайно сдвинутую границу было невозможно вернуть через Ctrl+Z.
    Геометрия запоминается целиком (позиция + размер), потому что
    потягивание за левый/верхний маркер меняет и то, и другое.
    """

    def __init__(self, slot, old_pos, old_rect, new_pos, new_rect):
        super().__init__(i18n.t('undo_resize_slot'))
        self.slot = slot
        self.old_pos = QPointF(old_pos)
        self.old_rect = QRectF(old_rect)
        self.new_pos = QPointF(new_pos)
        self.new_rect = QRectF(new_rect)

    def _apply(self, pos, rect):
        self.slot.setRect(0, 0, rect.width(), rect.height())
        self.slot.setPos(pos)
        self.slot._update_handles()

        # Картинку внутри слота пересчитываем (cover + ограниченное
        # панорамирование), но не перепривязываем через accept_image,
        # чтобы не терять slot_offset.
        if self.slot.image_item is not None:
            self.slot.position_image(self.slot.image_item)

    def redo(self):
        # При первом вызове геометрия уже такая — операция идемпотентная
        self._apply(self.new_pos, self.new_rect)

    def undo(self):
        self._apply(self.old_pos, self.old_rect)


class ResizeCanvasCommand(QUndoCommand):
    """Undo/Redo для изменения размера холста.

    ФИКС: смена размера холста масштабирует все слоты шаблона и раньше
    была необратимой. При undo геометрия слотов восстанавливается из
    снимка, а не обратным масштабированием: деление и умножение
    на дробный коэффициент накапливает погрешность.
    """

    def __init__(self, scene, old_size, new_size):
        super().__init__(i18n.t('undo_resize_canvas'))
        self.scene = scene
        self.old_size = (int(old_size[0]), int(old_size[1]))
        self.new_size = (int(new_size[0]), int(new_size[1]))

        # Снимок до изменения: __init__ выполняется до push()/redo()
        self._old_geometry = [
            (slot, QPointF(slot.pos()), QRectF(slot.rect()))
            for slot in getattr(scene, "template_slots", [])
        ]

    def redo(self):
        self.scene.set_canvas_size(*self.new_size)

    def undo(self):
        self.scene.set_canvas_size(*self.old_size)

        for slot, pos, rect in self._old_geometry:
            if slot.scene() is not self.scene:
                continue

            slot.setRect(0, 0, rect.width(), rect.height())
            slot.setPos(pos)
            slot._update_handles()

            if slot.image_item is not None:
                slot.position_image(slot.image_item)


class ChangeLayoutStyleCommand(QUndoCommand):
    """Undo/Redo для отступов, скруглений и фона холста.

    Оформление — свойство сцены, а не отдельных слотов, поэтому
    команда хранит состояние целиком («как было / как стало»).
    При применении картинки в слотах пересчитываются (cover под
    новый размер внутренней области), но сама сетка не меняется.
    """

    def __init__(self, scene, old_style, new_style):
        super().__init__(i18n.t('undo_layout_style'))
        self.scene = scene
        self.old_style = dict(old_style or {})
        self.new_style = dict(new_style or {})

    def redo(self):
        self.scene.apply_layout_style(self.new_style)

    def undo(self):
        self.scene.apply_layout_style(self.old_style)


class PanInSlotCommand(QUndoCommand):
    """Undo/Redo для панорамирования изображения внутри слота шаблона."""

    def __init__(self, slot, item, old_offset, new_offset):
        super().__init__(i18n.t('undo_transform'))
        self.slot = slot
        self.item = item
        self.old_offset = QPointF(old_offset)
        self.new_offset = QPointF(new_offset)

    def _apply(self, offset):
        self.item.slot_offset = QPointF(offset)
        if (
            self.item.parentItem() is self.slot
            and self.slot.image_item is self.item
        ):
            self.slot.position_image(self.item)

    def redo(self):
        self._apply(self.new_offset)

    def undo(self):
        self._apply(self.old_offset)


class RotateInSlotCommand(QUndoCommand):
    """Undo/Redo для поворота изображения внутри слота шаблона.

    Поворот — свойство содержимого, а не элемента сцены: сам слот
    остаётся на месте, а картинка внутри доворачивается и заново
    вписывается по правилу cover, поэтому пустых углов не появляется
    (см. TemplateSlotItem.position_image).

    Идущие подряд повороты одного изображения склеиваются в один шаг
    истории (mergeWith): иначе прокрутка колесом на 30° оставляла бы
    шесть отдельных записей в Ctrl+Z.
    """

    # Идентификатор нужен QUndoStack, чтобы вообще пробовать слияние
    ID = 1001

    def __init__(self, slot, item, old_angle, new_angle):
        super().__init__(i18n.t('undo_rotate_in_slot'))
        self.slot = slot
        self.item = item
        self.old_angle = float(old_angle)
        self.new_angle = float(new_angle)

    def id(self) -> int:
        return self.ID

    def mergeWith(self, other) -> bool:
        if not isinstance(other, RotateInSlotCommand):
            return False

        # Склеиваем только повороты одной и той же картинки в том же слоте
        if other.item is not self.item or other.slot is not self.slot:
            return False

        self.new_angle = other.new_angle
        return True

    def _apply(self, angle):
        self.item.slot_rotation = float(angle)

        if (
            self.item.parentItem() is self.slot
            and self.slot.image_item is self.item
        ):
            self.slot.position_image(self.item)
        else:
            self.item.update()

    def redo(self):
        self._apply(self.new_angle)

    def undo(self):
        self._apply(self.old_angle)


class WheelTransformCommand(QUndoCommand):
    """Undo/Redo для масштаба (Ctrl+колесо) и поворота (Shift+колесо).

    ФИКС: оба жеста меняли элемент напрямую: Ctrl+Z их не видел,
    а окно не считало проект изменённым — подогнал размер фото,
    закрыл приложение и работа пропала без вопросов.

    Шаги одного жеста склеиваются в одну запись истории (mergeWith):
    один прокрут колеса — это десятки событий wheelEvent, и без
    слияния Ctrl+Z пришлось бы жать десятки раз.
    """

    # Идентификатор нужен QUndoStack, чтобы пробовать слияние
    ID = 1002

    def __init__(self, item, kind: str, old_value, new_value):
        super().__init__(
            i18n.t('undo_scale' if kind == 'scale' else 'undo_rotate')
        )
        self.item = item
        self.kind = kind
        self.old_value = float(old_value)
        self.new_value = float(new_value)

    def id(self) -> int:
        return self.ID

    def mergeWith(self, other) -> bool:
        if not isinstance(other, WheelTransformCommand):
            return False

        # Склеиваем только однотипные шаги по одному элементу:
        # поворот после масштаба должен отменяться отдельно
        if other.item is not self.item or other.kind != self.kind:
            return False

        self.new_value = other.new_value
        return True

    def _apply(self, value):
        if self.kind == 'scale':
            self.item.setScale(value)
        else:
            self.item.setRotation(value)

    def redo(self):
        self._apply(self.new_value)

    def undo(self):
        self._apply(self.old_value)


class ContentViewCommand(QUndoCommand):
    """Undo/Redo для кадрирования содержимого (режим Z).

    Кадрирование — это пара значений (зум и центр видимой
    области); пиксмап не пересоздаётся, поэтому и отмена
    стоит копейки. Зум колесом склеивается в один шаг истории,
    а сдвиг мышью пишется одной записью на жест (mergeable=False).
    """

    ID = 1003

    def __init__(
        self,
        item,
        old_zoom,
        old_center,
        new_zoom,
        new_center,
        mergeable: bool = True,
    ):
        super().__init__(i18n.t('undo_content_view'))
        self.item = item
        self.old_zoom = float(old_zoom)
        self.new_zoom = float(new_zoom)
        self.old_center = QPointF(old_center)
        self.new_center = QPointF(new_center)
        self._mergeable = bool(mergeable)

    def id(self) -> int:
        # -1 отключает слияние: законченный жест мыши —
        # самостоятельный шаг истории
        return self.ID if self._mergeable else -1

    def mergeWith(self, other) -> bool:
        if not isinstance(other, ContentViewCommand):
            return False

        if not other._mergeable or other.item is not self.item:
            return False

        self.new_zoom = other.new_zoom
        self.new_center = QPointF(other.new_center)
        return True

    def _apply(self, zoom, center):
        self.item.apply_content_view(zoom, center)

    def redo(self):
        self._apply(self.new_zoom, self.new_center)

    def undo(self):
        self._apply(self.old_zoom, self.old_center)


class MirrorCommand(QUndoCommand):
    """Undo/Redo для зеркалирования (Ctrl+Shift+H / Ctrl+Shift+V).

    Зеркалирование — переключатель, поэтому undo и redo делают
    одно и то же. ФИКС: раньше mirror_image вызывался напрямую и
    жест не попадал ни в Ctrl+Z, ни в флаг несохранённых изменений.
    """

    def __init__(self, items, axis: str):
        super().__init__(i18n.t('undo_mirror'))
        self.items = list(items)
        self.axis = axis

    def _toggle(self):
        for item in self.items:
            item.mirror_image(self.axis)

    def redo(self):
        self._toggle()

    def undo(self):
        self._toggle()


class ChangeLayerCommand(QUndoCommand):
    """Undo/Redo для команд «Слои» (Ctrl+] и Ctrl+[).

    Слот шаблона меняет базовый слой (set_base_z — иначе теряется
    приподнятое состояние подсветки), свободный элемент — zValue.
    """

    def __init__(self, target, old_z, new_z):
        super().__init__(i18n.t('undo_layer'))
        self.target = target
        self.old_z = float(old_z)
        self.new_z = float(new_z)

    def _apply(self, z):
        if hasattr(self.target, "set_base_z"):
            self.target.set_base_z(z)
        else:
            self.target.setZValue(z)

    def redo(self):
        self._apply(self.new_z)

    def undo(self):
        self._apply(self.old_z)
