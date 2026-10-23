"""LDtk 1.5.3 JSON 的精簡型別（D-006）。

只宣告會讀寫的欄位；其餘欄位照範本原樣留在 dict 裡。
新的物件一律從範本複製，不憑空組 dict。
"""

from typing import TypedDict


class IntGridValueDef(TypedDict):
    value: int
    identifier: str | None
    color: str
    tile: object | None
    groupUid: int


class LayerDef(TypedDict):
    identifier: str
    type: str
    uid: int
    gridSize: int
    displayOpacity: float
    pxOffsetX: int
    pxOffsetY: int
    intGridValues: list[IntGridValueDef]
    tilesetDefUid: int | None
    requiredTags: list[str]
    excludedTags: list[str]
    autoRuleGroups: list[object]
    autoSourceLayerDefUid: int | None


# 欄位定義與實例：只寫用到的欄位；其餘照範本原樣保留（D-006）
FieldDef = TypedDict(
    "FieldDef",
    {
        "identifier": str,
        "uid": int,
        "type": str,
        "__type": str,
        "isArray": bool,
        "canBeNull": bool,
        "defaultOverride": object,
    },
)

FieldInstance = TypedDict(
    "FieldInstance",
    {
        "__identifier": str,
        "__type": str,
        "__value": object,
        "__tile": object,
        "defUid": int,
        "realEditorValues": list[object],
    },
)


class EntityDef(TypedDict):
    identifier: str
    uid: int
    width: int
    height: int
    color: str
    pivotX: float
    pivotY: float
    tags: list[str]
    fieldDefs: list[FieldDef]
    tileRect: object
    maxCount: int
    limitScope: str
    limitBehavior: str
    exportToToc: bool


class TilesetDef(TypedDict):
    uid: int
    relPath: str | None


class Definitions(TypedDict):
    layers: list[LayerDef]
    entities: list[EntityDef]
    tilesets: list[TilesetDef]


# 有 `__` 開頭欄位的型別用函式寫法：class 寫法會把 `__x` 改名（name mangling）。
EntityInstance = TypedDict(
    "EntityInstance",
    {
        "__identifier": str,
        "__grid": list[int],
        "__pivot": list[float],
        "__tags": list[str],
        "__tile": object,
        "__smartColor": str,
        "__worldX": int | None,
        "__worldY": int | None,
        "iid": str,
        "width": int,
        "height": int,
        "defUid": int,
        "px": list[int],
        "fieldInstances": list[FieldInstance],
    },
)

LayerInstance = TypedDict(
    "LayerInstance",
    {
        "__identifier": str,
        "__type": str,
        "__cWid": int,
        "__cHei": int,
        "__gridSize": int,
        "__opacity": float,
        "__pxTotalOffsetX": int,
        "__pxTotalOffsetY": int,
        "__tilesetDefUid": int | None,
        "__tilesetRelPath": str | None,
        "iid": str,
        "levelId": int,
        "layerDefUid": int,
        "pxOffsetX": int,
        "pxOffsetY": int,
        "visible": bool,
        "seed": int,
        "overrideTilesetUid": int | None,
        "intGridCsv": list[int],
        "gridTiles": list[object],
        "autoLayerTiles": list[object],
        "entityInstances": list[EntityInstance],
    },
)

Level = TypedDict(
    "Level",
    {
        "identifier": str,
        "iid": str,
        "uid": int,
        "worldX": int,
        "worldY": int,
        "worldDepth": int,
        "pxWid": int,
        "pxHei": int,
        "__bgColor": str,
        "bgColor": str | None,
        "useAutoIdentifier": bool,
        "externalRelPath": str | None,
        "layerInstances": list[LayerInstance] | None,
        "__neighbours": list[object],
    },
)


class Project(TypedDict):
    iid: str
    worldLayout: str | None
    jsonVersion: str
    nextUid: int
    defaultGridSize: int
    defaultLevelBgColor: str
    externalLevels: bool
    dummyWorldIid: str
    defs: Definitions
    levels: list[Level]
