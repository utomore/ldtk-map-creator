"""entity 欄位：型別、預設值、值的檢查，與欄位實例的衍生欄位（D-006）。

LDtk 1.5.3 的寫法（maps/fields-template 實測）：
- 每個 entity 對它定義裡的每個欄位都有一個欄位實例，順序跟定義一樣。
- 設過值的欄位，`realEditorValues` 是 `[{"id": "V_Int", "params": [3]}]`；沒設的是 `[]`，
  `__value` 用預設值（Int、Float 是 0，Bool 是 false，String、EntityRef 是 null）。
- EntityRef 的 `realEditorValues` 只存目標的 iid（`V_String`）；
  `__value` 另外寫出目標所在的 entity、圖層、level、world 的 iid。

只管非陣列的 Int、Float、Bool、String、EntityRef；其他型別（Color、Enum、陣列……）的實例原樣保留。
"""

import copy
from typing import cast

from .errors import SceneError
from .model import EntityDef, EntityInstance, FieldDef, FieldInstance, Project

FIELD_TYPES = ("Int", "Float", "Bool", "String", "EntityRef")
_EDITOR_IDS = {"Int": "V_Int", "Float": "V_Float", "Bool": "V_Bool", "String": "V_String"}
_EDITOR_IDS["EntityRef"] = "V_String"
_DEFAULTS: dict[str, object] = {"Int": 0, "Float": 0, "Bool": False}

# entity 的 iid → （圖層 iid, level iid）
Locations = dict[str, tuple[str, str]]


def managed(fdef: FieldDef) -> bool:
    """這個欄位是不是這裡管的型別。"""
    return not fdef["isArray"] and fdef["__type"] in FIELD_TYPES


def check_value(fdef: FieldDef, value: object, locations: Locations) -> None:
    """值的型別對不對；EntityRef 要指向存在的 entity。不對丟 SceneError。"""
    name, kind = fdef["identifier"], fdef["__type"]
    if not managed(fdef):
        raise SceneError(f"欄位 {name} 的型別 {kind} 還不支援設定")
    if value is None:
        if not fdef["canBeNull"]:
            raise SceneError(f"欄位 {name}（{kind}）不能是空的")
        return
    ok = {
        "Int": isinstance(value, int) and not isinstance(value, bool),
        "Float": isinstance(value, int | float) and not isinstance(value, bool),
        "Bool": isinstance(value, bool),
        "String": isinstance(value, str),
        "EntityRef": isinstance(value, str),
    }[kind]
    if not ok:
        raise SceneError(f"欄位 {name} 要是 {kind}：{value!r}")
    if kind == "EntityRef" and value not in locations:
        raise SceneError(f"欄位 {name} 指向不存在的 entity {value}")


def set_value(instance: FieldInstance, fdef: FieldDef, value: object) -> None:
    """寫進 `realEditorValues`；`__value` 留給 refresh 重算。值要先經過 check_value。"""
    if value is None:
        instance["realEditorValues"] = []
    else:
        instance["realEditorValues"] = [{"id": _EDITOR_IDS[fdef["__type"]], "params": [value]}]


def raw_value(instance: FieldInstance, fdef: FieldDef) -> object:
    """欄位的值：設過的值，否則是預設值。EntityRef 是目標的 iid。"""
    for entry in instance["realEditorValues"]:
        params = cast(dict[str, object], entry).get("params")
        if isinstance(params, list) and params:
            return cast(list[object], params)[0]
    override = fdef["defaultOverride"]
    if isinstance(override, dict):
        params = cast(dict[str, object], override).get("params")
        if isinstance(params, list) and params:
            return cast(list[object], params)[0]
    return _DEFAULTS.get(fdef["__type"])


def new_instance(fdef: FieldDef) -> FieldInstance:
    return {
        "__identifier": fdef["identifier"],
        "__type": fdef["__type"],
        "__value": [] if fdef["isArray"] else None,
        "__tile": None,
        "defUid": fdef["uid"],
        "realEditorValues": [],
    }


def locations(project: Project) -> Locations:
    """整個場景地圖裡每個 entity 在哪個圖層、哪個 level；EntityRef 可以跨 level。"""
    found: Locations = {}
    for level in project["levels"]:
        for layer in level["layerInstances"] or []:
            for entity in layer["entityInstances"]:
                found[entity["iid"]] = (layer["iid"], level["iid"])
    return found


def refresh_fields(
    project: Project, entity: EntityInstance, edef: EntityDef, where: Locations
) -> None:
    """欄位實例跟定義一一對應（順序也一樣），並重算管得到的欄位的 `__` 欄位。

    對不上任何定義的實例照原樣留著，排在最後。

    指向已經不存在的 entity 的 EntityRef，`__value` 是 null。
    """
    existing = {cast(dict[str, object], f).get("defUid"): f for f in entity["fieldInstances"]}
    known = {f["uid"] for f in edef["fieldDefs"]}
    rebuilt: list[FieldInstance] = []
    for fdef in edef["fieldDefs"]:
        instance = existing.get(fdef["uid"]) or new_instance(fdef)
        if managed(fdef):
            instance["__identifier"] = fdef["identifier"]
            instance["__type"] = fdef["__type"]
            instance["__tile"] = None
            value = raw_value(instance, fdef)
            if fdef["__type"] == "EntityRef":
                instance["__value"] = _ref_value(project, value, where)
            else:
                instance["__value"] = copy.deepcopy(value)
        rebuilt.append(instance)
    # 對不上定義的實例不是這裡管的，原樣留在最後（D-006）
    rebuilt += [
        f for f in entity["fieldInstances"] if cast(dict[str, object], f).get("defUid") not in known
    ]
    entity["fieldInstances"] = rebuilt


def _ref_value(project: Project, target: object, where: Locations) -> object:
    if not isinstance(target, str) or target not in where:
        return None
    layer_iid, level_iid = where[target]
    return {
        "entityIid": target,
        "layerIid": layer_iid,
        "levelIid": level_iid,
        "worldIid": project["dummyWorldIid"],
    }


def clear_refs_to(project: Project, iid: str) -> None:
    """把所有指向 `iid` 的 EntityRef 清成空的（刪掉 entity 時用，LDtk 也這樣做）。"""
    defs = {
        f["uid"]: f
        for d in project["defs"]["entities"]
        for f in d["fieldDefs"]
        if f["__type"] == "EntityRef" and not f["isArray"]
    }
    for level in project["levels"]:
        for layer in level["layerInstances"] or []:
            for entity in layer["entityInstances"]:
                for instance in entity["fieldInstances"]:
                    fdef = defs.get(instance["defUid"])
                    if fdef is not None and raw_value(instance, fdef) == iid:
                        instance["realEditorValues"] = []
