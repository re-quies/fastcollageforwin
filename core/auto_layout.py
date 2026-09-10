"""Подбор оптимального холста под фотографии из панели превью.

Задача: по набору размеров фотографий найти такой холст и такую сетку
ячеек, чтобы каждая фотография легла в ячейку СВОИХ пропорций (то есть
без обрезки), холст получился компактным, а его размер в пикселях —
«круглым».

Как это работает
----------------
1. Раскладка описывается двоичным деревом:
   - лист   — фотография с пропорцией ``a = w / h``;
   - узел h — два блока рядом по горизонтали (общая высота):
     ``a = a1 + a2``;
   - узел v — два блока друг под другом (общая ширина):
     ``1/a = 1/a1 + 1/a2``.
   При таком построении пропорции ячеек ТОЧНО совпадают с пропорциями
   фотографий, поэтому обрезки нет вовсе.

2. Перебираются варианты деревьев (динамика по отрезкам с отсевом
   близких по пропорции вариантов) плюс раскладки рядами, и всё это —
   для нескольких порядков фотографий.

3. Для каждого варианта считается минимальная ширина холста, при
   которой ни одну фотографию не приходится растягивать, и штраф за
   «лишнее» растяжение остальных. Побеждает вариант с минимальным
   штрафом.

4. Итоговый размер округляется (см. ``CanvasPlan.set_rounding``).

Пример из постановки задачи: 200×400, 200×400 и 400×400 дают дерево
v(h(A, B), C) — две вертикальные фотографии в ряд образуют квадрат
400×400, под ним квадрат 400×400, итого холст 400×800 без обрезки.
Если те же фотографии имеют размер 220×420, идеальный холст — 440×860,
а округление до 100 px даёт ожидаемые 400×800.

Модуль намеренно не зависит от PySide6: это чистая арифметика, которую
удобно проверять отдельно от интерфейса.
"""

import logging
import math

from core.canvas_presets import MAX_SIDE, MIN_SIDE

logger = logging.getLogger(__name__)

# --- Режимы округления итогового размера холста ---
# "auto"  — самый крупный круглый шаг, который почти ничего не стоит
#           по точности, иначе кратно BASE_STEP;
# "exact" — без округления;
# число   — кратно этому шагу (10, 100 и т. д.).
ROUNDING_AUTO = "auto"
ROUNDING_EXACT = "exact"

# Шаги «авто»-округления, от крупного к мелкому
AUTO_STEPS = (1000, 500, 250, 100, 50)

# Базовый шаг: до него округляем всегда (не бывает холстов 443×857)
BASE_STEP = 10

# Насколько «авто» разрешает отклониться от идеального размера…
AUTO_SIZE_TOLERANCE = 0.05
# …и от идеальных пропорций. Отклонение пропорций — это и есть
# обрезка по краям, поэтому порог здесь заметно строже.
AUTO_ASPECT_TOLERANCE = 0.008

# Выше этого числа фотографий полный перебор деревьев не запускаем:
# остаются только раскладки рядами (перебор растёт слишком быстро).
MAX_TREE_PHOTOS = 20

# Веса оценки варианта раскладки
WEIGHT_MEAN_UPSCALE = 2.0
WEIGHT_PEAK_UPSCALE = 1.0
WEIGHT_EXTREME_ASPECT = 1.2

# До этого соотношения сторон холст считается «нормальным»,
# дальше начинается штраф за вытянутость (1:3, 1:5 и т. п.)
COMFORT_ASPECT = 2.0


# ---------------------------------------------------------------- план


