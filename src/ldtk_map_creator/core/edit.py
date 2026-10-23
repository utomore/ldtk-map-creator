"""在 level 上畫 IntGrid、擺放 Entity（微調的基本操作）。

座標一律以格為單位，原點在 level 左上角。
每個操作先檢查完才動資料；後面的 refresh 失敗時還原，場景地圖不變。
"""

import re
from typing import cast

from . import fields, template
from .derived import allocate_uids, cell_count, cell_to_px, refresh_derived
from .errors import SceneError
from .model import (
    EntityDef,
    EntityInstance,
    FieldDef,
    FieldInstance,
    LayerDef,
    LayerInstance,
    Level,
    Project,
)
from .scene import IDENTIFIER, SceneMap

_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


def _layer(project: Project, level: Level, layer: str | None, layer_type: str) -> LayerInstance:
    """找 level 上的圖層；沒指定名稱時，該型別只有一層才自動選它。

    名稱與型別從圖層定義讀，不靠可能過期的 `__identifier`、`__type`。
    """
    defs = {d["uid"]: d for d in project["defs"]["layers"]}
    layers = [
        lv
        for lv in level["layerInstances"] or []
        if lv["layerDefUid"] in defs and defs[lv["layerDefUid"]]["type"] == layer_type
    ]
    if layer is not None:
        layers = [lv for lv in layers if defs[lv["layerDefUid"]]["identifier"] == layer]
        if not layers:
            raise SceneError(f"level {level['identifier']} 沒有叫 {layer} 的 {layer_type} 圖層")
    if not layers:
        raise SceneError(f"level {level['identifier']} 沒有 {layer_type} 圖層")
    if len(layers) > 1:
        names = [defs[lv["layerDefUid"]]["identifier"] for lv in layers]
        raise SceneError(
            f"level {level['identifier']} 的 {layer_type} 圖層不只一層，要指定：{names}"
        )
    return layers[0]


def _layer_def(project: Project, layer: LayerInstance) -> LayerDef:
    for ldef in project["defs"]["layers"]:
        if ldef["uid"] == layer["layerDefUid"]:
            return ldef
    raise SceneError(f"圖層 {layer['iid']} 的定義不見了")


def _grid_size(project: Project, level: Level, layer: LayerInstance) -> tuple[int, int, int]:
    """格子大小與格數，從定義與本體欄位算，不靠可能過期的 `__` 欄位。"""
    grid = _layer_def(project, layer)["gridSize"]
    return grid, cell_count(level["pxWid"], grid), cell_count(level["pxHei"], grid)


def _check_inside(width: int, height: int, cells: list[tuple[int, int]], level: Level) -> None:
    for x, y in cells:
        if not (0 <= x < width and 0 <= y < height):
            raise SceneError(f"({x},{y}) 超出 level {level['identifier']} 的 {width}x{height} 格")


def _intgrid(
    scene: SceneMap, level_id: str, layer: str | None
) -> tuple[Level, LayerInstance, int, int]:
    level = scene.level(level_id)
    target = _layer(scene.project, level, layer, "IntGrid")
    _, width, height = _grid_size(scene.project, level, target)
    if len(target["intGridCsv"]) != width * height:
        name = _layer_def(scene.project, target)["identifier"]
        raise SceneError(f"圖層 {name} 有 {len(target['intGridCsv'])} 格，尺寸是 {width}x{height}")
    return level, target, width, height


def _check_no_auto_rules(project: Project, ldef: LayerDef) -> None:
    """有 auto-layer 規則吃這層時，畫格後自動圖塊會過期；工具還不會重算，先拒絕。"""
    fed = [
        d["identifier"]
        for d in project["defs"]["layers"]
        if d["autoSourceLayerDefUid"] == ldef["uid"]
    ]
    if ldef["autoRuleGroups"] or fed:
        raise SceneError(
            f"圖層 {ldef['identifier']} 有 auto-layer 規則（{fed or '本層'}）；重算自動圖塊還不支援"
        )


