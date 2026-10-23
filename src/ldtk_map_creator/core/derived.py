"""統一補齊 `iid`、`uid`、`nextUid` 與 `__` 衍生欄位（D-006）。

LDtk 存檔時不會替沒改動的 level 補欄位（D-006 實測），
所以每次寫檔前都由這裡從本體欄位重算。
新建的物件把 `iid` 設成 `""`、`uid` 設成負數，交給這裡配發。
"""

import copy
import math
from typing import cast

from . import fields
from .errors import SceneError
from .ids import new_iid
from .model import EntityDef, EntityInstance, LayerDef, Level, Project

UNASSIGNED_UID = -1


def cell_count(px: int, grid: int) -> int:
    """一個方向上的格數：像素除以格子大小，向上取整。"""
    return math.ceil(px / grid)


def level_bg_color(project: Project, level: Level) -> str:
    """level 的背景色：自己設的，否則用專案預設（`__bgColor` 與 render 共用）。"""
    return level["bgColor"] or project["defaultLevelBgColor"]


def refresh_derived(project: Project, only: Level | None = None) -> None:
    """配發缺的 `iid` 與 `uid`，再依 defs 與本體欄位重算 `__` 欄位。

    給 `only` 時只重算那個 level 的圖層與 entity（level 內的編輯用），不動相鄰關係。
    """
    _assign_ids(project)
    layer_defs = {d["uid"]: d for d in project["defs"]["layers"]}
    entity_defs = {d["uid"]: d for d in project["defs"]["entities"]}
    tileset_paths = {t["uid"]: t["relPath"] for t in project["defs"]["tilesets"]}
    # EntityRef 可以跨 level，只重算一個 level 時也要看整個場景地圖
    where = fields.locations(project)
    for level in project["levels"] if only is None else [only]:
        _refresh_level(project, level, layer_defs, entity_defs, tileset_paths, where)
        if only is None:
            manual = _manual_layout(project)
            level["__neighbours"] = _neighbours(level, project["levels"]) if manual else []


def _used_uids(project: Project) -> list[int]:
    defs = project["defs"]
    used = [d["uid"] for d in defs["layers"]] + [d["uid"] for d in defs["entities"]]
    used += [f["uid"] for d in defs["entities"] for f in d["fieldDefs"]]
    used += [t["uid"] for t in defs["tilesets"]] + [lv["uid"] for lv in project["levels"]]
    return used


def allocate_uid(project: Project) -> int:
    """配一個沒人用的 uid（新的定義用），並推進 `nextUid`。"""
    project["nextUid"] = max([project["nextUid"], *(u + 1 for u in _used_uids(project))])
    uid = project["nextUid"]
    project["nextUid"] += 1
    return uid


def _assign_ids(project: Project) -> None:
    used = _used_uids(project)
    project["nextUid"] = max([project["nextUid"], *(u + 1 for u in used)])
    for level in project["levels"]:
        if level["uid"] == UNASSIGNED_UID:
            level["uid"] = project["nextUid"]
            project["nextUid"] += 1
        if not level["iid"]:
            level["iid"] = new_iid()
        for layer in level["layerInstances"] or []:
            if not layer["iid"]:
                layer["iid"] = new_iid()
            for entity in layer["entityInstances"]:
                if not entity["iid"]:
                    entity["iid"] = new_iid()
    if not project["iid"]:
        project["iid"] = new_iid()
    if not project["dummyWorldIid"]:
        project["dummyWorldIid"] = new_iid()


