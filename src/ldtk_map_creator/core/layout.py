"""配置器：照規則算出每個元素佔哪些格、錨點在哪（D-007）。只做計算，不碰場景地圖。

- 排列順序：先排固定位置的區域（照檔案順序）；再排指向的東西都排好了的元素，
  路徑優先，同種照檔案順序；entity 最後擺。
- 每個元素用 seed 加自己的名字當亂數種子；改一個元素，其他元素的亂數不受影響。
- 靠近別人的區域跟其他區域至少隔 1 格（彼此靠近的兩個除外）；固定位置的區域只檢查不重疊；
  跟路徑只要不重疊。
- 排不下一律丟 SceneError，訊息開頭是出錯的元素與欄位。
- 多個 level（D-009）：先排世界佈局（level 彼此不重疊），再一個 level 一個 level 排元素；
  元素只跟同一個 level 的元素互相擠。
"""

import random
from collections.abc import Iterable
from dataclasses import dataclass

from .errors import SceneError
from .rules import (
    AREA_ANCHORS,
    EDGE,
    AreaRule,
    Cell,
    LevelRule,
    PathRule,
    Rules,
    Side,
    area_anchor_offset,
    references,
)

Rect = tuple[int, int, int, int]  # x, y, 寬, 高（格）
Placeable = AreaRule | PathRule
_SIDE_NAMES: dict[str, str] = {"north": "北", "east": "東", "south": "南", "west": "西"}


@dataclass(frozen=True)
class Layout:
    """配置結果；元素的格都是它所在 level 的格座標。"""

    # 區域與路徑佔的格，照排列順序
    cells: dict[str, frozenset[Cell]]
    # 錨點，例如 `main_road.end`
    anchors: dict[str, Cell]
    # entity 擺在哪一格
    entities: dict[str, Cell]
    # 每個元素找位置時，被哪些元素擋過
    pushed_by: dict[str, frozenset[str]]
    # 每個 level 在世界上的位置與大小（格），照規則檔的順序
    levels: dict[str, Rect]
    # 每個元素（區域、路徑、entity）在哪個 level
    level_of: dict[str, str]


def placement_order(rules: Rules) -> tuple[Placeable, ...]:
    """區域與路徑的排列順序；只看規則，不看位置。"""
    fixed: list[Placeable] = [a for a in rules.areas if a.at is not None]
    rest: list[Placeable] = [*rules.paths, *(a for a in rules.areas if a.at is None)]
    order = list(fixed)
    placed = {e.name for e in fixed}
    while rest:
        # 從規則檔讀進來的不會成環；直接組出來的 Rules 可能會，照樣報成 SceneError
        ready = next((e for e in rest if {r for r, _ in references(e)} <= placed), None)
        if ready is None:
            stuck = rest[0]
            ref, field = next((r, f) for r, f in references(stuck) if r not in placed)
            known = {e.name for e in (*rules.areas, *rules.paths)}
            reason = "元素互相指向成環，排不出來" if ref in known else f"沒有叫 {ref} 的區域或路徑"
            raise SceneError(f"{stuck.name}.{field}：{reason}")
        rest.remove(ready)
        order.append(ready)
        placed.add(ready.name)
    return tuple(order)


def layout(rules: Rules) -> Layout:
    """照規則排出世界佈局與每個元素；排不下丟 SceneError。"""
    levels = place_levels(rules)
    cells: dict[str, frozenset[Cell]] = {}
    anchors: dict[str, Cell] = {}
    entities: dict[str, Cell] = {}
    pushed_by: dict[str, frozenset[str]] = {}
    level_of: dict[str, str] = {}
    order = placement_order(rules)
    for level in rules.levels:
        grid = _Grid(*level.size)
        for element in order:
            if rules.level_of(element).name != level.name:
                continue
            rng = random.Random(f"{rules.seed}:{element.name}")
            if isinstance(element, AreaRule):
                rect, pushed = _place_area(element, grid, rng)
                found = _rect_cells(rect)
                anchors |= _area_anchors(element.name, rect)
                grid.areas.add(element.name)
            else:
                found, start, end, pushed = _place_path(element, grid, rng)
                anchors[f"{element.name}.start"] = start
                anchors[f"{element.name}.end"] = end
            grid.claim(element.name, found)
            pushed_by[element.name] = frozenset(pushed)
            level_of[element.name] = level.name
        cells |= grid.cells

    taken: dict[tuple[str, Cell], str] = {}
    for entity in rules.entities:
        level = rules.level_of(entity).name
        if isinstance(entity.at, tuple):
            cell = entity.at
        elif entity.at in anchors:
            cell = anchors[entity.at]
        else:
            raise SceneError(f"{entity.name}.at：沒有錨點 {entity.at}")
        if (level, cell) in taken:
            raise SceneError(f"{entity.name}.at：跟 {taken[(level, cell)]} 擺在同一格 {cell}")
        taken[(level, cell)] = entity.name
        entities[entity.name] = cell
        pushed_by[entity.name] = frozenset()
        level_of[entity.name] = level
    return Layout(cells, anchors, entities, pushed_by, levels, level_of)