def _paint(
    scene: SceneMap, level_id: str, cells: list[tuple[int, int]], value: int, layer: str | None
) -> int:
    level, target, width, height = _intgrid(scene, level_id, layer)
    _check_inside(width, height, cells, level)
    ldef = _layer_def(scene.project, target)
    _check_no_auto_rules(scene.project, ldef)
    if type(value) is not int:
        raise SceneError(f"IntGrid 的值要是整數：{value!r}")
    declared = [v["value"] for v in ldef["intGridValues"]]
    if value != 0 and value not in declared:
        raise SceneError(f"圖層 {ldef['identifier']} 沒有宣告值 {value}；宣告過的：{declared}")
    changed = 0
    for x, y in cells:
        index = y * width + x
        if target["intGridCsv"][index] != value:
            target["intGridCsv"][index] = value
            changed += 1
    return changed


def paint_rect(
    scene: SceneMap,
    level_id: str,
    *,
    x: int,
    y: int,
    width: int = 1,
    height: int = 1,
    value: int,
    layer: str | None = None,
) -> int:
    """把矩形範圍內的格子設成 `value`（0 是清除）；回傳改了幾格。"""
    if width < 1 or height < 1:
        raise SceneError(f"範圍至少 1 格：{width}x{height}")
    level, _, cols, rows = _intgrid(scene, level_id, layer)
    # 先用算術檢查邊界，超大的範圍不展開成格子清單
    _check_inside(cols, rows, [(x, y), (x + width - 1, y + height - 1)], level)
    cells = [(cx, cy) for cy in range(y, y + height) for cx in range(x, x + width)]
    return _paint(scene, level_id, cells, value, layer)


def paint_cells(
    scene: SceneMap,
    level_id: str,
    *,
    cells: list[tuple[int, int]],
    value: int,
    layer: str | None = None,
) -> int:
    """把指定的幾格設成 `value`（0 是清除）；任何一格出界就全部不改。回傳改了幾格。"""
    return _paint(scene, level_id, cells, value, layer)


def read_cell(scene: SceneMap, level_id: str, *, x: int, y: int, layer: str | None = None) -> int:
    level, target, width, height = _intgrid(scene, level_id, layer)
    _check_inside(width, height, [(x, y)], level)
    return target["intGridCsv"][y * width + x]


def _entity_def_by_name(project: Project, identifier: str) -> EntityDef:
    for edef in project["defs"]["entities"]:
        if edef["identifier"] == identifier:
            return edef
    names = [d["identifier"] for d in project["defs"]["entities"]]
    raise SceneError(f"沒有叫 {identifier} 的 entity 定義；有的：{names}")


def _entity_def_by_uid(project: Project, uid: int) -> EntityDef:
    for edef in project["defs"]["entities"]:
        if edef["uid"] == uid:
            return edef
    raise SceneError(f"entity 定義 {uid} 不見了")


def _check_tags(project: Project, layer: LayerInstance, edef: EntityDef) -> None:
    """照 LDtk 的限制：圖層的 required / excluded tags。"""
    ldef = _layer_def(project, layer)
    tags = set(edef["tags"])
    if ldef["requiredTags"] and not tags & set(ldef["requiredTags"]):
        raise SceneError(f"圖層 {ldef['identifier']} 只收帶 {ldef['requiredTags']} 標籤的 entity")
    if tags & set(ldef["excludedTags"]):
        raise SceneError(f"圖層 {ldef['identifier']} 不收帶 {ldef['excludedTags']} 標籤的 entity")


def _check_toc(edef: EntityDef) -> None:
    if edef["exportToToc"]:
        raise SceneError(f"entity {edef['identifier']} 要匯出到 toc；重建 toc 還不支援")


def _scope_instances(
    scene: SceneMap, level: Level, layer: LayerInstance, edef: EntityDef
) -> list[tuple[LayerInstance, EntityInstance]]:
    if edef["limitScope"] == "PerLayer":
        layers = [layer]
    elif edef["limitScope"] == "PerWorld":
        layers = [ly for lv in scene.project["levels"] for ly in lv["layerInstances"] or []]
    else:
        layers = level["layerInstances"] or []
    return [(ly, e) for ly in layers for e in ly["entityInstances"] if e["defUid"] == edef["uid"]]