class CanvasPlan:
    """Рассчитанный холст: размер, ячейки и парный вариант для ⇄.

    Ячейки хранятся дважды: в долях холста (``unit_cells``, не зависят
    от округления) и в пикселях (``cells``, пересчитываются при смене
    режима округления).

    Каждая ячейка — кортеж ``(индекс фотографии, x, y, w, h)``.
    """

    def __init__(self, aspect, unit_cells, exact_size, score, photos):
        self.aspect = float(aspect)
        self.unit_cells = unit_cells
        self.exact_width = float(exact_size[0])
        self.exact_height = float(exact_size[1])
        self.score = float(score)

        # {индекс фотографии: (ширина, высота)} — для расчёта обрезки
        self._photos = photos

        # Вариант той же раскладки в другой ориентации (кнопка ⇄)
        self.alternative = None

        self.rounding = ROUNDING_AUTO
        self.width = 0
        self.height = 0
        self.cells = []
        self.max_crop = 0.0

        self.set_rounding(ROUNDING_AUTO)

    # ---------- Размер и округление ----------

    def set_rounding(self, mode):
        """Пересчитать размер холста и ячейки под режим округления."""
        if mode not in (ROUNDING_AUTO, ROUNDING_EXACT):
            try:
                mode = max(1, int(mode))
            except (TypeError, ValueError):
                mode = ROUNDING_AUTO

        self.rounding = mode

        if mode == ROUNDING_EXACT:
            size = (
                int(round(self.exact_width)),
                int(round(self.exact_height)),
            )
        elif mode == ROUNDING_AUTO:
            size = _auto_round(
                self.exact_width, self.exact_height, self.aspect
            )
        else:
            size = _round_to_step(
                self.exact_width, self.exact_height, self.aspect, mode
            )

        self.width, self.height = _fit_limits(size[0], size[1])
        self.cells = _pixel_cells(self.unit_cells, self.width, self.height)
        self.max_crop = _max_crop(self.cells, self._photos)

        return self

    # ---------- Служебное ----------

    @property
    def cell_count(self):
        return len(self.unit_cells)

    def photo_size(self, index):
        return self._photos.get(index)

    def __repr__(self):  # pragma: no cover — только для отладки
        return "CanvasPlan(%dx%d, cells=%d, crop=%.1f%%)" % (
            self.width, self.height, self.cell_count, self.max_crop * 100
        )


# ------------------------------------------------------------ публичное


def plan_canvas(sizes, rounding=ROUNDING_AUTO):
    """Подобрать холст под список размеров фотографий.

    ``sizes`` — последовательность ``(ширина, высота)`` в пикселях,
    в том же порядке, в каком фотографии лежат в панели превью.

    Возвращает лучший ``CanvasPlan`` (или ``None``, если считать нечего).
    У плана заполнено поле ``alternative`` — лучший вариант в другой
    ориентации, между ними переключает кнопка ⇄ в диалоге.
    """
    photos, index_map = _normalized(sizes)
    if not photos:
        return None

    photos_by_index = {
        index_map[pos]: photos[pos] for pos in range(len(photos))
    }

    best = {}

    for order in _orderings(photos):
        aspects = [photos[i][0] / float(photos[i][1]) for i in order]

        for root in _candidate_roots(aspects):
            plan = _make_plan(root, order, photos, index_map, photos_by_index)
            if plan is None:
                continue

            key = "landscape" if plan.aspect >= 1.0 else "portrait"
            current = best.get(key)
            if current is None or plan.score < current.score:
                best[key] = plan

    primary = _pick_primary(best, photos)
    if primary is None:
        return None

    other_key = "portrait" if primary.aspect >= 1.0 else "landscape"
    other = best.get(other_key)

    if other is not None and other is not primary:
        primary.alternative = other
        other.alternative = primary

    if rounding != ROUNDING_AUTO:
        primary.set_rounding(rounding)
        if other is not None:
            other.set_rounding(rounding)

    return primary


# -------------------------------------------------------- подготовка


def _normalized(sizes):
    """Отбросить непригодные размеры, сохранив исходные индексы."""
    photos = []
    index_map = []

    for index, size in enumerate(sizes or []):
        try:
            width = int(round(float(size[0])))
            height = int(round(float(size[1])))
        except (TypeError, ValueError, IndexError):
            logger.warning("Skipping malformed image size: %r", size)
            continue

        if width <= 0 or height <= 0:
            logger.warning("Skipping empty image size: %r", size)
            continue

        photos.append((width, height))
        index_map.append(index)

    return photos, index_map


def _orderings(photos):
    """Порядки фотографий, которые стоит попробовать.

    Порядок панели сохраняется как первый вариант, остальные —
    перестановки ради более плотной упаковки.
    """
    count = len(photos)
    base = list(range(count))

    if count < 3:
        return [base]

    aspects = [photos[i][0] / float(photos[i][1]) for i in base]
    areas = [photos[i][0] * photos[i][1] for i in base]

    variants = [
        base,
        sorted(base, key=lambda i: aspects[i]),
        sorted(base, key=lambda i: -aspects[i]),
        sorted(base, key=lambda i: -areas[i]),
    ]

    if count > MAX_TREE_PHOTOS:
        variants = variants[:2]

    unique = []
    seen = set()
    for variant in variants:
        key = tuple(variant)
        if key in seen:
            continue
        seen.add(key)
        unique.append(list(variant))

    return unique


