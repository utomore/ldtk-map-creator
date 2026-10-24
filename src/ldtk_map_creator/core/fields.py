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
import math
from typing import cast

from .errors import SceneError
from .model import EntityDef, EntityInstance, FieldDef, FieldInstance, Project

FIELD_TYPES = ("Int", "Float", "Bool", "String", "EntityRef")
_EDITOR_IDS = {"Int": "V_Int", "Float": "V_Float", "Bool": "V_Bool", "String": "V_String"}
_EDITOR_IDS["EntityRef"] = "V_String"
_DEFAULTS: dict[str, object] = {"Int": 0, "Float": 0, "Bool": False}

# entity 的 iid → （圖層 iid, level iid, 定義的 uid）
Locations = dict[str, tuple[str, str, int]]


def managed(fdef: FieldDef) -> bool:
    """這個欄位是不是這裡管的型別。"""
    return not fdef["isArray"] and fdef["__type"] in FIELD_TYPES


INT_RANGE = range(-(2**31), 2**31)
"""LDtk 的 Int 欄位是 32 位元。"""


def value_fits(kind: str, value: object) -> bool:
    """值的型別對不對（不看定義的範圍）：Float 也收整數、要是有限的數；Int 在 32 位元內；
    bool 是 int 的子類別，不算數字。
    """
    number = isinstance(value, int | float) and not isinstance(value, bool)
    return {
        "Int": number and isinstance(value, int) and value in INT_RANGE,
        "Float": number and math.isfinite(cast(float, value)),
        "Bool": isinstance(value, bool),
        "String": isinstance(value, str),
        "EntityRef": isinstance(value, str),
    }.get(kind, False)


def check_value(
    project: Project, fdef: FieldDef, value: object, where: Locations, owner: tuple[int, str]
) -> None:
    """值對不對：型別、範圍；EntityRef 要指向存在、而且定義允許的 entity。不對丟 SceneError。

    `owner` 是擁有這個欄位的 entity 的（定義 uid, level iid）。
    """
    name, kind = fdef["identifier"], fdef["__type"]
    if not managed(fdef):
        raise SceneError(f"欄位 {name} 的型別 {kind} 還不支援設定")
    if value is None:
        if not fdef["canBeNull"]:
            raise SceneError(f"欄位 {name}（{kind}）不能是空的")
        return
    numeric = isinstance(value, int | float) and not isinstance(value, bool)
    if kind in ("Int", "Float") and numeric and not math.isfinite(cast(float, value)):
        raise SceneError(f"欄位 {name} 要是有限的數字：{value!r}")  # NaN 寫進 JSON 會讓檔案壞掉
    if not value_fits(kind, value):
        raise SceneError(f"欄位 {name} 要是 {kind}：{value!r}")
    if kind in ("Int", "Float"):
        number = cast(float, value)
        if not math.isfinite(number):  # NaN 寫進 JSON 會讓檔案壞掉
            raise SceneError(f"欄位 {name} 要是有限的數字：{value!r}")
        low, high = fdef["min"], fdef["max"]
        if (low is not None and number < low) or (high is not None and number > high):
            raise SceneError(f"欄位 {name} 要在 {low}～{high} 之間：{value!r}")
    if kind == "EntityRef":
        _check_ref(project, fdef, cast(str, value), where, owner)


def _check_ref(
    project: Project, fdef: FieldDef, target: str, where: Locations, owner: tuple[int, str]
) -> None:
    """照欄位定義的 allowedRefs 與 allowOutOfLevelRef 檢查（跟 LDtk 一樣）。"""
    name = fdef["identifier"]
    if target not in where:
        raise SceneError(f"欄位 {name} 指向不存在的 entity {target}")
    _, level_iid, def_uid = where[target]
    owner_def, owner_level = owner
    if not fdef["allowOutOfLevelRef"] and level_iid != owner_level:
        raise SceneError(f"欄位 {name} 只能指向同一個 level 的 entity")
    allowed = fdef["allowedRefs"]
    if allowed == "OnlySame" and def_uid != owner_def:
        raise SceneError(f"欄位 {name} 只能指向同一種 entity")
    if allowed == "OnlySpecificEntity" and def_uid != fdef["allowedRefsEntityUid"]:
        raise SceneError(f"欄位 {name} 只能指向定義指定的那一種 entity")
    if allowed == "OnlyTags":
        none: list[str] = []
        tags = next((d["tags"] for d in project["defs"]["entities"] if d["uid"] == def_uid), none)
        if not set(tags) & set(fdef["allowedRefTags"]):
            raise SceneError(f"欄位 {name} 只能指向帶 {fdef['allowedRefTags']} 標籤的 entity")


