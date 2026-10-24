"""慣例檢查（D-011、CONV）：場景地圖照不照分層慣例；純函式，不寫檔、不改場景地圖。

慣例是下限：多的層、欄位、tags、entity 定義都不報。
每一條問題是一行字串，開頭寫出位置：`<level>/<層> (x,y)：…`、`<level>/<entity> <iid 前 8 碼>：…`、
`defs/<名稱>：…`。順序固定：先定義（照層、entity 的順序），再照 level、層、格的順序。
"""

from typing import Any, cast

from . import convention
from .derived import cell_count
from .fields import raw_value
from .model import EntityDef, EntityInstance, FieldDef, FieldInstance, LayerDef, LayerInstance
from .scene import SceneMap

_LAYER_TYPES = {convention.ENTITIES: "Entities"} | {n: "IntGrid" for n in convention.INTGRID_LAYERS}
# 四個方向的鄰格：北、東、南、西
_NEIGHBOURS = ((0, -1), (1, 0), (0, 1), (-1, 0))


def check_scene(scene: SceneMap) -> list[str]:
    """回傳每一處不照慣例的地方；空清單就是照慣例。"""
    project = scene.project
    problems: list[str] = []
    layer_defs = _check_layer_defs(project["defs"]["layers"], problems)
    entity_defs = _check_entity_defs(project["defs"]["entities"], problems)
    # 名稱一律從定義讀（用 uid 對），不靠可能過期的 `__identifier`
    def_names = {d["uid"]: d["identifier"] for d in project["defs"]["entities"]}
    names = _entity_names(scene, def_names)
    for level in project["levels"]:
        name = level["identifier"]
        by_name = _layer_instances(level["layerInstances"] or [], layer_defs)
        for layer in convention.LAYERS:
            instance = by_name.get(layer)
            if instance is None:
                problems.append(f"{name}/{layer}：沒有這一層的實例")
            elif layer == convention.ENTITIES:
                _check_entities(name, instance, entity_defs, def_names, names, problems)
        grids: dict[str, tuple[int, list[int]]] = {}
        for layer in convention.INTGRID_LAYERS:
            instance = by_name.get(layer)
            layer_def = layer_defs.get(layer)
            if instance is None or layer_def is None or layer_def["type"] != "IntGrid":
                continue  # 型別不對已經在 defs 報過，不再逐格報
            grid = layer_def["gridSize"]  # 每一層照自己的格子大小
            width = cell_count(level["pxWid"], grid)
            height = cell_count(level["pxHei"], grid)
            csv = instance["intGridCsv"]
            if len(csv) != width * height:
                problems.append(
                    f"{name}/{layer}：格子數 {len(csv)} 不等於 {width}×{height}（intGridCsv）"
                )
                continue
            grids[layer] = (grid, csv)
            _check_values(name, layer, csv, width, layer_def, problems)
        slope, heights = grids.get(convention.SLOPE), grids.get(convention.HEIGHT)
        # 兩層格子大小不同時對不上格，斜坡無從比較
        if slope is not None and heights is not None and slope[0] == heights[0]:
            width = cell_count(level["pxWid"], slope[0])
            _check_slopes(name, slope[1], heights[1], width, problems)
    return problems


def _check_layer_defs(defs: list[LayerDef], problems: list[str]) -> dict[str, LayerDef]:
    found: dict[str, LayerDef] = {}
    for layer in convention.LAYERS:
        same = [d for d in defs if d["identifier"] == layer]
        if not same:
            problems.append(f"defs/{layer}：缺少這一層的定義")
            continue
        if len(same) > 1:
            problems.append(f"defs/{layer}：有 {len(same)} 個同名的層定義，只能有一個")
        found[layer] = same[0]
        wanted = _LAYER_TYPES[layer]
        if same[0]["type"] != wanted:
            problems.append(f"defs/{layer}：型別是 {same[0]['type']}，要是 {wanted}")
    slope = found.get(convention.SLOPE)
    if slope is not None:
        for value in slope["intGridValues"]:
            if value["value"] != 1:
                problems.append(f"defs/{convention.SLOPE}：宣告了值 {value['value']}，只能宣告 1")
    height = found.get(convention.HEIGHT)
    if height is not None:
        for value in height["intGridValues"]:
            if value["value"] < 1:
                problems.append(f"defs/{convention.HEIGHT}：宣告了值 {value['value']}，要 ≥ 1")
    return found


def _field(edef: EntityDef, name: str) -> FieldDef | None:
    return next((f for f in edef["fieldDefs"] if f["identifier"] == name), None)


def _check_entity_defs(defs: list[EntityDef], problems: list[str]) -> dict[str, EntityDef]:
    found: dict[str, EntityDef] = {}
    for name in convention.ENTITY_TYPES:
        same = [d for d in defs if d["identifier"] == name]
        if not same:
            problems.append(f"defs/{name}：缺少這個 entity 定義")
            continue
        if len(same) > 1:
            problems.append(f"defs/{name}：有 {len(same)} 個同名的 entity 定義，只能有一個")
        found[name] = same[0]
    spawn = found.get(convention.PLAYER_SPAWN)
    if spawn is not None and convention.PLAYER_TAG not in spawn["tags"]:
        problems.append(f"defs/{convention.PLAYER_SPAWN}：缺少 tags {convention.PLAYER_TAG}")
    wanted = {
        convention.TELEPORT: ((convention.TELEPORT_TARGET, "EntityRef"),),
        convention.PART: ((convention.PART_NAME, "String"), (convention.PART_YAW, "Float")),
    }
    for name, fields in wanted.items():
        edef = found.get(name)
        if edef is None:
            continue
        for field, kind in fields:
            fdef = _field(edef, field)
            if fdef is None:
                problems.append(f"defs/{name}：缺少欄位 {field}（{kind}）")
            elif fdef["__type"] != kind:
                problems.append(f"defs/{name}：欄位 {field} 是 {fdef['__type']}，要是 {kind}")
    return found


