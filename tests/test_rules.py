"""RULE-1、RULE-2：讀取並檢查規則檔。"""

import copy
import dataclasses
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.rules import (
    AreaRule,
    EntityRule,
    Kind,
    LayerValue,
    LevelRule,
    PathRule,
    PortalRule,
    Rules,
    load_rules,
    parse_rules,
)
from tests.rule_strategies import multi_rules, rules, to_toml_data, width_limit

FAST = settings(max_examples=300, deadline=None)
BROKEN = settings(max_examples=1000, deadline=None)
EXAMPLE = Path(__file__).parent / "data" / "village.rules.toml"
MISSING = "zz_missing_ref"  # 比策略產生的名稱長，不會撞名
BAD_VALUES: list[object] = [True, 1.5, {}, [], "x?"]


@pytest.mark.law("RULE-1")
@FAST
@given(rules() | multi_rules(), st.booleans(), st.integers(1, 256))
def test_valid_rules_round_trip(expected: Rules, array: bool, grid: int) -> None:
    # 只有一個 level 時，M2 的 [level] 與 D-009 的 [[level]] 兩種寫法都讀得回原樣
    assert parse_rules(to_toml_data(expected, array=array)) == expected
    # 頂層的 grid 照檔案保留；沒寫是 16
    assert expected.grid == 16
    sized = dataclasses.replace(expected, grid=grid)
    assert parse_rules(to_toml_data(sized, array=array)) == sized


def _elements(data: dict[str, Any]) -> list[tuple[str, int, dict[str, Any]]]:
    return [
        (key, i, e) for key in ("area", "path", "entity") for i, e in enumerate(data.get(key, []))
    ]


