"""Справка по горячим клавишам и жестам мыши.

Часть управления нигде не подписана: режимы Z, C и R, возврат фото
в панель по X, панорамирование средней кнопкой мыши. Узнать о них
можно было только из исходников — этот диалог закрывает пробел
(Настройки → Горячие клавиши, либо F1).
"""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
)

import i18n


# Разделы справки: (ключ заголовка, ((клавиши, ключ описания), ...)).
# В колонке клавиш {wheel}, {drag} и {mmb} подставляются переводом:
# «колесо» и «средняя кнопка» на трёх языках звучат по-разному.
SECTIONS = (
    ('file', (
        ("Ctrl+O", 'add_image'),
        ("Ctrl+Shift+O", 'open_project'),
        ("Ctrl+S", 'save_project'),
        ("Ctrl+Shift+S", 'save_project_as'),
        ("Ctrl+E", 'export'),
    )),
    ('edit', (
        ("Ctrl+Z", 'undo'),
        ("Ctrl+Y", 'redo'),
        ("Delete", 'delete'),
        ("Ctrl+C", 'copy'),
        ("Ctrl+V", 'paste'),
        ("Ctrl+D", 'duplicate'),
        ("Ctrl+]", 'bring_front'),
        ("Ctrl+[", 'send_back'),
    )),
    ('canvas', (
        ("Ctrl+Shift+C", 'canvas_size'),
        ("Ctrl+Shift+A", 'auto_canvas_action'),
        ("Ctrl+M", 'zoom'),
        ("Ctrl + {wheel}", 'sc_view_zoom'),
        ("{mmb} + {drag}", 'sc_canvas_pan'),
        ("← ↑ ↓ →", 'sc_pan_buttons'),
    )),
    ('image_menu', (
        ("Ctrl+Shift+H", 'mirror_h'),
        ("Ctrl+Shift+V", 'mirror_v'),
        ("Ctrl+Shift+L", 'rotate_left'),
        ("Ctrl+Shift+R", 'rotate_right'),
        ("Ctrl + {wheel}", 'sc_item_scale'),
        ("Shift + {wheel}", 'sc_item_rotate'),
    )),
    ('sc_modes', (
        ("Z + {wheel}", 'sc_mode_zoom'),
        ("Z + {drag}", 'sc_mode_zoom_pan'),
        ("C + {drag}", 'sc_mode_pan'),
        ("Alt + {drag}", 'sc_mode_pan_alt'),
        ("R + {drag}", 'sc_mode_rotate'),
        ("X", 'sc_return_preview'),
    )),
)


def _escape(text: str) -> str:
    """Перевод попадает в HTML: символы разметки экранируем."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_html() -> str:
    """Собрать таблицу справки на текущем языке."""
    gestures = {
        "wheel": i18n.t('key_wheel'),
        "drag": i18n.t('key_drag'),
        "mmb": i18n.t('key_mmb'),
    }

    parts = []

    for section_key, rows in SECTIONS:
        parts.append(
            "<h3 style='margin: 12px 0 2px 0;'>%s</h3>"
            % _escape(i18n.t(section_key))
        )
        parts.append(
            "<table width='100%' cellspacing='0' cellpadding='3'>"
        )

        for keys, description_key in rows:
            parts.append(
                "<tr>"
                "<td width='40%%' valign='top'><b>%s</b></td>"
                "<td valign='top'>%s</td>"
                "</tr>" % (
                    _escape(keys.format(**gestures)),
                    _escape(i18n.t(description_key)),
                )
            )

        parts.append("</table>")

    return "".join(parts)


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle(i18n.t('shortcuts_title'))
        self.resize(560, 620)

        layout = QVBoxLayout(self)

        hint = QLabel(i18n.t('shortcuts_hint'), self)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # QTextBrowser, а не QLabel: список длиннее экрана, текст
        # должен прокручиваться и выделяться для копирования
        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(False)
        self.browser.setHtml(build_html())
        layout.addWidget(self.browser)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)

        close_button = buttons.button(QDialogButtonBox.Close)
        if close_button is not None:
            close_button.setText(i18n.t('close'))

        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
