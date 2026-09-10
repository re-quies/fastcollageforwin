"""Фоновая загрузка изображений: декодирование вне GUI-потока.

Раскодировать JPEG — это десятки, а для больших снимков и сотни
миллисекунд. Раньше это происходило прямо в GUI-потоке: добавление
полусотни фотографий в панель превью «вешало» окно на несколько секунд,
а открытие проекта выглядело как зависание приложения.

Здесь задачи уходят в отдельный QThreadPool. Важное ограничение Qt:
QPixmap нельзя создавать вне GUI-потока, поэтому рабочий поток отдаёт
QImage, а превращение в QPixmap делает уже получатель сигнала — он
всегда вызывается в GUI-потоке, потому что сигнал доставляется через
очередь событий.

Три вещи, которые здесь сделаны осознанно:

1. Число потоков считается от числа ядер, а не зашито четвёркой:
   на двухядерном ноутбуке четыре декодера отнимали время у
   интерфейса, а на двенадцатиядерном ПК — простаивали.

2. Один и тот же файл читается один раз. Раньше повторный submit()
   того же пути ставил вторую такую же задачу в очередь — два раза
   декодировали одну и ту же фотографию (типично для панели,
   где один снимок добавлен дважды). Теперь второй заказчик
   попадает в список ожидающих и получает тот же результат.

3. Отмена больше не врёт. cancel_all() обнулял счётчик задач, хотя
   рабочие потоки в этот момент продолжали читать файлы: при выходе
   из программы окно уже удалено, а задача ещё жива. Теперь
   задача ВСЕГДА сообщает о завершении, есть is_busy(), а
   wait_for_done() пишет в лог, если таймаут истёк.
"""

import logging

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal

from core import image_cache

logger = logging.getLogger(__name__)

# Меньше двух потоков смысла не имеет (пропадает весь выигрыш),
# больше восьми — тоже: узкое место не диск, а декодирование,
# и каждый поток держит в памяти свою копию картинки.
MIN_THREADS = 2
MAX_THREADS_CAP = 8

# None — определить автоматически по числу ядер (см. default_max_threads)
DEFAULT_MAX_THREADS = None

# Сколько ждать рабочие потоки при выходе. Прежние 3 секунды
# на медленном диске (или сетевом диске) истекали посреди чтения
# большого файла, и процесс завершался с живыми потоками.
DEFAULT_WAIT_MS = 15000


def default_max_threads() -> int:
    """Сколько рабочих потоков имеет смысл на этой машине."""
    try:
        ideal = int(QThread.idealThreadCount())
    except (TypeError, ValueError):
        ideal = 0

    if ideal <= 0:
        # Qt не смогла определить число ядер — берём прежнее значение
        ideal = 4

    return max(MIN_THREADS, min(ideal, MAX_THREADS_CAP))


class _TaskSignals(QObject):
    """Мост «рабочий поток → GUI-поток».

    Объект создаётся в GUI-потоке, поэтому соединение получается
    очередным (Qt::QueuedConnection) и обработчик выполняется там,
    где безопасно трогать виджеты и QPixmap.
    """

    # поколение, ключ загрузки, путь, QImage (может быть null/None)
    loaded = Signal(int, object, str, object)

    # Приходит всегда, даже если результат не отправлен (отмена,
    # ошибка): без этого загрузчик не знает, сколько задач ещё живо
    finished = Signal(int, object)


class _LoadTask(QRunnable):
    """Одна загрузка файла в рабочем потоке."""

    def __init__(self, signals, generation, key, path, max_side, is_cancelled):
        super().__init__()

        self._signals = signals
        self._generation = int(generation)
        self._key = key
        self._path = str(path)
        self._max_side = int(max_side)
        self._is_cancelled = is_cancelled

    def run(self):
        try:
            # Задача могла простоять в очереди дольше, чем был нужен результат
            if self._is_cancelled(self._generation):
                return

            try:
                image = image_cache.read_scaled_image(
                    self._path, self._max_side
                )
            except Exception:
                # Рабочий поток не имеет права падать: исключение здесь
                # осталось бы без обработчика и утащило бы приложение
                logger.exception("Background load failed for %s", self._path)
                image = None

            if self._is_cancelled(self._generation):
                return

            self._signals.loaded.emit(
                self._generation, self._key, self._path, image
            )
        finally:
            # Сигнал завершения нужен даже при отмене: иначе ожидающие
            # этого файла зависли бы навсегда, а прогресс не дошёл бы
            # до нуля
            self._signals.finished.emit(self._generation, self._key)