def _layer_instances(
    instances: list[LayerInstance], defs: dict[str, LayerDef]
) -> dict[str, LayerInstance]:
    """每一層的實例：用 layerDefUid 對慣例的層定義（同名重複的定義只認第一個）。"""
    by_uid = {d["uid"]: name for name, d in defs.items()}
    found: dict[str, LayerInstance] = {}
    for instance in instances:
        name = by_uid.get(instance["layerDefUid"])
        if name is not None:
            found.setdefault(name, instance)
    return found


def _entity_name(entity: EntityInstance, def_names: dict[int, str]) -> str:
    """entity 的定義名稱，用 defUid 從定義讀；定義不見了就是空字串。"""
    return def_names.get(entity["defUid"], "")


def _value(entity: EntityInstance, edef: EntityDef, name: str) -> tuple[bool, object]:
    """（定義有沒有這個欄位, 值）；沒設過的是預設值。"""
    fdef = _field(edef, name)
    if fdef is None:
        return False, None
    instance = next((f for f in entity["fieldInstances"] if f.get("defUid") == fdef["uid"]), None)
    if instance is None:
        return True, raw_value(_blank(fdef), fdef)
    return True, raw_value(_normal(instance), fdef)


def _blank(fdef: FieldDef) -> FieldInstance:
    return cast(FieldInstance, {"realEditorValues": [], "defUid": fdef["uid"]})


def _normal(instance: FieldInstance) -> FieldInstance:
    data = cast(dict[str, Any], instance)
    return cast(FieldInstance, {**data, "realEditorValues": data.get("realEditorValues", [])})


def _entity_names(scene: SceneMap, def_names: dict[int, str]) -> dict[str, str]:
    """整個場景地圖所有 entity 的 iid → 定義名稱（跨 level，查指向用）；不只 Teleport。"""
    found: dict[str, str] = {}
    for level in scene.project["levels"]:
        for layer in level["layerInstances"] or []:
            for entity in layer["entityInstances"]:
                found[entity["iid"]] = _entity_name(entity, def_names)
    return found


def _check_entities(
    level: str,
    layer: LayerInstance,
    entity_defs: dict[str, EntityDef],
    def_names: dict[int, str],
    names: dict[str, str],
    problems: list[str],
) -> None:
    for entity in layer["entityInstances"]:
        name = _entity_name(entity, def_names)
        where = f"{level}/{name} {entity['iid'][:8]}"
        edef = entity_defs.get(name)
        if edef is None or edef["uid"] != entity["defUid"]:
            continue  # 不是慣例的 entity，或屬於重複的同名定義（defs 已經報過）
        if name == convention.TELEPORT:
            has_field, target = _value(entity, edef, convention.TELEPORT_TARGET)
            if not has_field:
                continue  # 定義缺欄位已經在 defs 報過
            field = convention.TELEPORT_TARGET
            ref = _ref_iid(target)
            if ref is None:
                problems.append(f"{where}：{field} 是空的")
            elif ref == entity["iid"]:
                problems.append(f"{where}：{field} 指到自己")
            elif ref not in names:
                problems.append(f"{where}：{field} 指到不存在的 entity {ref[:8]}")
            elif names[ref] != convention.TELEPORT:
                problems.append(f"{where}：{field} 指到的是 {names[ref]}，要指到 Teleport")
        elif name == convention.PART:
            has_field, part = _value(entity, edef, convention.PART_NAME)
            if has_field and (not isinstance(part, str) or not part.strip()):
                problems.append(f"{where}：{convention.PART_NAME} 是空白的")


def _ref_iid(value: object) -> str | None:
    """EntityRef 的值是目標的 iid；空的、空字串都是 None。"""
    if isinstance(value, str) and value:
        return value
    return None


def _check_values(
    level: str,
    layer: str,
    csv: list[int],
    width: int,
    layer_def: LayerDef | None,
    problems: list[str],
) -> None:
    if layer_def is None:
        return
    allowed = {0} | {v["value"] for v in layer_def["intGridValues"]}
    for index, value in enumerate(csv):
        if value not in allowed:
            x, y = index % width, index // width
            problems.append(f"{level}/{layer} ({x},{y})：值 {value} 沒有宣告")


def stranded_slopes(slope: list[int], height: list[int], width: int) -> list[tuple[int, int]]:
    """斜坡格裡北東南西都沒有高一階的格，照列優先（先列後行）；慣例檢查與生成（D-012）共用。"""
    rows = len(slope) // width if width else 0
    found: list[tuple[int, int]] = []
    for index, value in enumerate(slope):
        if value == 0:
            continue
        x, y = index % width, index // width
        here = height[index]
        higher = any(
            0 <= x + dx < width
            and 0 <= y + dy < rows
            and height[(y + dy) * width + x + dx] == here + 1
            for dx, dy in _NEIGHBOURS
        )
        if not higher:
            found.append((x, y))
    return found


def _check_slopes(
    level: str, slope: list[int], height: list[int], width: int, problems: list[str]
) -> None:
    for x, y in stranded_slopes(slope, height, width):
        problems.append(f"{level}/{convention.SLOPE} ({x},{y})：斜坡四周沒有高一階的格")