@st.composite
def broken(draw: st.DrawFn) -> tuple[dict[str, Any], str]:
    """合法的規則弄壞一處；回傳弄壞後的資料，與訊息裡該出現的位置。"""
    good = draw(rules())
    data = copy.deepcopy(to_toml_data(good))
    elements = _elements(data)
    kinds: dict[str, dict[str, Any]] = data["kinds"]
    sections: dict[str, dict[str, dict[str, Any]]] = {
        key: data[key] for key in ("grounds", "collisions") if key in data
    }
    options = ["top_key", "level_key", "level_size", "bad_seed", "kind_old_format", "kind_empty"]
    options += ["kind_field", "kind_part", "bool_int", "level_reserved", "bad_grid"]
    if sections:
        options += ["layer_value", "layer_color", "layer_key"]
    if elements:
        options += ["element_key", "bad_type", "missing_kind_or_ref"]
    if good.paths or any(a.near for a in good.areas):
        options.append("self_ref")
    if good.paths:
        options += ["path_same_ends", "path_too_wide", "path_parted_kind"]
    if len(elements) >= 2:
        options.append("duplicate")
    if any(a.at is not None for a in good.areas):
        options.append("two_cycle")
    if good.areas:
        options.append("area_size")
    if good.entities:
        options.append("entity_out")
    match draw(st.sampled_from(options)):
        case "top_key":
            data["extra"] = 1
            return data, "規則檔.extra"
        case "level_key":
            data["level"]["extra"] = 1
            return data, "level.extra"
        case "level_size":
            data["level"]["size"][draw(st.integers(0, 1))] = draw(st.integers(-3, 0))
            return data, "level.size"
        case "bad_grid":
            # grid 要是 1 到 1024 的整數：0、負數、小數、字串、布林、超過上限都擋下來
            data["grid"] = draw(
                st.sampled_from([0, -1, -96, 1.5, 96.0, "96", True, False, [], 1025])
            )
            return data, "grid"
        case "bad_seed":
            data["seed"] = draw(st.sampled_from(BAD_VALUES))
            return data, "seed"
        case "bool_int":
            # TOML 的 true 在 Python 是 int 的子類別，整數欄位要擋下來
            slots: list[tuple[dict[str, Any] | list[Any], str | int, str]] = [
                (data, "seed", "seed"),
                (data["level"]["size"], 0, "level.size"),
            ]
            slots += [
                (v, "value", f"{key}.{n}.value")
                for key, sec in sections.items()
                for n, v in sec.items()
            ]
            slots += [(k, "height", f"kinds.{n}.height") for n, k in kinds.items() if "height" in k]
            for _, _, e in elements:
                for field in ("size", "at"):
                    if isinstance(e.get(field), list):
                        slots.append((e[field], 1, f"{e['name']}.{field}"))
                slots += [(e, f, f"{e['name']}.{f}") for f in ("gap", "width") if f in e]
            box, key, where = draw(st.sampled_from(slots))
            box[key] = True  # type: ignore[index]
            return data, where
        case "kind_old_format":
            # D-011 之前的寫法：圖格種類裡的 value 或 color
            name = draw(st.sampled_from(sorted(kinds)))
            field = draw(st.sampled_from(["value", "color"]))
            kinds[name][field] = 1 if field == "value" else "#000000"
            return data, f"kinds.{name}.{field}"
        case "kind_empty":
            name = draw(st.sampled_from(sorted(kinds)))
            kinds[name].clear()
            return data, f"kinds.{name}"
        case "kind_field":
            name = draw(st.sampled_from(sorted(kinds)))
            field = draw(st.sampled_from(["ground", "collision", "height", "slope", "extra"]))
            bad: dict[str, list[object]] = {
                "ground": [MISSING, "x?", 1, *BAD_VALUES],
                "collision": [MISSING, "x?", 1, *BAD_VALUES],
                "height": [-1, *BAD_VALUES, "1"],
                "slope": [0, 1, "yes", 1.5, {}, []],
                "extra": [1],
            }
            kinds[name][field] = draw(st.sampled_from(bad[field]))
            return data, f"kinds.{name}.{field}"
        case "kind_part":
            # D-013：part 要是不是空白的字串、yaw 要是有限的數字、只寫 yaw 沒寫 part 也不行
            name = draw(st.sampled_from(sorted(kinds)))
            field = draw(st.sampled_from(["part", "yaw", "yaw_only"]))
            bad_parts: list[object] = ["", " ", "	", 1, True, [], {}]
            bad_yaws: list[object] = [True, "90", [], {}, float("inf"), float("-inf"), float("nan")]
            if field == "part":
                kinds[name]["part"] = draw(st.sampled_from(bad_parts))
            elif field == "yaw":
                kinds[name]["part"] = "p"
                kinds[name]["yaw"] = draw(st.sampled_from(bad_yaws))
            else:
                kinds[name].pop("part", None)
                kinds[name]["yaw"] = draw(st.sampled_from([0, 90, 1.5]))
                field = "yaw"
            return data, f"kinds.{name}.{field}"
        case "path_parted_kind":
            # D-013：路徑用了帶零件的種類；報在第一個用它的路徑
            path = draw(st.sampled_from(data["path"]))
            kinds[path["kind"]]["part"] = "p"
            first = next(p for p in data["path"] if p["kind"] == path["kind"])
            return data, f"{first['name']}.kind"
        case "layer_value":
            key = draw(st.sampled_from(sorted(sections)))
            sec = sections[key]
            name = draw(st.sampled_from(sorted(sec)))
            others = [v["value"] for n, v in sec.items() if n != name]
            # 不放 True：True == 1 會跟值是 1 的值算成撞值；bool_int 另外測
            wrong = [v for v in BAD_VALUES if v is not True]
            sec[name]["value"] = draw(st.sampled_from([*wrong, 0, -1, *others]))
            # 撞到的值會報在排在後面的那個
            order = list(sec)
            clash = [n for n in order if n != name and sec[n]["value"] == sec[name]["value"]]
            later = max([name, *clash], key=order.index)
            return data, f"{key}.{later}.value"
        case "layer_color":
            key = draw(st.sampled_from(sorted(sections)))
            name = draw(st.sampled_from(sorted(sections[key])))
            sections[key][name]["color"] = draw(
                st.sampled_from([*BAD_VALUES, "#12345", "red", "#GGGGGG"])
            )
            return data, f"{key}.{name}.color"
        case "layer_key":
            key = draw(st.sampled_from(sorted(sections)))
            name = draw(st.sampled_from(sorted(sections[key])))
            sections[key][name]["extra"] = 1
            return data, f"{key}.{name}.extra"
        case "element_key":
            _, _, element = draw(st.sampled_from(elements))
            element["extra"] = 1
            return data, f"{element['name']}.extra"
        case "bad_type":
            key, i, element = draw(st.sampled_from(elements))
            field = draw(st.sampled_from(sorted(element)))
            element[field] = draw(st.sampled_from(BAD_VALUES))
            where = f"{key}[{i}]" if field == "name" else element["name"]
            return data, f"{where}.{field}"
        case "missing_kind_or_ref":
            _, _, element = draw(st.sampled_from(elements))
            fields = [f for f in ("kind", "near", "from", "to") if f in element]
            if isinstance(element.get("at"), str) and element["at"] != "center":
                fields.append("at")
            if not fields:  # entity 擺在固定的格：換成不存在的錨點
                fields = ["at"]
            field = draw(st.sampled_from(fields))
            element[field] = f"{MISSING}.end" if field == "at" else MISSING
            return data, f"{element['name']}.{field}"
        case "self_ref":
            refs = [e for _, _, e in elements if "near" in e or "from" in e]
            element = draw(st.sampled_from(refs))
            field = "near" if "near" in element else "from"
            element[field] = element["name"]
            return data, f"{element['name']}.{field}"
        case "duplicate":
            i, j = sorted(
                draw(
                    st.lists(st.integers(0, len(elements) - 1), min_size=2, max_size=2, unique=True)
                )
            )
            elements[j][2]["name"] = elements[i][2]["name"]
            return data, f"{elements[i][2]['name']}.name"
        case "two_cycle":
            # x 原本固定位置，改成靠近一個直接或間接指向 x 的元素 y，形成 x → y → … → x
            fixed = [e for e in data["area"] if "at" in e]
            x = draw(st.sampled_from(fixed))
            refs = ("near", "from", "to")
            pointing = [(e, f) for _, _, e in elements for f in refs if e.get(f) == x["name"]]
            # 每個元素沿著參照走回 x 的最長距離；不成環，所以算到不再變就停
            depth = {x["name"]: 0}
            changed = True
            while changed:
                changed = False
                for _, _, e in elements:
                    d = max((depth[e[f]] + 1 for f in refs if e.get(f) in depth), default=0)
                    if d > depth.get(e["name"], 0):
                        depth[e["name"]] = d
                        changed = True
            if len(depth) == 1:
                x["near"] = x["name"]
                del x["at"]
                return data, f"{x['name']}.near"
            # 挑最長的環，三個以上元素的環才測得到
            x["near"] = max(depth, key=lambda n: depth[n])
            del x["at"]
            # 其他元素都只指向排在前面的，所以環一定經過 x，由某條指向 x 的邊報出
            return data, "|".join(f"{e['name']}.{f}" for e, f in pointing)
        case "path_too_wide":
            index = draw(st.integers(0, len(good.paths) - 1))
            path = data["path"][index]
            path["width"] = width_limit(good.paths[index].to, good.level_size) + 1
            return data, f"{path['name']}.width"
        case "level_reserved":
            data["level"]["name"] = draw(st.sampled_from(["con", "NUL", "Com1", "lpt9"]))
            return data, "level.name"
        case "path_same_ends":
            path = draw(st.sampled_from(data["path"]))
            path["to"] = path["from"]
            return data, f"{path['name']}.to"
        case "area_size":
            area = draw(st.sampled_from(data["area"]))
            w, h = data["level"]["size"]
            area["size"] = draw(st.sampled_from([[0, 1], [1, -2], [w + 1, 1], [1, h + 1]]))
            return data, f"{area['name']}.size"
        case _:  # entity_out
            entity = draw(st.sampled_from(data["entity"]))
            w, h = data["level"]["size"]
            entity["at"] = draw(st.sampled_from([[w, 0], [0, h], [-1, 0]]))
            return data, f"{entity['name']}.at"


