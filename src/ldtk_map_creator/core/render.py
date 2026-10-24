"""把 level render 成 PNG 預覽（北極星：每一步看得到成果）。

畫法固定，讓同一份場景地圖在同一個環境每次 render 出一樣的位元組：
- 背景是 level 的背景色。
- 看得見的 IntGrid 從最下層畫到最上層，每個值用定義的顏色與圖層透明度；0 不畫。
- entity 畫成定義顏色的方塊（用 entity 自己的尺寸），名稱寫在方塊上方；貼著上緣時寫在下方。
- 格線畫在 IntGrid 上面；上方與左邊的格子座標最後畫，方便在對話裡指定位置。
- 都加上圖層的像素偏移，跟 LDtk 顯示的位置一致。
- 給了元素名稱時（從規則生成的 level），名稱寫在元素的位置上，加深色描邊，畫在格線上、
  entity 下：不被格線或顏色蓋掉，也不蓋住 entity；名稱整個留在 level 範圍內、彼此不重疊。

render 只讀不改：不呼叫 refresh_derived，所有值從定義與本體欄位算。
"""

import io
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageFont

from .derived import cell_count, level_bg_color
from .errors import SceneError
from .files import atomic_write
from .model import EntityDef, EntityInstance, LayerDef, LayerInstance, Level, Project
from .scene import SceneMap

MARGIN = 28
"""上方與左邊留給格子座標的像素。"""

_GRID_LINE = (255, 255, 255, 40)
_LABEL = (230, 230, 230, 255)
_FRAME = (24, 26, 34, 255)
_HEX = re.compile(r"#?[0-9A-Fa-f]{6}")


def _rgb(color: str, opacity: float = 1.0) -> tuple[int, int, int, int]:
    if not _HEX.fullmatch(color):
        raise SceneError(f"顏色格式不對：{color}")
    value = color.lstrip("#")
    alpha = round(max(0.0, min(1.0, opacity)) * 255)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)


def image_size(level: Level, scale: int) -> tuple[int, int]:
    """PNG 的尺寸：座標邊框加上 level 像素乘以倍率（RENDER-1）。"""
    return MARGIN + level["pxWid"] * scale, MARGIN + level["pxHei"] * scale


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Pillow 10.1 起內建可縮放的預設字型（pyproject 鎖 pillow>=10.1）
    return ImageFont.load_default(size)


def _offset(layer: LayerInstance, ldef: LayerDef, scale: int) -> tuple[int, int]:
    return (
        MARGIN + (layer["pxOffsetX"] + ldef["pxOffsetX"]) * scale,
        MARGIN + (layer["pxOffsetY"] + ldef["pxOffsetY"]) * scale,
    )


def _label_grid(
    project: Project, level: Level, layer_defs: dict[int, LayerDef], scale: int
) -> tuple[int, int, int]:
    """格線與座標跟著看得見的最上層 IntGrid：格子大小與偏移都一樣，跟 paint_cells 的格座標一致。

    回傳（格子大小, 原點 x, 原點 y）；沒有看得見的 IntGrid 才用預設格子大小、無偏移。
    """
    for layer in level["layerInstances"] or []:
        ldef = layer_defs.get(layer["layerDefUid"])
        if ldef is not None and ldef["type"] == "IntGrid" and layer["visible"]:
            return (ldef["gridSize"], *_offset(layer, ldef, scale))
    return project["defaultGridSize"], MARGIN, MARGIN


Label = tuple[str, float, float]
"""元素名稱與寫在哪裡：格座標，可以有小數（例如區域的正中央）。"""