# ----------------------------------------------------- дерево раскладки


def _combine(kind, left, right):
    """Склеить два блока по горизонтали (h) или по вертикали (v)."""
    left_aspect = left[3]
    right_aspect = right[3]

    if kind == "h":
        return ("h", left, right, left_aspect + right_aspect)

    return (
        "v",
        left,
        right,
        (left_aspect * right_aspect) / (left_aspect + right_aspect),
    )


def _candidate_limit(count):
    """Сколько вариантов пропорций держать на один отрезок."""
    if count <= 8:
        return 14
    if count <= 12:
        return 8
    if count <= 18:
        return 5
    return 3


def _prune(variants, limit):
    """Оставить limit вариантов, равномерно разбросанных по пропорциям."""
    variants.sort(key=lambda node: node[3])

    unique = []
    for node in variants:
        if unique and abs(math.log(node[3] / unique[-1][3])) < 0.02:
            continue
        unique.append(node)

    if len(unique) <= limit:
        return unique

    step = (len(unique) - 1) / float(limit - 1)

    picked = []
    used = set()
    for position in range(limit):
        index = int(round(position * step))
        if index in used:
            continue
        used.add(index)
        picked.append(unique[index])

    return picked


def _tree_candidates(aspects):
    """Перебор деревьев раскладки динамикой по отрезкам."""
    count = len(aspects)
    limit = _candidate_limit(count)

    table = {}
    for i in range(count):
        table[(i, i + 1)] = [("leaf", i, None, aspects[i])]

    for length in range(2, count + 1):
        for start in range(0, count - length + 1):
            end = start + length
            variants = []

            for split in range(start + 1, end):
                for left in table[(start, split)]:
                    for right in table[(split, end)]:
                        variants.append(_combine("h", left, right))
                        variants.append(_combine("v", left, right))

            table[(start, end)] = _prune(variants, limit)

    return table[(0, count)]


def _split_rows(aspects, rows):
    """Разбить последовательность на rows рядов примерно равной «ширины»."""
    count = len(aspects)
    if rows < 1 or rows > count:
        return None

    target = sum(aspects) / float(rows)

    groups = []
    current = []
    acc = 0.0

    for index, aspect in enumerate(aspects):
        current.append(index)
        acc += aspect

        rows_left = rows - len(groups) - 1
        items_left = count - index - 1

        if rows_left <= 0:
            continue

        if items_left == rows_left or (
            acc >= target and items_left > rows_left
        ):
            groups.append(current)
            current = []
            acc = 0.0

    if current:
        groups.append(current)

    if len(groups) != rows or any(not group for group in groups):
        return None

    return groups


def _join(nodes, kind):
    """Склеить список блоков слева направо (или сверху вниз)."""
    node = nodes[0]
    for other in nodes[1:]:
        node = _combine(kind, node, other)
    return node


def _row_candidates(aspects):
    """Раскладки «рядами»: работают при любом количестве фотографий."""
    count = len(aspects)
    max_rows = min(count, max(6, int(math.sqrt(count) * 2) + 1))

    roots = []
    seen = set()

    for rows in range(1, max_rows + 1):
        groups = _split_rows(aspects, rows)
        if groups is None:
            continue

        row_nodes = []
        for group in groups:
            leaves = [("leaf", i, None, aspects[i]) for i in group]
            row_nodes.append(_join(leaves, "h"))

        root = _join(row_nodes, "v")
        key = tuple(tuple(group) for group in groups)
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)

        # Тот же набор рядов, но развёрнутый: колонки вместо рядов
        column_nodes = []
        for group in groups:
            leaves = [("leaf", i, None, aspects[i]) for i in group]
            column_nodes.append(_join(leaves, "v"))
        roots.append(_join(column_nodes, "h"))

    return roots


def _candidate_roots(aspects):
    """Все варианты раскладки для заданного порядка фотографий."""
    roots = []

    if len(aspects) <= MAX_TREE_PHOTOS:
        roots.extend(_tree_candidates(aspects))

    roots.extend(_row_candidates(aspects))

    return roots