@st.composite
def broken_multi(draw: st.DrawFn) -> tuple[dict[str, Any], str]:
    """多個 level、entity 類型、傳送點的規則弄壞一處（D-009、D-010）。"""
    good = draw(multi_rules())
    data = copy.deepcopy(to_toml_data(good, array=True))
    levels: list[dict[str, Any]] = data["level"]
    elements = [(k, e) for k, _, e in _elements(data)]
    portals: list[dict[str, Any]] = data.get("portal", [])
    types: dict[str, dict[str, Any]] = data.get("entity_types", {})
    options = ["level_dup", "type_bad_color", "type_bad_field", "type_bad_tags", "level_key"]
    if len(levels) > 1:
        options += ["level_near_missing", "level_no_position", "level_cycle"]
        if elements:
            options.append("element_no_level")
    if elements:
        options.append("element_level_unknown")
    first = levels[0]["name"]  # 沒寫 level 的元素在第一個 level
    across = [
        (e, other)
        for k, e in elements
        if k in ("area", "path")
        for k2, other in elements
        if k2 in ("area", "path") and other.get("level", first) != e.get("level", first)
    ]
    if across:
        options.append("cross_level_ref")
    if portals:
        options += ["portal_anchor", "portal_same_ends", "portal_type"]
    entities: list[dict[str, Any]] = data.get("entity", [])
    if entities:
        options += ["field_unknown", "field_wrong_type", "field_ref"]
    match draw(st.sampled_from(options)):
        case "level_dup":
            if len(levels) == 1:
                levels.append(dict(levels[0], name=levels[0]["name"].swapcase(), at=[0, 0]))
                levels[-1].pop("near", None)
                if levels[-1]["name"] == levels[0]["name"]:  # 沒有大小寫的名字（例如 _1）
                    return data | {"extra": 1}, "規則檔.extra"
                return data, f"level.{levels[-1]['name']}.name"
            levels[-1]["name"] = levels[0]["name"].upper()
            return data, f"level.{levels[-1]['name']}.name|level.{levels[0]['name']}.name"
        case "level_key":
            draw(st.sampled_from(levels))["extra"] = 1
            return data, "|".join(f"level.{lv['name']}.extra" for lv in levels)
        case "type_bad_color":
            if not types:
                return data | {"extra": 1}, "規則檔.extra"
            name = draw(st.sampled_from(sorted(types)))
            types[name]["color"] = "blue"
            return data, f"entity_types.{name}.color"
        case "type_bad_field":
            if not types:
                return data | {"extra": 1}, "規則檔.extra"
            name = draw(st.sampled_from(sorted(types)))
            types[name]["fields"]["zz"] = "Color"
            return data, f"entity_types.{name}.fields.zz"
        case "type_bad_tags":
            if not types:
                return data | {"extra": 1}, "規則檔.extra"
            name = draw(st.sampled_from(sorted(types)))
            types[name]["tags"] = draw(
                st.sampled_from(["world", [1], [""], ["  "], ["a", "a"], {"a": 1}, None])
            )
            return data, f"entity_types.{name}.tags"
        case "level_near_missing":
            level = draw(st.sampled_from(levels[1:]))
            level.pop("at", None)
            level["near"] = MISSING
            return data, f"level.{level['name']}.near"
        case "level_no_position":
            level = draw(st.sampled_from(levels[1:]))
            for key in ("at", "near", "side", "gap"):
                level.pop(key, None)
            return data, f"level.{level['name']}.at"
        case "level_cycle":
            # 第一個 level 改成靠近第二個，第二個改成靠近第一個
            first, second = levels[0], levels[1]
            for level, other in ((first, second), (second, first)):
                for key in ("at", "near", "side", "gap"):
                    level.pop(key, None)
                level["near"] = other["name"]
            return data, f"level.{first['name']}.near|level.{second['name']}.near"
        case "element_no_level":
            _, element = draw(st.sampled_from(elements))
            element.pop("level", None)
            return data, f"{element['name']}.level"
        case "element_level_unknown":
            _, element = draw(st.sampled_from(elements))
            element["level"] = MISSING
            return data, f"{element['name']}.level"
        case "cross_level_ref":
            element, other = draw(st.sampled_from(across))
            field = "near" if "near" in element or "at" in element else "from"
            if field == "near":
                element.pop("at", None)
                element.pop("side", None)
                element.pop("gap", None)
            element[field] = other["name"]
            return data, f"{element['name']}.{field}"
        case "portal_anchor":
            portal = draw(st.sampled_from(portals))
            field = draw(st.sampled_from(["from", "to"]))
            portal[field] = f"{MISSING}.center"
            return data, f"{portal['name']}.{field}"
        case "portal_same_ends":
            portal = draw(st.sampled_from(portals))
            portal["to"] = portal["from"]
            return data, f"{portal['name']}.to"
        case "portal_type":
            # 傳送點一律是 Teleport，寫了 type 就錯，寫什麼值都一樣
            portal = draw(st.sampled_from(portals))
            portal["type"] = draw(st.sampled_from(["Teleport", "Part", "ZzNope", *types]))
            return data, f"{portal['name']}.type"
        case "field_unknown":
            entity = draw(st.sampled_from(entities))
            entity["type"] = "Part"
            entity["fields"] = {"zz": 1}
            return data, f"{entity['name']}.fields.zz"
        case "field_wrong_type":
            # 值的型別跟那個欄位的定義不合：part 是 String、yaw 是 Float
            entity = draw(st.sampled_from(entities))
            entity["type"] = "Part"
            field, value = draw(st.sampled_from([("part", 3), ("part", True), ("yaw", "90")]))
            entity["fields"] = {field: value}
            return data, f"{entity['name']}.fields.{field}"
        case _:  # field_ref
            # EntityRef 欄位不能寫在 fields 裡（傳送點用 [[portal]]）
            entity = draw(st.sampled_from(entities))
            entity["type"] = "Teleport"
            entity["fields"] = {"targetPort": "x"}
            return data, f"{entity['name']}.fields.targetPort"


