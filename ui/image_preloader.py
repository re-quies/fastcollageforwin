"""Предзагрузка изображений с прогрессом и без вложенного QEventLoop.

Зачем это вынесено в отдельный модуль
----------------------------------------
Открытие проекта и сборка авто-коллажа раньше ждали окончания
чтения файлов внутри QEventLoop.exec(): управление не возвращалось
в основной цикл событий, а рекурсивный цикл — классический
источник трудновоспроизводимых эффектов:

- пользователь может вызвать тот же обработчик повторно (два открытия
  проекта одновременно, второе — из стека первого);
- закрытие окна во время ожидания оставляло цикл без владельца;
- QProgressDialog сам крутит события и испускает canceled() при
  close(), из-за чего успешная загрузка могла выглядеть как отмена.

Здесь тот же прогресс сделан без рекурсии: диалог блокирует ввод
(ApplicationModal), но не выполнение кода, а продолжение работы
вызывается коллбэком on_done(cancelled), когда все файлы прочитаны
или загрузку отменили.
"""

import logging
import os

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QProgressDialog

from core import image_cache
from core.async_loader import AsyncImageLoader
import i18n

logger = logging.getLogger(__name__)

# Пока загрузка короче этого времени, окно прогресса не показывается:
# проект из трёх фотографий открывается мгновенно, и мелькающее окно
# только раздражает
PROGRESS_DELAY_MS = 300


class ImagePreloader(QObject):
    """Читает список файлов в фоне и вызывает on_done(cancelled).

    Готовые рабочие копии кладутся в image_cache, откуда их берёт
    последующая сборка сцены — уже без обращений к диску.

    Один экземпляр обслуживает одну загрузку за раз: повторный
    start() отменяет предыдущую.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # Загрузчик создаётся ОДИН раз и живёт вместе с объектом:
        # удалять его после каждой загрузки нельзя — рабочие потоки
        # могут ещё читать файл и отправлять сигнал
        self._loader = AsyncImageLoader(self)
        self._loader.loaded.connect(self._on_loaded)

        self._dialog = None
        self._on_done = None

        self._total = 0
        self._done = 0
        self._cancelled = False
        self._running = False

    # ---------- Public API ----------

    def is_running(self) -> bool:
        return self._running

    def start(self, paths, on_done, parent_widget=None):
        """Начать загрузку. on_done(cancelled) вызовется ровно один раз.

        Коллбэк всегда вызывается из основного цикла событий и НИКОГДА
        — из самого start(), даже если загружать нечего: вызывающий код
        получает одинаковое поведение в обоих случаях.
        """
        if self._running:
            logger.warning("Preload: a previous run is still active; cancelling it")
            self.cancel()

        files = []
        seen = set()
        for path in paths or []:
            if not path:
                continue
            text = str(path)
            if text in seen:
                continue
            seen.add(text)
            if os.path.isfile(text):
                files.append(text)

        self._on_done = on_done
        self._cancelled = False
        self._done = 0
        self._total = len(files)

        if not files:
            self._running = False
            self._call_done()
            return

        self._running = True

        widget = parent_widget if parent_widget is not None else self.parent()

        self._dialog = QProgressDialog(
            i18n.t('loading_images'), i18n.t('cancel'), 0, self._total, widget
        )
        self._dialog.setWindowTitle(i18n.t('app_title'))
        self._dialog.setWindowModality(Qt.ApplicationModal)
        # Диалогом управляем только мы: автозакрытие на максимуме
        # и автосброс мешают отличать отмену от завершения
        self._dialog.setAutoClose(False)
        self._dialog.setAutoReset(False)
        self._dialog.setMinimumDuration(PROGRESS_DELAY_MS)
        self._dialog.setValue(0)
        self._dialog.canceled.connect(self._on_cancel_clicked)

        logger.info("Preload: reading %d image file(s) in the background", self._total)

        for path in files:
            self._loader.submit(path)

    def cancel(self, notify=False):
        """Прервать загрузку извне (например, при закрытии окна).

        По умолчанию коллбэк НЕ вызывается: продолжать открытие
        проекта в закрывающемся окне нет смысла.
        """
        if not self._running:
            return

        self._cancelled = True

        if notify:
            self._finish()
            return

        self._running = False
        self._on_done = None
        self._teardown()

    def wait_for_done(self, msecs=None):
        """Дождаться рабочих потоков предзагрузки (при выходе)."""
        if msecs is None:
            return self._loader.wait_for_done()

        return self._loader.wait_for_done(msecs)

    # ---------- Internals ----------

    def _on_cancel_clicked(self):
        if not self._running:
            return

        logger.info("Preload: cancelled by the user")
        self._cancelled = True
        self._finish()

    def _on_loaded(self, _token, path, image):
        if not self._running:
            return

        if image is not None and not image.isNull():
            # QPixmap создаётся только здесь, в GUI-потоке
            image_cache.put_working_pixmap(path, QPixmap.fromImage(image))

        self._done += 1

        if self._dialog is not None:
            self._dialog.setValue(min(self._done, self._total))

        if self._done >= self._total:
            self._finish()

    def _finish(self):
        self._running = False
        self._teardown()

        logger.info(
            "Preload: %d of %d image(s) ready, cancelled: %s",
            self._done,
            self._total,
            self._cancelled,
        )

        self._call_done()

    def _teardown(self):
        self._loader.cancel_all()

        dialog = self._dialog
        self._dialog = None

        if dialog is None:
            return

        # QProgressDialog.close() сам испускает canceled(), даже если окно
        # так и не показалось: без отключения сигнала успешная
        # загрузка считалась бы отменённой
        try:
            dialog.canceled.disconnect(self._on_cancel_clicked)
        except (RuntimeError, TypeError):
            pass

        dialog.close()
        dialog.deleteLater()

    def _call_done(self):
        callback = self._on_done
        self._on_done = None

        if callback is None:
            return

        cancelled = self._cancelled

        # Продолжение вызывается из основного цикла событий, а не из
        # обработчика сигнала загрузчика: пересборка сцены не должна
        # идти внутри стека, который её же и запустил
        QTimer.singleShot(0, lambda: callback(cancelled))