def _fill(node, x, y, width, height, out):
    """Разложить дерево в прямоугольники (в долях холста)."""
    kind = node[0]

    if kind == "leaf":
        out.append((node[1], x, y, width, height))
        return

    left = node[1]
    right = node[2]
    left_aspect = left[3]
    right_aspect = right[3]

    if kind == "h":
        # Общая высота: ширины делятся пропорционально пропорциям
        left_width = width * (left_aspect / (left_aspect + right_aspect))
        _fill(left, x, y, left_width, height, out)
        _fill(right, x + left_width, y, width - left_width, height, out)
        return

    # Общая ширина: высоты делятся пропорционально обратным пропорциям
    inv_left = 1.0 / left_aspect
    inv_right = 1.0 / right_aspect
    left_height = height * (inv_left / (inv_left + inv_right))
    _fill(left, x, y, width, left_height, out)
    _fill(right, x, y + left_height, width, height - left_height, out)


# ------------------------------------------------------------- оценка


def _make_plan(root, order, photos, index_map, photos_by_index):
    """Превратить дерево в план холста и оценить его качество."""
    aspect = root[3]
    if not (1e-6 < aspect < 1e6):
        return None

    fractions = []
    _fill(root, 0.0, 0.0, 1.0, 1.0, fractions)

    if len(fractions) != len(order):
        return None

    # Минимальная ширина холста, при которой ни одну фотографию
    # не приходится растягивать
    width = 0.0
    for position, _x, _y, cell_width, cell_height in fractions:
        if cell_width <= 0.0 or cell_height <= 0.0:
            return None
        photo_width = photos[order[position]][0]
        width = max(width, photo_width / cell_width)

    if width <= 0.0:
        return None

    height = width / aspect

    # Во сколько раз каждую фотографию пришлось увеличить
    upscales = []
    for position, _x, _y, cell_width, _cell_height in fractions:
        photo_width = photos[order[position]][0]
        upscales.append(math.log((cell_width * width) / photo_width))

    mean_upscale = sum(upscales) / len(upscales)
    peak_upscale = max(upscales)
    extreme = max(0.0, abs(math.log(aspect)) - math.log(COMFORT_ASPECT))

    score = (
        WEIGHT_MEAN_UPSCALE * mean_upscale
        + WEIGHT_PEAK_UPSCALE * peak_upscale
        + WEIGHT_EXTREME_ASPECT * extreme
    )

    width, height = _fit_limits_float(width, height)

    unit_cells = [
        (index_map[order[position]], x, y, cell_width, cell_height)
        for position, x, y, cell_width, cell_height in fractions
    ]

    return CanvasPlan(
        aspect, unit_cells, (width, height), score, photos_by_index
    )


def _pick_primary(best, photos):
    """Какой вариант показать первым: альбомный или книжный."""
    landscape = best.get("landscape")
    portrait = best.get("portrait")

    if landscape is None:
        return portrait
    if portrait is None:
        return landscape

    # Явно лучший вариант побеждает
    if abs(landscape.score - portrait.score) > 0.05:
        return landscape if landscape.score < portrait.score else portrait

    # Оценки равны — идём за большинством фотографий
    portrait_photos = sum(1 for width, height in photos if height > width)
    landscape_photos = sum(1 for width, height in photos if width > height)

    if portrait_photos > landscape_photos:
        return portrait
    if landscape_photos > portrait_photos:
        return landscape

    return landscape if landscape.score <= portrait.score else portrait


# ---------------------------------------------------------- округление


def _fit_limits_float(width, height):
    """Вписать размер в допустимые границы холста (без округления)."""
    scale = 1.0

    longest = max(width, height)
    if longest > MAX_SIDE:
        scale = MAX_SIDE / longest

    shortest = min(width, height) * scale
    if shortest < MIN_SIDE:
        grow = MIN_SIDE / shortest
        # Увеличиваем, только если это не выбьет длинную сторону
        if longest * scale * grow <= MAX_SIDE:
            scale *= grow

    return width * scale, height * scale


def _fit_limits(width, height):
    """То же для целых значений после округления."""
    width = max(1, int(round(width)))
    height = max(1, int(round(height)))

    if width <= MAX_SIDE and height <= MAX_SIDE:
        return width, height

    scale = MAX_SIDE / float(max(width, height))
    return (
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
    )