@pytest.mark.law("RULE-2")
@BROKEN
@given(broken() | broken_multi())
def test_broken_rules_are_rejected(case: tuple[dict[str, Any], str]) -> None:
    data, wheres = case
    with pytest.raises(SceneError) as error:
        parse_rules(data)
    message = str(error.value)
    assert any(message.startswith(f"{w}：") for w in wheres.split("|")), (wheres, message)


def test_example_village() -> None:
    """典型的規則檔：每種元素與錨點各用一次，照檔案順序讀出。"""
    r = load_rules(EXAMPLE)
    assert (r.seed, r.level_name, r.level_size) == (7, "Village", (40, 30))
    assert r.kinds == (
        Kind("plaza", ground="dirt"),
        Kind("house", ground="dirt", collision="wall"),
        Kind("road", ground="stone"),
    )
    assert r.grounds == (LayerValue("dirt", 1, "#C8B27A"), LayerValue("stone", 2, "#8E8E8E"))
    assert r.collisions == (LayerValue("wall", 1, "#8A4B2F"),)
    assert r.areas[0] == AreaRule("plaza", "plaza", (8, 8), at="center")
    assert r.areas[2] == AreaRule("house_2", "house", (6, 5), near="plaza", side="east", gap=3)
    assert r.paths == (PathRule("main_road", "road", "plaza", "edge:south", 2),)
    assert r.entities == (EntityRule("spawn", "PlayerSpawn", "main_road.end"),)


