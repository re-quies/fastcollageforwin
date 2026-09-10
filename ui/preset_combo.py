# -*- coding: utf-8 -*-
"""Заполнение выпадающего списка пресетов холста.

Оба диалога (создание коллажа и изменение размера) используют одну
и ту же функцию, чтобы списки не разъезжались.
"""

from core.canvas_presets import iter_preset_entries
import i18n


def fill_preset_combo(combo):
    """Заполнить QComboBox пресетами, сгруппированными по назначению.

    Заголовки групп добавляются как неактивные элементы, поэтому
    выбрать их нельзя, а разделители отбивают группы друг от друга.
    Подписи заголовков берутся из i18n, так что список переводится
    вместе с остальным интерфейсом.
    """
    combo.clear()
    first = True
    for entry in iter_preset_entries():
        if entry[0] == "group":
            if not first:
                combo.insertSeparator(combo.count())
            combo.addItem(i18n.t(entry[1]))
            try:
                combo.model().item(combo.count() - 1).setEnabled(False)
            except (AttributeError, TypeError):
                # Если модель не даёт доступа к элементам, заголовок
                # останется выбираемым, но _apply_preset его игнорирует.
                pass
        else:
            combo.addItem(entry[1])
        first = False
    return combo