def render_level(
    scene: SceneMap, level_id: str, *, scale: int = 2, labels: Sequence[Label] = ()
) -> Image.Image:
    """render 一個 level；`scale` 是每個 LDtk 像素放大幾倍。不改動場景地圖。

    `labels` 是要寫上去的元素名稱；沒給就跟沒有規則檔的場景地圖一樣，不寫。
    """
    if scale < 1:
        raise SceneError(f"倍率至少 1：{scale}")
    project = scene.project
    level = scene.level(level_id)
    layer_defs = {d["uid"]: d for d in project["defs"]["layers"]}
    entity_defs = {d["uid"]: d for d in project["defs"]["entities"]}
    # RGB 底圖：半透明的圖層才會跟下面的顏色混合；存檔前再轉成 RGBA
    image = Image.new("RGB", image_size(level, scale), _FRAME[:3])
    draw = ImageDraw.Draw(image, "RGBA")
    background = level_bg_color(project, level)
    right = MARGIN + level["pxWid"] * scale - 1
    bottom = MARGIN + level["pxHei"] * scale - 1
    draw.rectangle((MARGIN, MARGIN, right, bottom), fill=_rgb(background))
    layers = level["layerInstances"] or []
    for layer in layers:
        if layer["layerDefUid"] not in layer_defs:
            raise SceneError(f"圖層 {layer['iid']} 的定義不見了")
    visible = [ly for ly in layers if ly["visible"]]
    # LDtk 的圖層清單第一個在最上面，所以倒著畫
    for layer in reversed(visible):
        ldef = layer_defs[layer["layerDefUid"]]
        if ldef["type"] == "IntGrid":
            _draw_intgrid(draw, level, layer, ldef, scale)
    # 格線與元素名稱畫在 IntGrid 上、entity 下：名稱不被格線蓋掉，也不蓋住 entity
    origin = _label_grid(project, level, layer_defs, scale)
    _draw_grid(draw, level, origin, scale, numbers=False)
    boxes = _entity_boxes(level, layer_defs, entity_defs, scale)
    _draw_names(draw, _place_names(level, origin, labels, scale, _keep_out(boxes, scale)), scale)
    # 方塊外的名稱先寫、再畫方塊：名稱可能比一格寬，不能蓋住別的 entity 方塊；
    # 寫在方塊裡的名稱最後寫，才不會被自己的方塊蓋掉
    entity_labels = [
        (edef["identifier"], _label_place(box, image.size[1], scale)) for edef, box in boxes
    ]
    font = _font(_label_size(scale))
    for name, (xy, anchor, inside) in entity_labels:
        if not inside:
            draw.text(xy, name, fill=_LABEL, font=font, anchor=anchor)
    for edef, box in boxes:
        draw.rectangle(box, fill=_rgb(edef["color"]), outline=_LABEL)
    for name, (xy, anchor, inside) in entity_labels:
        if inside:
            draw.text(xy, name, fill=_LABEL, font=font, anchor=anchor)
    # 格子座標最後畫，entity 跑進邊框也蓋不到
    _draw_grid(draw, level, origin, scale, lines=False)
    return image.convert("RGBA")


NameBox = tuple[str, int, tuple[int, int], tuple[int, int, int, int]]
"""一個要寫的元素名稱：（名稱, 字級, 中心點, 外框）；外框是像素 (左, 上, 右, 下)，右下不含。"""

_SMALLEST_NAME = 6


def name_boxes(
    scene: SceneMap, level_id: str, labels: Sequence[Label], *, scale: int = 2
) -> list[NameBox]:
    """元素名稱實際寫在哪裡、多大；render 照這份畫。放不下的名稱不在裡面。"""
    project = scene.project
    level = scene.level(level_id)
    layer_defs = {d["uid"]: d for d in project["defs"]["layers"]}
    entity_defs = {d["uid"]: d for d in project["defs"]["entities"]}
    keep_out = _keep_out(_entity_boxes(level, layer_defs, entity_defs, scale), scale)
    origin = _label_grid(project, level, layer_defs, scale)
    return _place_names(level, origin, labels, scale, keep_out)


Box = tuple[int, int, int, int]