def place_entity(
    scene: SceneMap,
    level_id: str,
    entity: str,
    *,
    x: int,
    y: int,
    layer: str | None = None,
    values: dict[str, object] | None = None,
) -> EntityInstance:
    """在 (x, y) 格擺一個 entity，回傳它（`iid` 已配發）。

    `values` 是欄位值（欄位名稱 → 值）；沒給的欄位用預設值。

    到了定義的 maxCount 時照 limitBehavior：MoveLastOne 把最後一個移過來、
    DiscardOldOnes 丟掉最舊的、PreventAdding 拒絕。
    """
    level = scene.level(level_id)
    target = _layer(scene.project, level, layer, "Entities")
    grid, width, height = _grid_size(scene.project, level, target)
    _check_inside(width, height, [(x, y)], level)
    edef = _entity_def_by_name(scene.project, entity)
    unsupported = [f["identifier"] for f in edef["fieldDefs"] if not fields.managed(f)]
    if unsupported:
        raise SceneError(f"entity {entity} 有還不支援的欄位型別（{unsupported}），先不擺")
    _check_toc(edef)
    _check_tags(scene.project, target, edef)
    refresh_derived(scene.project, only=level)  # 先確定這個 level 本身沒問題，才動資料
    px = [cell_to_px(x, edef["pivotX"], grid), cell_to_px(y, edef["pivotY"], grid)]
    existing = _scope_instances(scene, level, target, edef)
    full = edef["maxCount"] > 0 and len(existing) >= edef["maxCount"]
    behavior = edef["limitBehavior"] if full else None
    if behavior not in (None, "MoveLastOne", "DiscardOldOnes"):
        raise SceneError(f"entity {entity} 在 {edef['limitScope']} 最多 {edef['maxCount']} 個")
    where = fields.locations(scene.project)
    discarded = existing[0][1]["iid"] if behavior == "DiscardOldOnes" else None
    if discarded is not None:
        where.pop(discarded)  # 要丟掉的那個不能再被指
    _check_values(scene.project, edef, values or {}, where, (edef["uid"], level["iid"]))
    if behavior == "MoveLastOne":
        old_layer, moved = existing[-1]
        _check_move_refs(scene.project, moved, values or {}, level["iid"])
        old_layer["entityInstances"].remove(moved)
        moved["px"] = px
        _write_values(edef, moved, values or {})
        target["entityInstances"].append(moved)
        refresh_derived(scene.project)
        return moved
    if discarded is not None:
        old_layer, oldest = existing[0]
        old_layer["entityInstances"].remove(oldest)
        fields.clear_refs_to(scene.project, discarded)
    instance = template.entity()
    instance["iid"] = ""
    instance["defUid"] = edef["uid"]
    instance["width"] = edef["width"]
    instance["height"] = edef["height"]
    instance["px"] = px
    instance["fieldInstances"] = [fields.new_instance(f) for f in edef["fieldDefs"]]
    _write_values(edef, instance, values or {})
    target["entityInstances"].append(instance)
    refresh_derived(scene.project, only=None if discarded else level)
    return instance