def place_levels(rules: Rules) -> dict[str, Rect]:
    """世界佈局（D-009）：每個 level 在世界上的位置與大小（格），彼此不重疊。

    先擺固定位置的（照檔案順序，第一個沒寫就在 (0, 0)），再照依賴順序擺靠近別人的：
    貼著對象那一邊、隔 gap 格，優先跟對象置中對齊，被別的 level 擋住就往旁邊挪。
    沒寫邊時用 seed 加 level 名稱的亂數挑。
    """
    placed: dict[str, Rect] = {}
    for level in rules.levels:
        if level.near is None:
            x, y = level.at or (0, 0)
            rect = (x, y, *level.size)
            hit = [n for n, r in placed.items() if _rects_overlap(rect, r)]
            if hit:
                raise SceneError(f"level.{level.name}.at：跟 level {hit[0]} 重疊")
            placed[level.name] = rect
    rest = [lv for lv in rules.levels if lv.near is not None]
    while rest:
        ready = next((lv for lv in rest if lv.near in placed), None)
        if ready is None:  # 從規則檔讀進來的不會成環；直接組出來的照樣報成 SceneError
            stuck = rest[0]
            raise SceneError(f"level.{stuck.name}.near：靠近的 level 不存在或互相靠近成環")
        rest.remove(ready)
        placed[ready.name] = _place_level_near(rules, ready, placed)
    return {lv.name: placed[lv.name] for lv in rules.levels}


def _place_level_near(rules: Rules, level: LevelRule, placed: dict[str, Rect]) -> Rect:
    assert level.near is not None
    tx, ty, tw, th = placed[level.near]
    w, h = level.size
    gap = level.gap
    rng = random.Random(f"{rules.seed}:level:{level.name}")
    every: list[Side] = ["north", "east", "south", "west"]
    sides: list[Side] = [level.side] if level.side else rng.sample(every, 4)
    for side in sides:
        if side in ("north", "south"):
            y = ty - gap - h if side == "north" else ty + th + gap
            middle = tx + (tw - w) // 2
            spots = [(x, y) for x in range(tx - w + 1, tx + tw)]
            spots.sort(key=lambda s: (abs(s[0] - middle), s[0]))
        else:
            x = tx - gap - w if side == "west" else tx + tw + gap
            middle = ty + (th - h) // 2
            spots = [(x, y) for y in range(ty - h + 1, ty + th)]
            spots.sort(key=lambda s: (abs(s[1] - middle), s[1]))
        for sx, sy in spots:
            rect = (sx, sy, w, h)
            if not any(_rects_overlap(rect, r) for r in placed.values()):
                return rect
    where = f"{level.near} 的{_SIDE_NAMES[level.side]}邊" if level.side else f"{level.near} 旁邊"
    raise SceneError(f"level.{level.name}.near：在 level {where}排不下 {w}x{h}（gap {gap}）")