def _entity_boxes(
    level: Level, layer_defs: dict[int, LayerDef], entity_defs: dict[int, EntityDef], scale: int
) -> list[tuple[EntityDef, Box]]:
    """看得見的 entity 方塊（最下層先）。"""
    boxes: list[tuple[EntityDef, Box]] = []
    for layer in reversed([ly for ly in level["layerInstances"] or [] if ly["visible"]]):
        ldef = layer_defs[layer["layerDefUid"]]
        for entity in layer["entityInstances"]:
            edef = entity_defs.get(entity["defUid"])
            if edef is None:
                raise SceneError(f"entity {entity['iid']} 的定義不見了")
            boxes.append((edef, _entity_box(layer, ldef, edef, entity, scale)))
    return boxes


def _keep_out(boxes: list[tuple[EntityDef, Box]], scale: int) -> list[Box]:
    """元素名稱要避開的範圍：entity 方塊，連同它上方寫 entity 名稱的那一條。"""
    label = _label_size(scale) + 2
    return [(left, top - label, right, bottom) for _, (left, top, right, bottom) in boxes]


def _place_names(
    level: Level,
    origin: tuple[int, int, int],
    labels: Sequence[Label],
    scale: int,
    keep_out: Sequence[Box] = (),
) -> list[NameBox]:
    """以標示位置為中心擺名稱，整個留在 level 裡、彼此不重疊。

    靠邊放不下就往內推；會蓋到 entity 方塊（`keep_out`）就上下挪一格、兩格；
    比 level 還寬、還高或會撞到先擺的名稱，就縮小字；縮到最小還放不下就不寫。
    怎麼挪都會蓋到 entity 時，照原位寫（寧可被蓋一部分，也不丟掉名稱）。
    照 labels 的順序擺，先到先得。
    """
    grid, origin_x, origin_y = origin
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    stroke = _name_stroke(scale)
    right = MARGIN + level["pxWid"] * scale  # 不含
    bottom = MARGIN + level["pxHei"] * scale
    placed: list[NameBox] = []
    for name, x, y in labels:
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        for size in range(_name_size(scale), _SMALLEST_NAME - 1, -1):
            font = _font(size)
            left, top, end, low = measure.textbbox(
                (0, 0), name, font=font, anchor="mm", stroke_width=stroke
            )
            if end - left > right - MARGIN or low - top > bottom - MARGIN:
                continue
            cx = origin_x + x * grid * scale
            cx = round(min(max(cx, MARGIN - left), right - end))
            spots: list[tuple[int, Box]] = []
            for shift in (0, -1, 1, -2, 2):  # 以格為單位上下挪
                cy = origin_y + (y + shift) * grid * scale
                cy = round(min(max(cy, MARGIN - top), bottom - low))
                box = (
                    max(MARGIN, math.floor(cx + left)),
                    max(MARGIN, math.floor(cy + top)),
                    min(right, math.ceil(cx + end)),
                    min(bottom, math.ceil(cy + low)),
                )
                if not any(_overlap(box, other[3]) for other in placed):
                    spots.append((cy, box))
            if not spots:
                continue
            clear = [s for s in spots if not any(_overlap(s[1], k) for k in keep_out)]
            cy, box = (clear or spots)[0]
            placed.append((name, size, (cx, cy), box))
            break
    return placed


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _draw_names(draw: ImageDraw.ImageDraw, boxes: list[NameBox], scale: int) -> None:
    """淺色字加深色描邊。"""
    stroke = _name_stroke(scale)
    for name, size, xy, _ in boxes:
        font = _font(size)
        draw.text(
            xy, name, fill=_LABEL, font=font, anchor="mm", stroke_width=stroke, stroke_fill=_FRAME
        )


def _name_size(scale: int) -> int:
    """元素名稱的字級。"""
    return max(12, 7 * scale)


def _name_stroke(scale: int) -> int:
    """元素名稱描邊的寬度。"""
    return max(2, scale)


