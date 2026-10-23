"""iid 的產生（D-006）。uid 與 nextUid 的配發在 derived.refresh_derived。"""

import uuid


def new_iid() -> str:
    """LDtk 的 iid：UUID 字串。"""
    return str(uuid.uuid4())


# 生成器的 iid 用的命名空間；固定不變，改了等於所有生成的 iid 都換掉
_GENERATED = uuid.uuid5(uuid.NAMESPACE_URL, "ldtk-map-creator/generated")


def derived_iid(seed: int, key: str) -> str:
    """由 seed 加名字推出的 iid（D-007）：同 seed 同名字，iid 就相同。"""
    return str(uuid.uuid5(_GENERATED, f"{seed}:{key}"))
