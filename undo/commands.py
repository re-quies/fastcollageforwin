from PySide6.QtGui import QUndoCommand
import i18n


class AddItemCommand(QUndoCommand):
    """
    Undo/Redo для добавления изображения на сцену
    """

    def __init__(self, scene, item):
        super().__init__(i18n.t('undo_add'))
        self.scene = scene
        self.item = item

    def redo(self):
        if self.item not in self.scene.items():
            self.scene.addItem(self.item)

    def undo(self):
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
