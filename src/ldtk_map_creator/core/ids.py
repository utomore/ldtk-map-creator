"""iid 的產生（D-006）。uid 與 nextUid 的配發在 derived.refresh_derived。"""

import uuid


def new_iid() -> str:
    """LDtk 的 iid：UUID 字串。"""
    return str(uuid.uuid4())