class AsyncImageLoader(QObject):
    """Очередь фоновых загрузок с отменой и без двойного чтения.

    Сигнал ``loaded(token, path, image)`` приходит уже в GUI-потоке:
    ``image`` — это QImage (может быть пустым, если файл не прочитан)
    либо None, если загрузка сорвалась с ошибкой.

    ``token`` — произвольный объект заказчика: по нему получатель
    понимает, к какому элементу списка или сцене относится результат.
    На один файл может быть несколько заказчиков — файл будет
    прочитан один раз, а сигнал придёт каждому.
    """

    loaded = Signal(object, str, object)
    # Сколько заказов ещё в работе (для индикаторов прогресса)
    progressChanged = Signal(int)

    def __init__(self, parent=None, max_threads=DEFAULT_MAX_THREADS):
        super().__init__(parent)

        # Собственный пул, а не глобальный: фоновая загрузка картинок
        # не должна конкурировать с другими задачами Qt
        threads = (
            default_max_threads()
            if not max_threads
            else max(1, int(max_threads))
        )

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(threads)

        logger.debug("Image loader: %d worker thread(s)", threads)

        self._signals = _TaskSignals()
        self._signals.loaded.connect(self._on_loaded)
        self._signals.finished.connect(self._on_task_finished)

        self._generation = 0

        # Сколько заказов ждёт результата (с учётом тех, что
        # присоединились к уже идущей загрузке того же файла)
        self._pending = 0

        # Сколько задач реально живёт в пуле (включая отменённые,
        # которые ещё не дочитали свой файл)
        self._active = 0

        # {(путь, max_side): [токены заказчиков]}
        self._waiters = {}

    # ---------- Public API ----------

    def submit(
        self, path, token=None, max_side=image_cache.MAX_WORKING_SIDE
    ) -> bool:
        """Поставить файл в очередь на чтение.

        Возвращает True, если создана новая задача, и False, если
        такой файл уже читается и заказ присоединён к ней.
        """
        key = (str(path), int(max_side))

        self._pending += 1

        waiters = self._waiters.get(key)
        if waiters is not None:
            # Файл уже читается — второй раз декодировать его незачем
            waiters.append(token)
            self.progressChanged.emit(self._pending)
            return False

        self._waiters[key] = [token]

        task = _LoadTask(
            self._signals,
            self._generation,
            key,
            path,
            max_side,
            self._is_cancelled,
        )

        self._active += 1
        self._pool.start(task)
        self.progressChanged.emit(self._pending)

        return True

    def pending(self) -> int:
        """Сколько заказов ещё не завершено."""
        return self._pending

    def is_busy(self) -> bool:
        """Живы ли ещё рабочие задачи (включая отменённые)."""
        return self._active > 0

    def cancel_all(self):
        """Забыть про все текущие заказы.

        Смена поколения гасит и те задачи, что уже выполняются:
        результат просто не будет отправлен. ``clear()``
        дополнительно убирает из очереди те, что ещё не начали
        считать.

        ВАЖНО: прервать уже идущее чтение файла нельзя — такие
        задачи дочитают своё и завершатся сами. См. is_busy()
        и wait_for_done() перед удалением получателей сигнала.
        """
        self._generation += 1
        self._pending = 0
        self._waiters.clear()
        self._pool.clear()

        if self._active:
            logger.debug(
                "Image loader: cancelled, %d task(s) still finishing",
                self._active,
            )

        self.progressChanged.emit(0)

    def wait_for_done(self, msecs: int = DEFAULT_WAIT_MS) -> bool:
        """Дождаться завершения рабочих потоков (при выходе).

        Возвращает False, если таймаут истёк раньше: раньше это
        происходило молча и приложение завершалось с живыми потоками.
        """
        done = bool(self._pool.waitForDone(int(msecs)))

        if not done:
            logger.warning(
                "Image loader: %d task(s) still running after %d ms",
                self._active,
                int(msecs),
            )

        return done

    # ---------- Internals ----------

    def _is_cancelled(self, generation) -> bool:
        return generation != self._generation

    def _on_loaded(self, generation, key, path, image):
        # Результат отменённого поколения мог уже лежать в очереди сигналов
        if generation != self._generation:
            return

        tokens = self._waiters.pop(key, [])
        if not tokens:
            return

        self._pending = max(0, self._pending - len(tokens))
        self.progressChanged.emit(self._pending)

        # Одно чтение — всем заказчикам этого файла
        for token in tokens:
            self.loaded.emit(token, path, image)

    def _on_task_finished(self, generation, key):
        self._active = max(0, self._active - 1)

        if generation != self._generation:
            return

        # Задача завершилась, не отправив результат (отмена или сбой):
        # ожидающих надо снять с учёта, иначе индикатор прогресса
        # никогда не дойдёт до нуля
        tokens = self._waiters.pop(key, None)
        if tokens:
            self._pending = max(0, self._pending - len(tokens))
            self.progressChanged.emit(self._pending)