def test_example_grid_sets_default_entity_size() -> None:
    """典型：頂層 grid = 96；沒寫 size 的 entity 類型是一格大，寫了 size 的照寫（像素）。"""
    data = to_toml_data(load_rules(EXAMPLE))
    data["grid"] = 96
    data["entity_types"] = {"Sign": {}, "Tower": {"size": [96, 192]}}
    found = parse_rules(data)
    assert found.grid == 96
    assert [(t.name, t.size) for t in found.entity_types] == [
        ("Sign", (96, 96)),
        ("Tower", (96, 192)),
    ]


def test_toml_syntax_error(tmp_path: Path) -> None:
    """TOML 本身寫錯也是 SceneError，訊息帶檔名。"""
    path = tmp_path / "bad.rules.toml"
    path.write_text("seed = \n", encoding="utf-8")
    with pytest.raises(SceneError, match=r"bad.rules.toml"):
        load_rules(path)


def test_23_long_reference_chain() -> None:
    """曾經壞過：一長串 near 讓成環檢查超過遞迴上限，丟出 RecursionError（#23 審查）。"""
    # 每個區域靠近排在後面的那個，檢查從第一個走起就要走完整條
    areas: list[dict[str, Any]] = [
        {"name": f"a{i}", "kind": "k", "size": [1, 1], "near": f"a{i + 1}"} for i in range(2999)
    ]
    areas.append({"name": "a2999", "kind": "k", "size": [1, 1], "at": "center"})
    data = {
        "seed": 1,
        "level": {"name": "L", "size": [4, 4]},
        "grounds": {"g": {"value": 1, "color": "#000000"}},
        "kinds": {"k": {"ground": "g"}},
        "area": areas,
    }
    assert len(parse_rules(data).areas) == 3000