def _check_move_refs(
    project: Project, moved: EntityInstance, values: dict[str, object], new_level: str
) -> None:
    """entity 挪到別的 level 時，不能跨 level 的引用（它指出去的、別人指向它的）都要還成立。"""
    where = fields.locations(project)
    old_level = where[moved["iid"]][1]
    if old_level == new_level:
        return
    defs: dict[object, FieldDef] = {
        f["uid"]: f for d in project["defs"]["entities"] for f in d["fieldDefs"]
    }
    for instance in moved["fieldInstances"]:
        fdef = defs.get(cast(dict[str, object], instance).get("defUid"))
        if fdef is None or fdef["__type"] != "EntityRef" or fdef["allowOutOfLevelRef"]:
            continue
        target = values.get(fdef["identifier"], fields.raw_value(instance, fdef))
        if isinstance(target, str) and target in where and where[target][1] != new_level:
            raise SceneError(f"挪過去之後，欄位 {fdef['identifier']} 會指到別的 level，定義不允許")
    for level in project["levels"]:
        for layer in level["layerInstances"] or []:
            for entity in layer["entityInstances"]:
                for instance in entity["fieldInstances"]:
                    fdef = defs.get(cast(dict[str, object], instance).get("defUid"))
                    if fdef is None or fdef["__type"] != "EntityRef" or fdef["allowOutOfLevelRef"]:
                        continue
                    if (
                        fields.raw_value(instance, fdef) == moved["iid"]
                        and level["iid"] != new_level
                    ):
                        name = fdef["identifier"]
                        raise SceneError(
                            f"{entity['iid']} 的欄位 {name} 只能指同一個 level，不能挪走"
                        )


def _check_values(
    project: Project,
    edef: EntityDef,
    values: dict[str, object],
    where: fields.Locations,
    owner: tuple[int, str],
) -> None:
    """欄位名稱都存在、值都對；先檢查完才動資料。`owner` 是（定義 uid, level iid）。"""
    by_name = {f["identifier"]: f for f in edef["fieldDefs"]}
    for name, value in values.items():
        fdef = by_name.get(name)
        if fdef is None:
            raise SceneError(f"entity {edef['identifier']} 沒有欄位 {name}；有的：{list(by_name)}")
        fields.check_value(project, fdef, value, where, owner)


def _write_values(edef: EntityDef, entity: EntityInstance, values: dict[str, object]) -> None:
    by_name = {f["identifier"]: f for f in edef["fieldDefs"]}
    # 同一個欄位有好幾個實例時寫第一個，跟 refresh 認的同一個
    by_uid: dict[object, FieldInstance] = {}
    for f in entity["fieldInstances"]:
        by_uid.setdefault(cast(dict[str, object], f).get("defUid"), f)
    for name, value in values.items():
        fdef = by_name[name]
        instance = by_uid.get(fdef["uid"])
        if instance is None:
            instance = fields.new_instance(fdef)
            entity["fieldInstances"].append(instance)
        fields.set_value(instance, fdef, value)


def values_from_text(edef: EntityDef, texts: dict[str, str]) -> dict[str, object]:
    """把文字的欄位值照欄位型別轉成值（CLI 用）；欄位不存在丟 SceneError。"""
    by_name = {f["identifier"]: f for f in edef["fieldDefs"]}
    values: dict[str, object] = {}
    for name, text in texts.items():
        fdef = by_name.get(name)
        if fdef is None:
            raise SceneError(f"entity {edef['identifier']} 沒有欄位 {name}；有的：{list(by_name)}")
        values[name] = fields.parse_text(fdef, text)
    return values


def entity_def_of(scene: SceneMap, iid: str) -> EntityDef:
    """一個 entity 的定義。"""
    _, _, entity = find_entity(scene, iid)
    return _entity_def_by_uid(scene.project, entity["defUid"])


def entity_def_named(scene: SceneMap, identifier: str) -> EntityDef:
    """用名稱找 entity 定義。"""
    return _entity_def_by_name(scene.project, identifier)


def set_entity_fields(scene: SceneMap, iid: str, values: dict[str, object]) -> EntityInstance:
    """設定一個 entity 的欄位值（欄位名稱 → 值；None 是清空）；回傳那個 entity。"""
    level, _, entity = find_entity(scene, iid)
    edef = _entity_def_by_uid(scene.project, entity["defUid"])
    _check_toc(edef)
    refresh_derived(scene.project, only=level)  # 先確定這個 level 本身沒問題，才動資料
    where = fields.locations(scene.project)
    _check_values(scene.project, edef, values, where, (edef["uid"], level["iid"]))
    _write_values(edef, entity, values)
    refresh_derived(scene.project, only=level)
    return entity


