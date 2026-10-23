"""把 level render 成 PNG 預覽（北極星：每一步看得到成果）。

畫法固定，讓同一份場景地圖在同一個環境每次 render 出一樣的位元組：
- 背景是 level 的背景色。
- 看得見的 IntGrid 從最下層畫到最上層，每個值用定義的顏色與圖層透明度；0 不畫。
- entity 畫成定義顏色的方塊（用 entity 自己的尺寸），名稱寫在方塊上方；貼著上緣時寫在下方。
- 格線畫在 IntGrid 上面，加上方與左邊的格子座標，方便在對話裡指定位置。
- 都加上圖層的像素偏移，跟 LDtk 顯示的位置一致。
- 給了元素名稱時（從規則生成的 level），名稱寫在元素的位置上，加深色描邊，畫在格線上、
  entity 下：不被格線或顏色蓋掉，也不蓋住 entity；名稱整個留在 level 範圍內。

render 只讀不改：不呼叫 refresh_derived，所有值從定義與本體欄位算。
"""

import io
import re
from collections.abc import Sequence
from pathlib import Path

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
    image = Image.new("RGBA", image_size(level, scale), _FRAME)
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
    _draw_grid(draw, level, origin, scale)
    _draw_names(draw, level, origin, labels, scale)
    boxes: list[tuple[EntityDef, tuple[int, int, int, int]]] = []
    for layer in reversed(visible):
        ldef = layer_defs[layer["layerDefUid"]]
        for entity in layer["entityInstances"]:
            edef = entity_defs.get(entity["defUid"])
            if edef is None:
                raise SceneError(f"entity {entity['iid']} 的定義不見了")
            boxes.append((edef, _entity_box(layer, ldef, edef, entity, scale)))
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
    return image


def _draw_names(
    draw: ImageDraw.ImageDraw,
    level: Level,
    origin: tuple[int, int, int],
    labels: Sequence[Label],
    scale: int,
) -> None:
    """元素名稱寫在格座標 (x, y) 上：淺色字加深色描邊，整個留在 level 裡。

    靠邊放不下就往內推；比 level 還寬或還高就縮小字，縮到最小還放不下就不寫。
    """
    grid, origin_x, origin_y = origin
    stroke = _name_stroke(scale)
    right = MARGIN + level["pxWid"] * scale - 1
    bottom = MARGIN + level["pxHei"] * scale - 1
    for name, x, y in labels:
        fitted = _fit(draw, name, stroke, right - MARGIN + 1, bottom - MARGIN + 1, scale)
        if fitted is None:
            continue
        font, (left, top, end, low) = fitted
        cx = origin_x + x * grid * scale
        cy = origin_y + y * grid * scale
        cx = min(max(cx, MARGIN - left), right - end)
        cy = min(max(cy, MARGIN - top), bottom - low)
        draw.text(
            (round(cx), round(cy)),
            name,
            fill=_LABEL,
            font=font,
            anchor="mm",
            stroke_width=stroke,
            stroke_fill=_FRAME,
        )


_SMALLEST_NAME = 6


def _fit(
    draw: ImageDraw.ImageDraw, name: str, stroke: int, width: int, height: int, scale: int
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, tuple[float, float, float, float]] | None:
    """從正常字級往下找放得進 width x height 的字；都放不下回傳 None。"""
    for size in range(_name_size(scale), _SMALLEST_NAME - 1, -1):
        font = _font(size)
        box = draw.textbbox((0, 0), name, font=font, anchor="mm", stroke_width=stroke)
        if box[2] - box[0] <= width and box[3] - box[1] <= height:
            return font, box
    return None


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
    draw: ImageDraw.ImageDraw, level: Level, origin: tuple[int, int, int], scale: int
) -> None:
    grid, origin_x, origin_y = origin
    cols = cell_count(level["pxWid"], grid)
    rows = cell_count(level["pxHei"], grid)
    cell = grid * scale
    right = MARGIN + level["pxWid"] * scale - 1
    bottom = MARGIN + level["pxHei"] * scale - 1
    font = _font(12)
    for cx in range(cols + 1):
        x = min(origin_x + cx * cell, right)
        draw.line((x, MARGIN, x, bottom), fill=_GRID_LINE)
        if cx < cols:
            # 最後一欄可能不滿一格，標籤放在看得見的那一段中間
            middle = x + (min(x + cell, right + 1) - x) / 2
            draw.text((middle, MARGIN / 2), str(cx), fill=_LABEL, font=font, anchor="mm")
    for cy in range(rows + 1):
        y = min(origin_y + cy * cell, bottom)
        draw.line((MARGIN, y, right, y), fill=_GRID_LINE)
        if cy < rows:
            middle = y + (min(y + cell, bottom + 1) - y) / 2
            draw.text((MARGIN / 2, middle), str(cy), fill=_LABEL, font=font, anchor="mm")


def preview_path(scene: SceneMap, level_id: str) -> Path:
    """預覽圖的位置：場景地圖旁的 `previews/<主檔名>/<level>.png`（D-002）。"""
    return scene.path.parent / "previews" / scene.path.stem / f"{level_id}.png"


def write_preview(scene: SceneMap, level_id: str, image: Image.Image) -> Path:
    """把 render 好的圖存成那個 level 的預覽圖，回傳路徑。"""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    path = preview_path(scene, level_id)
    atomic_write(path, buffer.getvalue())
    return path