def test_example_village_with_house() -> None:
    """典型（D-009、D-010、D-011）：兩個 level、一個跨 level 的傳送點、一個帶欄位值的 Part。"""
    r = load_rules(EXAMPLE.with_name("village_house.rules.toml"))
    assert [lv.name for lv in r.levels] == ["Village", "House1"]
    assert r.levels[1] == LevelRule("House1", (12, 10), near="Village", side="east", gap=2)
    assert r.entity_types == ()
    assert r.portals == (PortalRule("house_1_door", "house_1.south", "room.south"),)
    assert r.entities == (
        EntityRule(
            "oak_1", "Part", "plaza.east", level="Village", fields=(("part", "oak"), ("yaw", 90))
        ),
    )
    assert r.level_of(r.areas[2]).name == "House1"


@pytest.mark.parametrize(
    ("case", "where"),
    [("area-near-other-level", "house_1.near"), ("entity-at-other-level", "spawn.at")],
)
def test_example_only_portals_cross_levels(case: str, where: str) -> None:
    """只有傳送點能跨 level：區域靠近、entity 擺在別的 level 的東西都報錯。"""
    text = EXAMPLE.with_name("village_house.rules.toml").read_text(encoding="utf-8")
    data: dict[str, Any] = tomllib.loads(text)
    if case == "area-near-other-level":
        data["area"][1]["near"] = "room"  # house_1 在 Village，room 在 House1
    else:
        spawn = {"name": "spawn", "type": "PlayerSpawn", "at": "room.center", "level": "Village"}
        data["entity"] = [spawn]
    with pytest.raises(SceneError, match=rf"^{re.escape(where)}："):
        parse_rules(data)


@pytest.mark.parametrize(
    ("case", "where"),
    [
        ("portal-same-cell", "door_portal.to"),
        ("type-named-spawn", "entity_types.playerspawn"),
        ("type-named-teleport", "entity_types.Teleport"),
        ("type-named-part", "entity_types.Part"),
    ],
)
def test_example_portal_and_type_mistakes(case: str, where: str) -> None:
    """傳送點兩端在同一格、類型跟範本的 PlayerSpawn、Teleport、Part 撞名（D-011），都報錯。

    1 格高的區域，north 與 south 是同一格。
    """
    text = EXAMPLE.with_name("village_house.rules.toml").read_text(encoding="utf-8")
    data: dict[str, Any] = tomllib.loads(text)
    if case == "portal-same-cell":
        door = {"name": "door", "level": "Village", "kind": "house", "size": [3, 1], "at": [0, 0]}
        data["area"].append(door)
        data["portal"].append({"name": "door_portal", "from": "door.north", "to": "door.south"})
    else:
        data.setdefault("entity_types", {})[where.split(".")[1]] = {}
    with pytest.raises(SceneError, match=rf"^{re.escape(where)}："):
        parse_rules(data)


def test_example_portal_with_type_is_refused() -> None:
    """舊寫法（D-010 原本的 type）：傳送點一律是 Teleport，寫了 type 報錯並說明（D-011）。"""
    text = EXAMPLE.with_name("village_house.rules.toml").read_text(encoding="utf-8")
    data: dict[str, Any] = tomllib.loads(text)
    data["portal"][0]["type"] = "Portal"
    with pytest.raises(SceneError, match=r"^house_1_door\.type：傳送點一律是 Teleport（D-011）"):
        parse_rules(data)