def add_entity_def(
    scene: SceneMap,
    identifier: str,
    *,
    color: str = "#BE4A2F",
    width: int = 16,
    height: int = 16,
    field_types: dict[str, str] | None = None,
) -> EntityDef:
    """新增一個 entity 定義。

    `field_types` 是欄位名稱 → 型別（Int、Float、Bool、String、EntityRef）。
    """
    project = scene.project
    if not IDENTIFIER.fullmatch(identifier):
        raise SceneError(f"entity 名稱只能用英數字與底線，且不以數字開頭：{identifier}")
    if any(d["identifier"].lower() == identifier.lower() for d in project["defs"]["entities"]):
        raise SceneError(f"已經有同名的 entity 定義（不分大小寫）：{identifier}")
    if not _COLOR.fullmatch(color):
        raise SceneError(f'顏色要是 "#RRGGBB"：{color!r}')
    if width < 1 or height < 1:
        raise SceneError(f"entity 尺寸至少 1 像素：{width}x{height}")
    wanted = field_types or {}
    for name, kind in wanted.items():
        if not IDENTIFIER.fullmatch(name):
            raise SceneError(f"欄位名稱只能用英數字與底線，且不以數字開頭：{name}")
        if kind not in fields.FIELD_TYPES:
            raise SceneError(f"欄位 {name} 的型別要是 {'、'.join(fields.FIELD_TYPES)}：{kind}")
    if len({n.lower() for n in wanted}) != len(wanted):
        raise SceneError(f"欄位名稱重複（不分大小寫）：{list(wanted)}")
    uids = allocate_uids(project, 1 + len(wanted))
    edef = template.entity_def()
    edef["identifier"] = identifier
    edef["uid"] = uids[0]
    edef["color"] = color
    edef["width"] = width
    edef["height"] = height
    for (name, kind), uid in zip(wanted.items(), uids[1:], strict=True):
        fdef = template.field_def(kind)
        fdef["identifier"] = name
        fdef["uid"] = uid
        # 範本的 EntityRef 只能指同一種 entity；對話裡沒地方改，新建的放寬成哪一種都能指
        fdef["allowedRefs"] = "Any"
        edef["fieldDefs"].append(fdef)
    project["defs"]["entities"].append(edef)
    return edef


def find_entity(scene: SceneMap, iid: str) -> tuple[Level, LayerInstance, EntityInstance]:
    """用 iid 找 entity，回傳（所在 level, 所在圖層, entity）。"""
    for level in scene.project["levels"]:
        for layer in level["layerInstances"] or []:
            for entity in layer["entityInstances"]:
                if entity["iid"] == iid:
                    return level, layer, entity
    raise SceneError(f"找不到 entity {iid}")


def move_entity(scene: SceneMap, iid: str, *, x: int, y: int) -> tuple[Level, EntityInstance]:
    """把 entity 移到同一個 level 的 (x, y) 格；回傳（所在 level, entity）。"""
    level, layer, entity = find_entity(scene, iid)
    grid, width, height = _grid_size(scene.project, level, layer)
    _check_inside(width, height, [(x, y)], level)
    edef = _entity_def_by_uid(scene.project, entity["defUid"])
    _check_toc(edef)
    refresh_derived(scene.project, only=level)  # 先確定這個 level 本身沒問題，才動資料
    entity["px"] = [cell_to_px(x, edef["pivotX"], grid), cell_to_px(y, edef["pivotY"], grid)]
    refresh_derived(scene.project, only=level)
    return level, entity


def delete_entity(scene: SceneMap, iid: str) -> Level:
    """刪掉一個 entity；回傳它原本所在的 level。"""
    level, layer, entity = find_entity(scene, iid)
    _check_toc(_entity_def_by_uid(scene.project, entity["defUid"]))
    layer["entityInstances"].remove(entity)
    if fields.clear_refs_to(scene.project, iid):  # 指向它的 EntityRef 一起清掉，跟 LDtk 一樣
        refresh_derived(scene.project)  # 別的 level 的 __value 也要跟著變
    return level