def _step_variants(width, height, aspect, step):
    """Два способа округлить пару сторон с сохранением пропорций.

    Одна сторона округляется до кратной шагу, вторая берётся из
    пропорций холста и тоже округляется: так соотношение сторон
    (а значит, и обрезка) страдает минимально.
    """
    step = max(1, int(step))

    first_width = max(step, int(round(width / step)) * step)
    first_height = max(step, int(round((first_width / aspect) / step)) * step)

    second_height = max(step, int(round(height / step)) * step)
    second_width = max(step, int(round((second_height * aspect) / step)) * step)

    return [
        (first_width, first_height),
        (second_width, second_height),
    ]


def _errors(candidate, width, height, aspect):
    """Отклонение варианта по размеру и по пропорциям."""
    size_error = max(
        abs(candidate[0] - width) / width,
        abs(candidate[1] - height) / height,
    )

    candidate_aspect = candidate[0] / float(candidate[1])
    aspect_error = abs(candidate_aspect / aspect - 1.0)

    return size_error, aspect_error


def _round_to_step(width, height, aspect, step):
    """Округлить размер холста до кратного шагу."""
    best = None
    best_error = None

    for candidate in _step_variants(width, height, aspect, step):
        size_error, aspect_error = _errors(candidate, width, height, aspect)
        # Пропорции важнее: их искажение превращается в обрезку
        error = size_error + 3.0 * aspect_error

        if best_error is None or error < best_error:
            best = candidate
            best_error = error

    return best


def _auto_round(width, height, aspect):
    """«Авто»: самый крупный круглый шаг, который почти ничего не стоит."""
    for step in AUTO_STEPS:
        for candidate in _step_variants(width, height, aspect, step):
            size_error, aspect_error = _errors(
                candidate, width, height, aspect
            )

            if (
                size_error <= AUTO_SIZE_TOLERANCE
                and aspect_error <= AUTO_ASPECT_TOLERANCE
            ):
                return candidate

    return _round_to_step(width, height, aspect, BASE_STEP)


def _pixel_cells(unit_cells, width, height):
    """Перевести доли холста в пиксельные прямоугольники.

    Границы соседних ячеек округляются одинаково, поэтому щелей
    и нахлёстов между слотами не возникает.
    """
    cells = []

    for index, x, y, cell_width, cell_height in unit_cells:
        left = int(round(x * width))
        top = int(round(y * height))
        right = int(round((x + cell_width) * width))
        bottom = int(round((y + cell_height) * height))

        cells.append(
            (
                index,
                left,
                top,
                max(1, right - left),
                max(1, bottom - top),
            )
        )

    return cells


def _max_crop(cells, photos):
    """Самая заметная обрезка среди ячеек (доля от стороны)."""
    worst = 0.0

    for index, _x, _y, width, height in cells:
        size = photos.get(index)
        if not size or width <= 0 or height <= 0:
            continue

        cell_aspect = width / float(height)
        photo_aspect = size[0] / float(size[1])

        if cell_aspect <= 0 or photo_aspect <= 0:
            continue

        ratio = min(cell_aspect, photo_aspect) / max(cell_aspect, photo_aspect)
        worst = max(worst, 1.0 - ratio)

    return worst


# =====================================================================
# Режим 2: подбор «с обрезкой» (больше вариантов)
# =====================================================================
#
# Первый режим (plan_canvas) требует, чтобы пропорции каждой ячейки
# ТОЧНО совпадали с пропорциями фотографии. Обрезки нет вовсе, но форма
# холста жёстко связана с набором фотографий: разных раскладок мало.
#
# Второй режим даёт каждой фотографии «бюджет уменьшения»: до
# CROP_TOLERANCE её стороны может уйти за край ячейки. Фотография
# по-прежнему заполняет ячейку целиком, лишнее срезается — именно так
# ведёт себя слот на холсте. Пропорции ячеек больше не привязаны к
# фотографиям, поэтому вариантов сетки становится намного больше, и их
# можно перебирать кнопкой «другая сетка».
#
# Первый режим этот код не затрагивает: ниже только новые функции.

# Сколько разрешено срезать с фотографии (доля стороны)
CROP_TOLERANCE = 0.2

# Сколько готовых вариантов держать для перебора кнопкой
VARIANT_LIMIT = 12

# Веса обрезки в оценке варианта: средняя по кадрам и худшая
WEIGHT_MEAN_CROP = 3.0
WEIGHT_PEAK_CROP = 1.5