def _rects_overlap(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


class _Grid:
    """level 上每一格被哪個元素佔了。"""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.owner: dict[Cell, str] = {}
        self.cells: dict[str, frozenset[Cell]] = {}
        self.areas: set[str] = set()

    def claim(self, name: str, cells: frozenset[Cell]) -> None:
        self.cells[name] = cells
        for cell in cells:
            self.owner[cell] = name

    def inside(self, rect: Rect) -> bool:
        x, y, w, h = rect
        return x >= 0 and y >= 0 and x + w <= self.width and y + h <= self.height

    def owners(self, cells: Iterable[Cell]) -> set[str]:
        return {self.owner[c] for c in cells if c in self.owner}

    def bbox(self, name: str) -> Rect:
        xs = [x for x, _ in self.cells[name]]
        ys = [y for _, y in self.cells[name]]
        return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def _rect_cells(rect: Rect) -> frozenset[Cell]:
    x, y, w, h = rect
    return frozenset((cx, cy) for cy in range(y, y + h) for cx in range(x, x + w))


def _area_anchors(name: str, rect: Rect) -> dict[str, Cell]:
    """區域的錨點：正中央，加上四邊在區域自己的格裡、那一邊的正中央（D-010）。"""
    x, y, w, h = rect
    found: dict[str, Cell] = {}
    for anchor in AREA_ANCHORS:
        dx, dy = area_anchor_offset((w, h), anchor)
        found[f"{name}.{anchor}"] = (x + dx, y + dy)
    return found


def _center(rect: Rect) -> Cell:
    return rect[0] + rect[2] // 2, rect[1] + rect[3] // 2


# 區域 ----------------------------------------------------------------------


def _place_area(area: AreaRule, grid: _Grid, rng: random.Random) -> tuple[Rect, set[str]]:
    w, h = area.size
    if area.at is not None:
        if area.at == "center":
            rect = ((grid.width - w) // 2, (grid.height - h) // 2, w, h)
        else:
            rect = (area.at[0], area.at[1], w, h)
        hit = grid.owners(_rect_cells(rect))
        if hit:
            raise SceneError(f"{area.name}.at：跟 {'、'.join(sorted(hit))} 重疊")
        return rect, set()

    if area.near is None:
        raise SceneError(f"{area.name}.at：at 與 near 要剛好寫一個")
    target = grid.cells[area.near]
    gap = area.gap
    every: list[Side] = ["north", "east", "south", "west"]
    sides: list[Side] = [area.side] if area.side else rng.sample(every, 4)
    pushed: set[str] = set()
    for side in sides:
        spots = _near_spots(target, (w, h), gap, side)
        rng.shuffle(spots)
        for x, y in spots:
            rect = (x, y, w, h)
            if not grid.inside(rect):
                continue
            blockers = _area_blockers(rect, grid, area.near)
            if blockers:
                pushed |= blockers
                continue
            return rect, pushed
    where = f"{area.near} 的{_SIDE_NAMES[area.side]}邊" if area.side else f"{area.near} 旁邊"
    raise SceneError(f"{area.name}.near：在 {where}排不下 {w}x{h}（gap {gap}）")


def _near_spots(target: frozenset[Cell], size: Cell, gap: int, side: Side) -> list[Cell]:
    """在對象的某一邊、剛好隔 gap 格的位置（左上角），照座標排好。

    看對象實際佔的格，不看外框：L 形路徑旁邊也排得進去。
    """
    w, h = size
    spots: set[Cell] = set()
    for cx, cy in target:
        if side == "north":
            spots.update((x, cy - gap - h) for x in range(cx - w + 1, cx + 1))
        elif side == "south":
            spots.update((x, cy + gap + 1) for x in range(cx - w + 1, cx + 1))
        elif side == "west":
            spots.update((cx - gap - w, y) for y in range(cy - h + 1, cy + 1))
        else:
            spots.update((cx + gap + 1, y) for y in range(cy - h + 1, cy + 1))

    def clear(x: int, y: int) -> bool:
        # 區域四周 gap 格內都不能碰到對象：L 形的對象可能從別的方向更靠近
        return not _rect_cells((x - gap, y - gap, w + 2 * gap, h + 2 * gap)) & target

    return sorted(s for s in spots if clear(*s))


def _area_blockers(rect: Rect, grid: _Grid, target: str) -> set[str]:
    """跟候選位置重疊的元素，加上外圍一格內的其他區域（靠近的對象不算）。"""
    x, y, w, h = rect
    blockers = grid.owners(_rect_cells(rect))
    ring = _rect_cells((x - 1, y - 1, w + 2, h + 2)) - _rect_cells(rect)
    blockers |= {o for o in grid.owners(ring) if o in grid.areas and o != target}
    return blockers


# 路徑 ----------------------------------------------------------------------


def _place_path(
    path: PathRule, grid: _Grid, rng: random.Random
) -> tuple[frozenset[Cell], Cell, Cell, set[str]]:
    if path.to.startswith(EDGE):
        return _edge_path(path, grid)
    return _link_path(path, grid, rng)


def _edge_path(path: PathRule, grid: _Grid) -> tuple[frozenset[Cell], Cell, Cell, set[str]]:
    """從起點佔的格筆直通到 level 的一邊；優先對齊起點的中央，被擋就往旁邊挪。

    座標換成「順著路」與「橫過路」兩軸：南北向的路順著 y，東西向的順著 x。
    """
    side = path.to.removeprefix(EDGE)
    vertical = side in ("north", "south")
    forward = side in ("south", "east")

    def pos(along: int, across: int) -> Cell:
        return (across, along) if vertical else (along, across)

    along_limit = grid.height if vertical else grid.width
    across_limit = grid.width if vertical else grid.height
    # 起點在橫軸每一格上，順軸佔到哪些位置
    by_across: dict[int, list[int]] = {}
    for x, y in grid.cells[path.start]:
        along, across = (y, x) if vertical else (x, y)
        by_across.setdefault(across, []).append(along)
    low, high = min(by_across), max(by_across)
    middle = (low + high + 1) / 2
    wd = path.width
    offsets = range(max(0, low - wd + 1), min(across_limit - wd, high) + 1)
    pushed: set[str] = set()
    room = False
    for offset in sorted(offsets, key=lambda o: (abs(o + wd / 2 - middle), o)):
        alongs = [a for c in range(offset, offset + wd) for a in by_across.get(c, [])]
        if not alongs:
            continue
        begin, stop = (max(alongs) + 1, along_limit) if forward else (0, min(alongs))
        if begin >= stop:
            continue
        room = True
        cells = frozenset(pos(a, c) for a in range(begin, stop) for c in range(offset, offset + wd))
        blockers = grid.owners(cells)
        if blockers:
            pushed |= blockers
            continue
        line = offset + wd // 2
        near, far = (begin, stop - 1) if forward else (stop - 1, begin)
        return cells, pos(near, line), pos(far, line), pushed
    edge = _SIDE_NAMES[side]
    if not room:
        raise SceneError(f"{path.name}.to：{path.start} 已經貼著 level 的{edge}邊")
    blocked = "、".join(sorted(pushed))
    raise SceneError(f"{path.name}.to：從 {path.start} 到{edge}邊的路都被 {blocked} 擋住")


def _link_path(
    path: PathRule, grid: _Grid, rng: random.Random
) -> tuple[frozenset[Cell], Cell, Cell, set[str]]:
    """L 形：從起點的中心走到終點的中心，先橫後直或先直後橫，用自己的亂數決定先試哪一種。"""
    a = _middle_cell(grid, path.start)
    b = _middle_cell(grid, path.to)
    ends = grid.cells[path.start] | grid.cells[path.to]
    shapes = ["hv", "vh"]
    rng.shuffle(shapes)
    pushed: set[str] = set()
    for shape in shapes:
        segment = _between(_l_line(a, b, shape), grid.cells[path.start], grid.cells[path.to])
        if not segment:
            continue  # 這種形狀整條落在兩端裡，換另一種
        # 只加寬兩端之間的那段中線，路才不會繞到起點、終點的背後
        cells = frozenset(c for c in _thicken(segment, path.width, grid) if c not in ends)
        blockers = grid.owners(cells)
        if blockers:
            pushed |= blockers
            continue
        return cells, segment[0], segment[-1], pushed
    if not pushed:
        raise SceneError(f"{path.name}.to：{path.start} 跟 {path.to} 已經相接，路徑沒有格")
    blocked = "、".join(sorted(pushed))
    raise SceneError(f"{path.name}.to：從 {path.start} 到 {path.to} 的路都被 {blocked} 擋住")


def _between(line: list[Cell], start: frozenset[Cell], end: frozenset[Cell]) -> list[Cell]:
    """中線上最後一次離開起點之後、第一次碰到終點之前的那一段。

    中線離開 L 形的對象又繞回來時，只留真的連起兩端的那段，不留殘枝。
    """
    stop = next((i for i, c in enumerate(line) if c in end), len(line))
    begin = max((i for i in range(stop) if line[i] in start), default=-1) + 1
    return line[begin:stop]


def _middle_cell(grid: _Grid, name: str) -> Cell:
    """元素自己的格裡，離外框中心最近的一格；L 形路徑的外框中心可能不在路上。"""
    cx, cy = _center(grid.bbox(name))
    return min(grid.cells[name], key=lambda c: (abs(c[0] - cx) + abs(c[1] - cy), c[1], c[0]))


def _l_line(a: Cell, b: Cell, shape: str) -> list[Cell]:
    """L 形的中線，從 a 走到 b。"""
    (ax, ay), (bx, by) = a, b
    corner = (bx, ay) if shape == "hv" else (ax, by)
    return _segment(a, corner) + _segment(corner, b)[1:]


def _segment(a: Cell, b: Cell) -> list[Cell]:
    (ax, ay), (bx, by) = a, b
    if ay == by:
        step = 1 if bx >= ax else -1
        return [(x, ay) for x in range(ax, bx + step, step)]
    step = 1 if by >= ay else -1
    return [(ax, y) for y in range(ay, by + step, step)]


def _thicken(line: list[Cell], width: int, grid: _Grid) -> set[Cell]:
    """中線加寬成路：每一點擴成 width x width 的方塊，切掉 level 外的格。"""
    offset = width // 2
    cells: set[Cell] = set()
    for x, y in line:
        for dy in range(width):
            for dx in range(width):
                cx, cy = x - offset + dx, y - offset + dy
                if 0 <= cx < grid.width and 0 <= cy < grid.height:
                    cells.add((cx, cy))
    return cells