@pytest.mark.parametrize(
    ("entity_type", "fields", "where"),
    [
        ("Part", {"part": "oak", "yaw": 90}, None),  # Float 也收整數
        ("Part", {"part": "oak", "yaw": 1.5}, None),
        ("Part", {"yaw": 1.5}, "oak.fields.part"),  # Part 要寫零件名稱
        ("Part", {"part": " "}, "oak.fields.part"),
        ("Teleport", {}, "oak.type"),  # Teleport 要成對，用 [[portal]]
        ("Part", {"yaw": True}, "oak.fields.yaw"),  # bool 不是數字
        ("Part", {"part": 1}, "oak.fields.part"),
        ("Part", {"nope": 1}, "oak.fields.nope"),
        ("Teleport", {"targetPort": "x"}, "oak.fields.targetPort"),  # 指向用 [[portal]]
        ("Marker", {"count": 1, "ok": False, "ratio": 2, "note": "n"}, None),
        ("Marker", {"count": 1.5}, "oak.fields.count"),  # Int 不收小數
        ("Marker", {"count": 2**31}, "oak.fields.count"),  # LDtk 的 Int 是 32 位元
        ("Typo", {"when": [1]}, "oak.fields.when"),  # 類型不存在也要是純量
        ("Marker", {"ok": 1}, "oak.fields.ok"),  # Bool 不收整數
        ("Marker", {"link": "x"}, "oak.fields.link"),
    ],
)
def test_example_entity_field_values(
    entity_type: str, fields: dict[str, Any], where: str | None
) -> None:
    """[[entity]] 的 fields 照那個類型的欄位定義檢查（範本的三種，或 [entity_types] 宣告的）。"""
    data: dict[str, Any] = {
        "seed": 1,
        "level": {"name": "L", "size": [4, 4]},
        "grounds": {"g": {"value": 1, "color": "#000000"}},
        "kinds": {"k": {"ground": "g"}},
        "entity_types": {
            "Marker": {
                "fields": {
                    "count": "Int",
                    "ok": "Bool",
                    "ratio": "Float",
                    "note": "String",
                    "link": "EntityRef",
                }
            }
        },
        "entity": [{"name": "oak", "type": entity_type, "at": [0, 0], "fields": fields}],
    }
    if where is None:
        assert dict(parse_rules(data).entities[0].fields) == fields
        return
    with pytest.raises(SceneError, match=rf"^{re.escape(where)}："):
        parse_rules(data)


def test_example_level_at_can_be_negative() -> None:
    """level 的世界座標可以是負的（LDtk 的 Free 佈局）。"""
    text = EXAMPLE.with_name("village_house.rules.toml").read_text(encoding="utf-8")
    data: dict[str, Any] = tomllib.loads(text)
    data["level"][1] = {"name": "House1", "size": [12, 10], "at": [-20, -5]}
    assert parse_rules(data).levels[1].at == (-20, -5)


def test_example_level_of_unknown_level_is_scene_error() -> None:
    """沒經過檢查、直接組出來的規則指向不存在的 level：丟 SceneError，不丟 StopIteration。"""
    r = load_rules(EXAMPLE)
    lost = AreaRule("lost", "plaza", (1, 1), at=(0, 0), level="Nope")
    with pytest.raises(SceneError, match=r"^lost\.level"):
        r.level_of(lost)


def test_example_fixed_levels_must_not_overlap() -> None:
    """兩個都用 at 固定位置的 level 重疊：讀規則檔時就報錯（D-009：level 彼此不重疊）。"""
    text = EXAMPLE.with_name("village_house.rules.toml").read_text(encoding="utf-8")
    data: dict[str, Any] = tomllib.loads(text)
    data["level"][1] = {
        "name": "House1",
        "size": [12, 10],
        "at": [10, 10],
    }  # Village 在 (0,0) 40x30
    with pytest.raises(SceneError, match=r"^level\.House1\.at："):
        parse_rules(data)


def test_example_single_table_level_takes_at() -> None:
    """M2 的 `[level]` 寫法也能寫 at，跟 `[[level]]` 一樣。"""
    text = EXAMPLE.read_text(encoding="utf-8").replace(
        "size = [40, 30]  # 格", "size = [40, 30]\nat = [3, -2]"
    )
    assert parse_rules(tomllib.loads(text)).levels[0].at == (3, -2)