# «Ровные» пропорции, к которым выгодно подтягивать кадры:
# на них получаются аккуратные сетки
NICE_ASPECTS = (
    0.5,
    9.0 / 16.0,
    2.0 / 3.0,
    3.0 / 4.0,
    4.0 / 5.0,
    1.0,
    5.0 / 4.0,
    4.0 / 3.0,
    3.0 / 2.0,
    16.0 / 9.0,
    2.0,
)

# Точность подписи раскладки при отсеве одинаковых вариантов
SIGNATURE_PRECISION = 2


def _crop_limits(aspect, tolerance):
    """Границы пропорций ячейки, при которых обрезка не больше бюджета."""
    tolerance = max(0.0, min(float(tolerance), 0.9))
    if tolerance <= 0.0:
        return aspect, aspect

    return aspect * (1.0 - tolerance), aspect / (1.0 - tolerance)


def _pull_aspect(aspect, target, tolerance):
    """Подтянуть пропорции ячейки к target, не выходя за бюджет."""
    low, high = _crop_limits(aspect, tolerance)
    return max(low, min(float(target), high))


def _nearest_nice(aspect):
    """Ближайшая «ровная» пропорция в логарифмической шкале."""
    return min(NICE_ASPECTS, key=lambda nice: abs(math.log(aspect / nice)))


def _aspect_variants(photos, tolerance):
    """Наборы пропорций ячеек: сами фотографии и подтянутые формы.

    Каждый набор целиком лежит внутри бюджета обрезки, поэтому любой из
    них даёт допустимую раскладку — отличается только форма сетки.
    Подтягивание делается сразу для всех кадров: это даёт разные семьи
    раскладок, не устраивая перебор по каждой фотографии отдельно.
    """
    exact = [photo[0] / float(photo[1]) for photo in photos]

    # Общая пропорция набора: к ней подтягиваем ради ровной сетки
    common = math.exp(sum(math.log(a) for a in exact) / len(exact))

    variants = [
        ("exact", list(exact)),
        ("square", [_pull_aspect(a, 1.0, tolerance) for a in exact]),
        ("common", [_pull_aspect(a, common, tolerance) for a in exact]),
        ("nice", [_pull_aspect(a, _nearest_nice(a), tolerance) for a in exact]),
        # Половина пути к квадрату: промежуточная форма ячеек
        ("half", [_pull_aspect(a, math.sqrt(a), tolerance) for a in exact]),
    ]

    unique = []
    seen = set()

    for name, aspects in variants:
        key = tuple(round(math.log(a), 4) for a in aspects)
        if key in seen:
            continue
        seen.add(key)
        unique.append((name, aspects))

    return unique


def _crop_candidate(root, order, photos, index_map, tolerance):
    """Оценить раскладку, в которой ячейка может не совпасть с кадром.

    Возвращает кортеж ``(оценка, пропорции холста, ячейки в долях,
    точный размер, худшая обрезка)`` или ``None``, если вариант не
    годится (вышел за бюджет обрезки либо вырожден).
    """
    aspect = root[3]
    if not (1e-6 < aspect < 1e6):
        return None

    fractions = []
    _fill(root, 0.0, 0.0, 1.0, 1.0, fractions)

    if len(fractions) != len(order):
        return None

    # Минимальный холст, при котором ни одну фотографию не приходится
    # растягивать. Кадр закрывает ячейку целиком, лишнее срезается,
    # поэтому достаточно совпадения по одной из сторон.
    width = 0.0
    for position, _x, _y, cell_width, cell_height in fractions:
        if cell_width <= 0.0 or cell_height <= 0.0:
            return None

        photo_width, photo_height = photos[order[position]]
        by_width = photo_width / cell_width
        by_height = (photo_height * aspect) / cell_height
        width = max(width, min(by_width, by_height))

    if width <= 0.0:
        return None

    crops = []
    upscales = []

    for position, _x, _y, cell_width, cell_height in fractions:
        photo_width, photo_height = photos[order[position]]
        photo_aspect = photo_width / float(photo_height)

        # Пропорции ячейки в пикселях: доли холста плюс его пропорции
        cell_aspect = (cell_width / cell_height) * aspect

        ratio = min(cell_aspect, photo_aspect) / max(cell_aspect, photo_aspect)
        crop = 1.0 - ratio
        if crop > tolerance + 1e-9:
            return None

        crops.append(crop)

        # Во сколько раз кадр пришлось увеличить, чтобы закрыть ячейку
        scale = max(
            (cell_width * width) / photo_width,
            (cell_height * width / aspect) / photo_height,
        )
        upscales.append(math.log(scale))

    mean_upscale = sum(upscales) / len(upscales)
    peak_upscale = max(upscales)
    mean_crop = sum(crops) / len(crops)
    peak_crop = max(crops)
    extreme = max(0.0, abs(math.log(aspect)) - math.log(COMFORT_ASPECT))

    # Первые три слагаемых — та же оценка, что и в первом режиме,
    # поэтому вариант без обрезки получает ровно прежний балл
    score = (
        WEIGHT_MEAN_UPSCALE * mean_upscale
        + WEIGHT_PEAK_UPSCALE * peak_upscale
        + WEIGHT_EXTREME_ASPECT * extreme
        + WEIGHT_MEAN_CROP * mean_crop
        + WEIGHT_PEAK_CROP * peak_crop
    )

    exact_size = _fit_limits_float(width, width / aspect)

    unit_cells = [
        (index_map[order[position]], x, y, cell_width, cell_height)
        for position, x, y, cell_width, cell_height in fractions
    ]

    return (score, aspect, unit_cells, exact_size, peak_crop)