def _draw_intgrid(
    draw: ImageDraw.ImageDraw, level: Level, layer: LayerInstance, ldef: LayerDef, scale: int
) -> None:
    grid = ldef["gridSize"]
    cols = cell_count(level["pxWid"], grid)
    rows = cell_count(level["pxHei"], grid)
    csv = layer["intGridCsv"]
    if len(csv) != cols * rows:
        raise SceneError(f"圖層 {ldef['identifier']} 有 {len(csv)} 格，尺寸是 {cols}x{rows}")
    opacity = ldef["displayOpacity"]
    colors = {v["value"]: _rgb(v["color"], opacity) for v in ldef["intGridValues"]}
    left, top = _offset(layer, ldef, scale)
    cell = grid * scale
    for index, value in enumerate(csv):
        if value == 0:
            continue
        color = colors.get(value)
        if color is None:
            raise SceneError(f"圖層 {ldef['identifier']} 的值 {value} 沒有定義")
        x = left + (index % cols) * cell
        y = top + (index // cols) * cell
        draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=color)


def _entity_box(
    layer: LayerInstance,
    ldef: LayerDef,
    edef: EntityDef,
    entity: EntityInstance,
    scale: int,
) -> tuple[int, int, int, int]:
    width, height = entity["width"], entity["height"]
    if width < 1 or height < 1:
        raise SceneError(f"entity {entity['iid']} 的尺寸不對：{width}x{height}")
    origin_x, origin_y = _offset(layer, ldef, scale)
    left = origin_x + round((entity["px"][0] - edef["pivotX"] * width) * scale)
    top = origin_y + round((entity["px"][1] - edef["pivotY"] * height) * scale)
    return left, top, left + width * scale - 1, top + height * scale - 1


def _label_size(scale: int) -> int:
    return max(10, 6 * scale)


def _label_place(
    box: tuple[int, int, int, int], image_height: int, scale: int
) -> tuple[tuple[int, int], str, bool]:
    """名稱寫在方塊上方；上方會蓋到欄座標就寫在下方；下方也超出圖就寫在方塊裡。

    回傳（位置, 錨點, 是否寫在方塊裡）。
    """
    left, top, _, bottom = box
    size = _label_size(scale)
    if top - 2 - size >= MARGIN:
        return (left, top - 2), "lb", False
    if bottom + 2 + size <= image_height:
        return (left, bottom + 2), "lt", False
    return (left + 2, top + 2), "lt", True


def _draw_grid(
    draw: ImageDraw.ImageDraw,
    level: Level,
    origin: tuple[int, int, int],
    scale: int,
    *,
    lines: bool = True,
    numbers: bool = True,
) -> None:
    """格線（`lines`）與上方、左邊的格子座標（`numbers`）。"""
    grid, origin_x, origin_y = origin
    cols = cell_count(level["pxWid"], grid)
    rows = cell_count(level["pxHei"], grid)
    cell = grid * scale
    right = MARGIN + level["pxWid"] * scale - 1
    bottom = MARGIN + level["pxHei"] * scale - 1
    font = _font(12)
    for cx in range(cols + 1):
        x = min(origin_x + cx * cell, right)
        if lines:
            draw.line((x, MARGIN, x, bottom), fill=_GRID_LINE)
        if numbers and cx < cols:
            # 最後一欄可能不滿一格，標籤放在看得見的那一段中間
            middle = x + (min(x + cell, right + 1) - x) / 2
            draw.text((middle, MARGIN / 2), str(cx), fill=_LABEL, font=font, anchor="mm")
    for cy in range(rows + 1):
        y = min(origin_y + cy * cell, bottom)
        if lines:
            draw.line((MARGIN, y, right, y), fill=_GRID_LINE)
        if numbers and cy < rows:
            middle = y + (min(y + cell, bottom + 1) - y) / 2
            draw.text((MARGIN / 2, middle), str(cy), fill=_LABEL, font=font, anchor="mm")