def _refresh_level(
    project: Project,
    level: Level,
    layer_defs: dict[int, LayerDef],
    entity_defs: dict[int, EntityDef],
    tileset_paths: dict[int, str | None],
    where: fields.Locations,
) -> None:
    level["__bgColor"] = level_bg_color(project, level)
    layers = level["layerInstances"]
    if layers is None:
        raise SceneError(f"level {level['identifier']} 的圖層還沒載入")
    for layer in layers:
        ldef = layer_defs.get(layer["layerDefUid"])
        if ldef is None:
            raise SceneError(f"圖層 {layer['iid']} 指向不存在的圖層定義 {layer['layerDefUid']}")
        grid = ldef["gridSize"]
        c_wid = cell_count(level["pxWid"], grid)
        c_hei = cell_count(level["pxHei"], grid)
        if ldef["type"] == "IntGrid" and len(layer["intGridCsv"]) != c_wid * c_hei:
            raise SceneError(
                f"level {level['identifier']} 的 {ldef['identifier']} 有 "
                f"{len(layer['intGridCsv'])} 格，尺寸是 {c_wid}x{c_hei}"
            )
        override = layer["overrideTilesetUid"]
        tileset_uid = override if override is not None else ldef["tilesetDefUid"]
        layer["__identifier"] = ldef["identifier"]
        layer["__type"] = ldef["type"]
        layer["__gridSize"] = grid
        layer["__cWid"] = c_wid
        layer["__cHei"] = c_hei
        layer["__opacity"] = ldef["displayOpacity"]
        layer["__pxTotalOffsetX"] = layer["pxOffsetX"] + ldef["pxOffsetX"]
        layer["__pxTotalOffsetY"] = layer["pxOffsetY"] + ldef["pxOffsetY"]
        layer["__tilesetDefUid"] = tileset_uid
        layer["__tilesetRelPath"] = None if tileset_uid is None else tileset_paths.get(tileset_uid)
        layer["levelId"] = level["uid"]
        for entity in layer["entityInstances"]:
            edef = entity_defs.get(entity["defUid"])
            if edef is None:
                raise SceneError(f"entity {entity['iid']} 指向不存在的定義 {entity['defUid']}")
            entity["__identifier"] = edef["identifier"]
            entity["__grid"] = [
                _cell(entity["px"][0], edef["pivotX"], grid),
                _cell(entity["px"][1], edef["pivotY"], grid),
            ]
            entity["__pivot"] = [edef["pivotX"], edef["pivotY"]]
            entity["__tags"] = list(edef["tags"])
            entity["__smartColor"] = edef["color"]
            fields.refresh_fields(project, entity, edef, where)
            # LDtk 的 smart tile 先看 Tile 欄位、再看定義的 tileRect；
            # 有 Tile 欄位的 entity 保留 LDtk 算好的值，其餘照定義
            if not _has_tile_field(entity):
                entity["__tile"] = copy.deepcopy(edef["tileRect"])
            if _manual_layout(project):
                entity["__worldX"] = level["worldX"] + entity["px"][0]
                entity["__worldY"] = level["worldY"] + entity["px"][1]
            else:
                entity["__worldX"] = None
                entity["__worldY"] = None


def _has_tile_field(entity: EntityInstance) -> bool:
    fields = cast(list[dict[str, object]], entity["fieldInstances"])
    return any(
        str(f.get("__type", "")).endswith("Tile>") or f.get("__type") == "Tile" for f in fields
    )


def _manual_layout(project: Project) -> bool:
    """Free 與 GridVania 由 worldX / worldY 決定位置；線性佈局的座標固定是 -1。"""
    return project["worldLayout"] in ("Free", "GridVania")


def _cell(px: int, pivot: float, grid: int) -> int:
    """entity 所在的格；照 LDtk 1.5.3 的 EntityInstance.getCx：pivot 為 1 時先減 1，再截斷取整。"""
    return int((px + (-1 if pivot == 1 else 0)) / grid)


def cell_to_px(cell: int, pivot: float, grid: int) -> int:
    """格子座標轉成 LDtk 的 px（pivot 點的位置）；是 `_cell` 的反函數。"""
    return cell * grid + int(pivot * grid)


def _depth_direction(depth: int, other_depth: int) -> str:
    """重疊的兩個 level：同深度 o，對方較深 >，較淺 <。"""
    if other_depth == depth:
        return "o"
    return ">" if other_depth > depth else "<"


def _neighbours(level: Level, levels: list[Level]) -> list[object]:
    """照 LDtk 1.5.3 的 Level.getNeighboursJson：先列重疊的，再列相接的。

    - 重疊：深度差不超過 1；同深度 o，對方較深 >，較淺 <。
    - 相接：同深度、不重疊、邊或角碰在一起；角 nw/ne/sw/se，邊 n/s/w/e。
    """
    ax, ay, aw, ah = level["worldX"], level["worldY"], level["pxWid"], level["pxHei"]
    overlaps: list[object] = []
    touches: list[object] = []
    for other in levels:
        if other is level:
            continue
        bx, by, bw, bh = other["worldX"], other["worldY"], other["pxWid"], other["pxHei"]
        depth, other_depth = level["worldDepth"], other["worldDepth"]
        overlapping = ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by
        if overlapping and abs(depth - other_depth) <= 1:
            overlaps.append({"dir": _depth_direction(depth, other_depth), "levelIid": other["iid"]})
            continue
        touching = bx <= ax + aw and bx + bw >= ax and by <= ay + ah and by + bh >= ay
        if overlapping or not touching or other_depth != depth:
            continue
        touches.append(
            {"dir": _touch_direction(ax, ay, aw, ah, bx, by, bw, bh), "levelIid": other["iid"]}
        )
    return overlaps + touches


def _touch_direction(ax: int, ay: int, aw: int, ah: int, bx: int, by: int, bw: int, bh: int) -> str:
    if bx == ax + aw and by == ay + ah:
        return "se"
    if bx + bw == ax and by == ay + ah:
        return "sw"
    if bx == ax + aw and by + bh == ay:
        return "ne"
    if bx + bw == ax and by + bh == ay:
        return "nw"
    if bx >= ax + aw:
        return "e"
    if bx + bw <= ax:
        return "w"
    if by + bh <= ay:
        return "n"
    return "s"