def set_value(instance: FieldInstance, fdef: FieldDef, value: object) -> None:
    """寫進 `realEditorValues`；`__value` 留給 refresh 重算。值要先經過 check_value。

    清空寫成 `[null]`（LDtk 清掉一個設過的值也這樣寫），有預設值的欄位清掉才會是空的。
    """
    if value is None:
        instance["realEditorValues"] = [None]
    else:
        instance["realEditorValues"] = [{"id": _EDITOR_IDS[fdef["__type"]], "params": [value]}]


def raw_value(instance: FieldInstance, fdef: FieldDef) -> object:
    """欄位的值：設過的值，否則是預設值。EntityRef 是目標的 iid。

    `realEditorValues` 是 `[]` 表示沒設過（用預設值）；`[null]` 表示明確設成空的。
    """
    for entry in instance["realEditorValues"]:
        if not isinstance(entry, dict):
            return None
        params = cast(dict[str, object], entry).get("params")
        if isinstance(params, list) and params:
            return cast(list[object], params)[0]
        return None
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


def has_refs(project: Project) -> bool:
    """場景地圖裡有沒有 EntityRef 欄位；沒有就不用建 locations。"""
    return any(
        f["__type"] == "EntityRef" for d in project["defs"]["entities"] for f in d["fieldDefs"]
    )


def parse_text(fdef: FieldDef, text: str) -> object:
    """把文字照欄位型別轉成值（CLI 用）；`null` 是清空，String 欄位的文字照原樣。"""
    kind = fdef["__type"]
    if text == "null" and kind != "String":
        return None
    try:
        if kind == "Int":
            return int(text)
        if kind == "Float":
            return float(text)
    except ValueError as error:
        raise SceneError(f"欄位 {fdef['identifier']} 要是 {kind}：{text!r}") from error
    if kind == "Bool":
        if text not in ("true", "false"):
            raise SceneError(f"欄位 {fdef['identifier']} 要是 true 或 false：{text!r}")
        return text == "true"
    return text


def locations(project: Project) -> Locations:
    """整個場景地圖裡每個 entity 在哪個圖層、哪個 level；EntityRef 可以跨 level。"""
    found: Locations = {}
    for level in project["levels"]:
        for layer in level["layerInstances"] or []:
            for entity in layer["entityInstances"]:
                found[entity["iid"]] = (layer["iid"], level["iid"], entity["defUid"])
    return found


def refresh_fields(
    project: Project, entity: EntityInstance, edef: EntityDef, where: Locations
) -> None:
    """欄位實例跟定義一一對應（順序也一樣），並重算管得到的欄位的 `__` 欄位。

    對不上任何定義的實例照原樣留著，排在最後。

    指向已經不存在的 entity 的 EntityRef，`__value` 是 null。
    """
    # 同一個定義有好幾個實例時（手改過的檔），第一個是它的，其餘當成對不上的原樣留著
    existing: dict[object, FieldInstance] = {}
    for f in entity["fieldInstances"]:
        existing.setdefault(cast(dict[str, object], f).get("defUid"), f)
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
    kept = {id(f) for f in rebuilt}
    rebuilt += [f for f in entity["fieldInstances"] if id(f) not in kept]
    entity["fieldInstances"] = rebuilt


def _ref_value(project: Project, target: object, where: Locations) -> object:
    if not isinstance(target, str) or target not in where:
        return None
    layer_iid, level_iid, _ = where[target]
    return {
        "entityIid": target,
        "layerIid": layer_iid,
        "levelIid": level_iid,
        "worldIid": project["dummyWorldIid"],
    }


def clear_refs_to(project: Project, iid: str) -> bool:
    """把所有指向 `iid` 的 EntityRef 清成空的（刪掉 entity 時用，LDtk 也這樣做）。

    回傳有沒有清到。
    """
    defs: dict[object, FieldDef] = {
        f["uid"]: f
        for d in project["defs"]["entities"]
        for f in d["fieldDefs"]
        if f["__type"] == "EntityRef" and not f["isArray"]
    }
    cleared = False
    for level in project["levels"]:
        for layer in level["layerInstances"] or []:
            for entity in layer["entityInstances"]:
                for instance in entity["fieldInstances"]:
                    fdef = defs.get(cast(dict[str, object], instance).get("defUid"))
                    if fdef is not None and raw_value(instance, fdef) == iid:
                        instance["realEditorValues"] = [None]
                        cleared = True
    return cleared
