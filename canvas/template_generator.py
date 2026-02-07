from PySide6.QtCore import QRectF
import random


def generate_template(canvas_width, canvas_height, count):
    """
    Возвращает список QRectF,
    которые полностью покрывают холст
    """
    rects = [QRectF(0, 0, canvas_width, canvas_height)]

    while len(rects) < count:
        # берём самый большой прямоугольник
        rects.sort(key=lambda r: r.width() * r.height(), reverse=True)
        rect = rects.pop(0)

        if rect.width() > rect.height():
            split = random.uniform(0.35, 0.65)
            w1 = rect.width() * split
            w2 = rect.width() - w1

            rects.append(QRectF(rect.left(), rect.top(), w1, rect.height()))
            rects.append(QRectF(rect.left() + w1, rect.top(), w2, rect.height()))
        else:
            split = random.uniform(0.35, 0.65)
            h1 = rect.height() * split
            h2 = rect.height() - h1

            rects.append(QRectF(rect.left(), rect.top(), rect.width(), h1))
            rects.append(QRectF(rect.left(), rect.top() + h1, rect.width(), h2))

    return rects[:count]
