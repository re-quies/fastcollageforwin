import logging

from PySide6.QtGui import QUndoCommand
from PySide6.QtCore import QPointF

import i18n
from canvas.slot_item import TemplateSlotItem

logger = logging.getLogger(__name__)


class AddItemCommand(QUndoCommand):
    """
    Undo/Redo для добавления изображения на сцену.

    Если указан `slot` — изображение сразу помещается в слот шаблона
    (drag&drop в template режиме), а undo корректно очищает ссылку
    slot.image_item, чтобы не оставалась висячая ссылка.
    """

    def __init__(self, scene, item, slot=None):
        super().__init__(i18n.t('undo_add'))
        self.scene = scene
        self.item = item
        self.slot = slot

    def redo(self):
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
                # Вернуть в прежний слот (accept_image сам центрирует)
                slot.accept_image(item)
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

    def __init__(self, scene, window, item):
        super().__init__(i18n.t('delete'))
        self.scene = scene
        self.window = window
        self.item = item

        parent = item.parentItem()
        self.slot = parent if isinstance(parent, TemplateSlotItem) else None

        self._pos = QPointF(item.pos())
        self._scale = item.scale()
        self._rotation = item.rotation()
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
        # Сначала помещаем item в новый слот: accept_image корректно снимет
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