def preview_path(scene: SceneMap, level_id: str) -> Path:
    """預覽圖的位置：場景地圖旁的 `previews/<主檔名>/<level>.png`（D-002）。"""
    return scene.path.parent / "previews" / scene.path.stem / f"{level_id}.png"


def write_preview(scene: SceneMap, level_id: str, image: Image.Image) -> Path:
    """把 render 好的圖存成那個 level 的預覽圖，回傳路徑。"""
    path = preview_path(scene, level_id)
    _write_png(path, image)
    return path


# 世界總覽 ------------------------------------------------------------------

WORLD_PREVIEW = "world-overview"
"""世界總覽的檔名（不含副檔名）；有 `-`，不會跟 level 的預覽圖撞名（level 名稱是識別字）。"""

WORLD_MAX = 1600
"""世界總覽長邊最多幾個像素；世界比這大就縮小。"""

_LINK = (255, 214, 0, 255)
_WORLD_STROKE = (0, 0, 0, 255)  # 跟總覽的底色分開，名稱蓋到的地方不會看起來像空白
_WORLD_NAME = 12


@dataclass(frozen=True)
class WorldView:
    """世界總覽的版面：倍率、世界原點（像素）、每個 level 在總覽裡的位置與大小。"""

    scale: float
    origin: tuple[int, int]
    rects: dict[str, tuple[int, int, int, int]]

    def point(self, x: float, y: float) -> tuple[int, int]:
        """世界座標（像素）換成總覽裡的位置；level 的外框與連線都用這一個換算。"""
        return (
            MARGIN + round((x - self.origin[0]) * self.scale),
            MARGIN + round((y - self.origin[1]) * self.scale),
        )


def world_positions(scene: SceneMap) -> dict[str, tuple[int, int]]:
    """每個 level 的世界座標（像素）。

    線性排法（LinearHorizontal、LinearVertical）的 worldX、worldY 是 -1，照 level 的順序接著排，
    跟 LDtk 顯示的一樣；其他排法照 worldX、worldY。
    """
    project = scene.project
    layout = project["worldLayout"]
    positions: dict[str, tuple[int, int]] = {}
    offset = 0
    for level in project["levels"]:
        if layout == "LinearHorizontal":
            positions[level["identifier"]] = (offset, 0)
            offset += level["pxWid"]
        elif layout == "LinearVertical":
            positions[level["identifier"]] = (0, offset)
            offset += level["pxHei"]
        else:
            positions[level["identifier"]] = (level["worldX"], level["worldY"])
    return positions


def world_view(scene: SceneMap) -> WorldView:
    """照 level 的世界座標等比例縮小，左上角留 MARGIN；長邊不超過 WORLD_MAX。"""
    levels = scene.project["levels"]
    if not levels:
        raise SceneError("場景地圖沒有 level，畫不出世界總覽")
    at = world_positions(scene)
    left = min(at[lv["identifier"]][0] for lv in levels)
    top = min(at[lv["identifier"]][1] for lv in levels)
    right = max(at[lv["identifier"]][0] + lv["pxWid"] for lv in levels)
    bottom = max(at[lv["identifier"]][1] + lv["pxHei"] for lv in levels)
    scale = min(1.0, WORLD_MAX / max(1, right - left, bottom - top))
    view = WorldView(scale, (left, top), {})
    for level in levels:
        x, y = view.point(*at[level["identifier"]])
        size = (max(1, round(level["pxWid"] * scale)), max(1, round(level["pxHei"] * scale)))
        view.rects[level["identifier"]] = (x, y, *size)
    return view