def _layout_signature(aspect, unit_cells):
    """Подпись формы сетки: одинаковые раскладки в список не попадают.

    Округление до SIGNATURE_PRECISION знаков склеивает варианты, которые
    различаются на глаз неразличимо: перебор кнопкой должен каждый
    раз заметно менять сетку.
    """
    precision = SIGNATURE_PRECISION

    rects = sorted(
        (
            round(x, precision),
            round(y, precision),
            round(cell_width, precision),
            round(cell_height, precision),
        )
        for _index, x, y, cell_width, cell_height in unit_cells
    )

    return (round(math.log(aspect), precision), tuple(rects))


def plan_canvas_variants(
    sizes,
    rounding=ROUNDING_AUTO,
    tolerance=CROP_TOLERANCE,
    limit=VARIANT_LIMIT,
):
    """Ранжированный список раскладок для режима «с обрезкой».

    ``sizes`` — размеры фотографий в том же порядке, в каком они лежат
    в панели превью (как у ``plan_canvas``).

    Первый элемент списка — лучшая раскладка, дальше идут следующие
    по оценке: именно так работает кнопка «другая сетка». Одинаковые
    по форме сетки в список не попадают, поэтому каждое обновление даёт
    видимо другую расстановку. Пустой список — считать нечего.

    ``tolerance`` — бюджет обрезки на одно фото (0.2 — до 20% стороны),
    ``limit`` — сколько вариантов вернуть.
    """
    photos, index_map = _normalized(sizes)
    if not photos:
        return []

    photos_by_index = {
        index_map[pos]: photos[pos] for pos in range(len(photos))
    }

    tolerance = max(0.0, min(float(tolerance), 0.9))

    # Лучший кандидат на каждую форму сетки
    best = {}

    for _name, variant_aspects in _aspect_variants(photos, tolerance):
        for order in _orderings(photos):
            aspects = [variant_aspects[i] for i in order]

            for root in _candidate_roots(aspects):
                candidate = _crop_candidate(
                    root, order, photos, index_map, tolerance
                )
                if candidate is None:
                    continue

                signature = _layout_signature(candidate[1], candidate[2])
                current = best.get(signature)

                if current is None or candidate[0] < current[0]:
                    best[signature] = candidate

    if not best:
        return []

    ranked = sorted(best.values(), key=lambda candidate: candidate[0])
    limit = max(1, int(limit))

    plans = []
    for score, aspect, unit_cells, exact_size, _crop in ranked[:limit]:
        plan = CanvasPlan(
            aspect, unit_cells, exact_size, score, photos_by_index
        )

        if rounding != ROUNDING_AUTO:
            plan.set_rounding(rounding)

        plans.append(plan)

    # Кнопка ⇄ показывает лучший вариант другой ориентации
    landscape = next((plan for plan in plans if plan.aspect >= 1.0), None)
    portrait = next((plan for plan in plans if plan.aspect < 1.0), None)

    for plan in plans:
        other = portrait if plan.aspect >= 1.0 else landscape
        if other is not None and other is not plan:
            plan.alternative = other

    logger.info(
        "Auto canvas (crop mode): %d of %d layout(s) kept, crop budget %.0f%%",
        len(plans),
        len(best),
        tolerance * 100,
    )

    return plans