D011_RULES = """
seed = 1
[level]
name = "Village"
size = [20, 20]

[grounds]
grass = { value = 1, color = "#7CB342" }
dirt  = { value = 2, color = "#C8B27A" }

[collisions]
wall  = { value = 1, color = "#000000" }
water = { value = 2, color = "#2F4CBE" }

[kinds]
plaza = { ground = "dirt" }
house = { ground = "dirt", collision = "wall" }
hill  = { ground = "grass", height = 2 }
ramp  = { ground = "grass", height = 1, slope = true }
"""


def test_example_d013_kind_parts() -> None:
    """典型與邊界（D-013）：整數的 yaw 存成小數；只有零件、一層都沒寫的種類合法；yaw 沒寫是 0。"""
    data = tomllib.loads(D011_RULES)
    data["kinds"] = {
        "shrine": {"ground": "dirt", "part": "shrine", "yaw": 90},
        "beacon": {"part": "beacon"},
    }
    r = parse_rules(data)
    assert r.kinds == (
        Kind("shrine", ground="dirt", part="shrine", yaw=90.0),
        Kind("beacon", part="beacon"),
    )
    assert isinstance(r.kinds[0].yaw, float)


def test_example_d011_kinds() -> None:
    """典型（D-011）：圖格種類寫進不同的層，沒寫的欄位是預設值；值與顏色住在 [grounds] 等。"""
    r = parse_rules(tomllib.loads(D011_RULES))
    assert [v.name for v in r.grounds] == ["grass", "dirt"]
    assert r.collisions[1] == LayerValue("water", 2, "#2F4CBE")
    assert r.kinds == (
        Kind("plaza", ground="dirt"),
        Kind("house", ground="dirt", collision="wall"),
        Kind("hill", ground="grass", height=2),
        Kind("ramp", ground="grass", height=1, slope=True),
    )


@pytest.mark.parametrize(
    ("kind", "where"),
    [
        ({"value": 1, "color": "#C8B27A"}, "kinds.old.value"),
        ({"color": "#C8B27A", "ground": "dirt"}, "kinds.old.color"),
        ({}, "kinds.old"),
        ({"height": 0}, "kinds.old"),
        ({"slope": False}, "kinds.old"),
        ({"height": 65}, "kinds.old.height"),
        ({"ground": "mud"}, "kinds.old.ground"),
        ({"collision": "dirt"}, "kinds.old.collision"),
        ({"height": -1}, "kinds.old.height"),
        ({"slope": 1}, "kinds.old.slope"),
    ],
)
def test_example_d011_old_format_and_mistakes(kind: dict[str, Any], where: str) -> None:
    """舊寫法（D-011 之前，圖格種類帶 value、color）與新欄位的錯誤：訊息開頭是位置。"""
    data = tomllib.loads(D011_RULES)
    data["kinds"] = {"old": kind}
    with pytest.raises(SceneError, match=rf"^{re.escape(where)}：") as error:
        parse_rules(data)
    if where.endswith((".value", ".color")):
        assert "[grounds]" in str(error.value) and "[collisions]" in str(error.value)


def test_example_anchor_on_area_part_is_refused() -> None:
    """邊界（D-013）：entity 或傳送點的錨點指到帶零件區域的中心，讀規則檔就擋，報在它自己的欄位。"""
    data: dict[str, Any] = {
        "seed": 1,
        "level": {"name": "L", "size": [9, 9]},
        "grounds": {"g": {"value": 1, "color": "#000000"}},
        "kinds": {"house": {"ground": "g", "part": "house"}},
        "area": [{"name": "home", "kind": "house", "size": [3, 3], "at": "center"}],
        "entity": [{"name": "spawn", "type": "PlayerSpawn", "at": "home.center"}],
    }
    with pytest.raises(SceneError, match=r"^spawn\.at：home\.center 是 home 的零件擺的格"):
        parse_rules(data)
    data["entity"][0]["at"] = "home.north"
    parse_rules(data)
