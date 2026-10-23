"""寫檔的共用做法。"""

import contextlib
import time
import uuid
from pathlib import Path

from .errors import SceneError

_RETRIES = 5
_RETRY_DELAY = 0.05


def atomic_write(path: Path, data: bytes) -> None:
    """先寫暫存檔再換名：寫到一半中斷也不會留下半個檔；失敗時清掉暫存檔。

    - 位元組原樣寫出：文字檔的換行固定是 LF，各作業系統寫出的檔一樣（跟 .gitattributes 一致）。
    - Windows 上目標檔被別的程式開著時，換名會暫時失敗；重試幾次，還是不行就丟 SceneError。
    """
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")  # 每次不同，不跟別的寫入撞名
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        for attempt in range(_RETRIES):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == _RETRIES - 1:
                    raise
                time.sleep(_RETRY_DELAY)
    except OSError as error:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise SceneError(f"寫不了 {path}：{error}") from error