def world_links(scene: SceneMap) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """EntityRef 從擁有者中心到目標中心的世界座標（像素）；陣列也算，互指的一對只算一次。"""
    project = scene.project
    at = world_positions(scene)
    layer_defs = {d["uid"]: d for d in project["defs"]["layers"]}
    entity_defs = {d["uid"]: d for d in project["defs"]["entities"]}
    centers: dict[str, tuple[float, float]] = {}
    pairs: set[tuple[str, str]] = set()
    for level in project["levels"]:
        wx, wy = at[level["identifier"]]
        for layer in level["layerInstances"] or []:
            ldef = layer_defs.get(layer["layerDefUid"])
            # 跟 render_level 一樣：圖層實例的偏移加上圖層定義的偏移
            ox = layer["pxOffsetX"] + (ldef["pxOffsetX"] if ldef else 0)
            oy = layer["pxOffsetY"] + (ldef["pxOffsetY"] if ldef else 0)
            for entity in layer["entityInstances"]:
                edef = entity_defs.get(entity["defUid"])
                pivot_x, pivot_y = (edef["pivotX"], edef["pivotY"]) if edef else (0.0, 0.0)
                centers[entity["iid"]] = (
                    wx + ox + entity["px"][0] + (0.5 - pivot_x) * entity["width"],
                    wy + oy + entity["px"][1] + (0.5 - pivot_y) * entity["height"],
                )
                for target in _ref_targets(entity):
                    if target != entity["iid"]:
                        first, second = sorted((entity["iid"], target))
                        pairs.add((first, second))
    return [(centers[a], centers[b]) for a, b in sorted(pairs) if a in centers and b in centers]


def _ref_targets(entity: EntityInstance) -> list[str]:
    found: list[str] = []
    for field in entity["fieldInstances"]:
        kind = cast(dict[str, object], field).get("__type")
        if kind not in ("EntityRef", "Array<EntityRef>"):
            continue
        value = cast(dict[str, object], field).get("__value")
        for item in cast(list[object], value) if isinstance(value, list) else [value]:
            if isinstance(item, dict):
                target = cast(dict[str, object], item).get("entityIid")
                if isinstance(target, str):
                    found.append(target)
    return found


def render_world(scene: SceneMap) -> Image.Image:
    """世界總覽（D-002）：每個 level 照世界座標畫在一起、標出名稱，EntityRef 兩端之間畫線。"""
    view = world_view(scene)
    levels = scene.project["levels"]
    width = max(x + w for x, _, w, _ in view.rects.values()) + MARGIN
    height = max(y + h for _, y, _, h in view.rects.values()) + MARGIN
    image = Image.new("RGB", (width, height), _FRAME[:3])
    draw = ImageDraw.Draw(image, "RGBA")
    for level in levels:
        # 直接用總覽的倍率畫，不先照原尺寸 render 再縮小：很大的世界也只用到總覽那麼多記憶體
        _draw_level_small(image, draw, scene, level, view)
        x, y, w, h = view.rects[level["identifier"]]
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=_LABEL)
    for start, end in world_links(scene):
        points = [view.point(*start), view.point(*end)]
        draw.line(points, fill=_LINK, width=2)
        for px, py in points:
            draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=_LINK)
    for level in levels:  # 名稱最後寫，不被線蓋掉；level 太小就縮字，再放不下就不寫
        x, y, w, h = view.rects[level["identifier"]]
        for size in range(_WORLD_NAME, _SMALLEST_NAME - 1, -1):
            font = _font(size)
            box = draw.textbbox((0, 0), level["identifier"], font=font, stroke_width=2)
            if box[2] - box[0] + 6 <= w and box[3] - box[1] + 4 <= h:
                draw.text(
                    (x + 3, y + 2),
                    level["identifier"],
                    fill=_LABEL,
                    font=font,
                    anchor="lt",
                    stroke_width=2,
                    stroke_fill=_WORLD_STROKE,
                )
                break
    return image.convert("RGBA")


