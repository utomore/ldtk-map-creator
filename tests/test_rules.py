"""RULE-1、RULE-2：讀取並檢查規則檔。"""

import copy
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.rules import (
    AreaRule,
    EntityRule,
    PathRule,
    Rules,
    load_rules,
    parse_rules,
)
from tests.rule_strategies import rules, to_toml_data, width_limit

FAST = settings(max_examples=300, deadline=None)
BROKEN = settings(max_examples=1000, deadline=None)
EXAMPLE = Path(__file__).parent / "data" / "village.rules.toml"
MISSING = "zz_missing_ref"  # 比策略產生的名稱長，不會撞名
BAD_VALUES: list[object] = [True, 1.5, {}, [], "x?"]


@pytest.mark.law("RULE-1")
@FAST
@given(rules())
def test_valid_rules_round_trip(expected: Rules) -> None:
    assert parse_rules(to_toml_data(expected)) == expected


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
    options = ["top_key", "level_key", "level_size", "bad_seed", "kind_value", "kind_color"]
    options += ["bool_int", "level_reserved"]
    if elements:
        options += ["element_key", "bad_type", "missing_kind_or_ref"]
    if good.paths or any(a.near for a in good.areas):
        options.append("self_ref")
    if good.paths:
        options += ["path_same_ends", "path_too_wide"]
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
        case "bad_seed":
            data["seed"] = draw(st.sampled_from(BAD_VALUES))
            return data, "seed"
        case "bool_int":
            # TOML 的 true 在 Python 是 int 的子類別，整數欄位要擋下來
            slots: list[tuple[dict[str, Any] | list[Any], str | int, str]] = [
                (data, "seed", "seed"),
                (data["level"]["size"], 0, "level.size"),
            ]
            slots += [(k, "value", f"kinds.{n}.value") for n, k in kinds.items()]
            for _, _, e in elements:
                for field in ("size", "at"):
                    if isinstance(e.get(field), list):
                        slots.append((e[field], 1, f"{e['name']}.{field}"))
                slots += [(e, f, f"{e['name']}.{f}") for f in ("gap", "width") if f in e]
            box, key, where = draw(st.sampled_from(slots))
            box[key] = True  # type: ignore[index]
            return data, where
        case "kind_value":
            name = draw(st.sampled_from(sorted(kinds)))
            others = [k["value"] for n, k in kinds.items() if n != name]
            # 不放 True：True == 1 會跟值是 1 的種類算成撞值；bool_int 另外測
            wrong = [v for v in BAD_VALUES if v is not True]
            kinds[name]["value"] = draw(st.sampled_from([*wrong, 0, -1, *others]))
            # 撞到的值會報在排在後面的那個種類
            order = list(kinds)
            clash = [n for n in order if n != name and kinds[n]["value"] == kinds[name]["value"]]
            later = max([name, *clash], key=order.index)
            return data, f"kinds.{later}.value"
        case "kind_color":
            name = draw(st.sampled_from(sorted(kinds)))
            kinds[name]["color"] = draw(st.sampled_from([*BAD_VALUES, "#12345", "red", "#GGGGGG"]))
            return data, f"kinds.{name}.color"
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


@pytest.mark.law("RULE-2")
@BROKEN
@given(broken())
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
    assert [k.name for k in r.kinds] == ["plaza", "house", "road"]
    assert r.areas[0] == AreaRule("plaza", "plaza", (8, 8), at="center")
    assert r.areas[2] == AreaRule("house_2", "house", (6, 5), near="plaza", side="east", gap=3)
    assert r.paths == (PathRule("main_road", "road", "plaza", "edge:south", 2),)
    assert r.entities == (EntityRule("spawn", "Spawn", "main_road.end"),)


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
        "kinds": {"k": {"value": 1, "color": "#000000"}},
        "area": areas,
    }
    assert len(parse_rules(data).areas) == 3000
