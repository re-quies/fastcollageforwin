from PySide6.QtCore import QRectF
import math


class TemplateGenerator:
    def __init__(self, canvas_width: int, canvas_height: int):
        self.w = canvas_width
        self.h = canvas_height

    def generate(self, count: int) -> list[QRectF]:
        """
        Возвращает список QRectF — слоты шаблона
        """
        cols = math.ceil(math.sqrt(count))
        rows = math.ceil(count / cols)

        slot_w = self.w / cols
        slot_h = self.h / rows

        rects = []

        i = 0
        for row in range(rows):
            for col in range(cols):
                if i >= count:
                    break

                x = col * slot_w
                y = row * slot_h

                rects.append(
                    QRectF(x, y, slot_w, slot_h)
                )
                i += 1

        return rects