def _draw_level_small(
    image: Image.Image, draw: ImageDraw.ImageDraw, scene: SceneMap, level: Level, view: WorldView
) -> None:
    """總覽裡的一個 level：背景色、看得見的 IntGrid（最下層先畫）、entity 方塊；不畫格線。"""
    project = scene.project
    x, y, w, h = view.rects[level["identifier"]]
    scale = view.scale
    draw.rectangle((x, y, x + w - 1, y + h - 1), fill=_rgb(level_bg_color(project, level)))
    layer_defs = {d["uid"]: d for d in project["defs"]["layers"]}
    entity_defs = {d["uid"]: d for d in project["defs"]["entities"]}
    visible = [ly for ly in level["layerInstances"] or [] if ly["visible"]]

    def box(px: float, py: float, pw: float, ph: float) -> tuple[int, int, int, int]:
        # level 裡的像素範圍換成總覽的格；至少一個像素，看得見
        left, top = x + int(px * scale), y + int(py * scale)
        right = max(left, x + int((px + pw) * scale) - 1)
        bottom = max(top, y + int((py + ph) * scale) - 1)
        return left, top, min(right, x + w - 1), min(bottom, y + h - 1)

    for layer in reversed(visible):
        ldef = layer_defs.get(layer["layerDefUid"])
        if ldef is None or ldef["type"] != "IntGrid":
            continue
        grid = ldef["gridSize"]
        cols = cell_count(level["pxWid"], grid)
        colors = {v["value"]: _rgb(v["color"]) for v in ldef["intGridValues"]}
        ox = layer["pxOffsetX"] + ldef["pxOffsetX"]
        oy = layer["pxOffsetY"] + ldef["pxOffsetY"]
        # 縮小時好幾格落在同一個像素：整層先不透明地畫在一張透明圖上，再照圖層透明度只混一次
        # 暫存圖只跟這個 level 在總覽裡一樣大
        sheet = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sheet_draw = ImageDraw.Draw(sheet)
        for index, value in enumerate(layer["intGridCsv"]):
            color = colors.get(value)
            if value == 0 or color is None:
                continue
            cx, cy = index % cols, index // cols
            left, top, right, bottom = box(ox + cx * grid, oy + cy * grid, grid, grid)
            sheet_draw.rectangle((left - x, top - y, right - x, bottom - y), fill=color)
        opacity = min(1.0, max(0.0, ldef["displayOpacity"]))  # 跟 _rgb 一樣限制在 0～1
        table = [round(a * opacity) for a in range(256)]
        # Pillow 的 stub 在 point 的一個 overload 帶 Unknown，只在這一行放寬
        mask = sheet.getchannel("A").point(table)  # pyright: ignore[reportUnknownMemberType]
        image.paste(sheet, (x, y), mask)
    for layer in reversed(visible):
        ldef = layer_defs.get(layer["layerDefUid"])
        ox = layer["pxOffsetX"] + (ldef["pxOffsetX"] if ldef else 0)
        oy = layer["pxOffsetY"] + (ldef["pxOffsetY"] if ldef else 0)
        for entity in layer["entityInstances"]:
            edef = entity_defs.get(entity["defUid"])
            if edef is None:
                continue
            ex = ox + entity["px"][0] - edef["pivotX"] * entity["width"]
            ey = oy + entity["px"][1] - edef["pivotY"] * entity["height"]
            draw.rectangle(box(ex, ey, entity["width"], entity["height"]), fill=_rgb(edef["color"]))


def world_preview_path(scene: SceneMap) -> Path:
    """世界總覽的位置：`previews/<主檔名>/world-overview.png`（D-002）。"""
    return scene.path.parent / "previews" / scene.path.stem / f"{WORLD_PREVIEW}.png"


def write_world_preview(scene: SceneMap, image: Image.Image) -> Path:
    """把 render 好的世界總覽存起來，回傳路徑。"""
    path = world_preview_path(scene)
    _write_png(path, image)
    return path


def _write_png(path: Path, image: Image.Image) -> None:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    atomic_write(path, buffer.getvalue())
