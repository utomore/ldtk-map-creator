"""分層慣例的名稱（D-011）：引擎照這些名稱讀場景地圖。

這裡只放名稱與小函式；core 裡用到慣例名稱的程式從這裡取，不各自寫字串。
"""

# 圖層，LDtk 裡由上到下
ENTITIES = "Entities"
COLLISION = "Collision"
SLOPE = "Slope"
HEIGHT = "Height"
GROUND = "Ground"
INTGRID_LAYERS = (COLLISION, SLOPE, HEIGHT, GROUND)
LAYERS = (ENTITIES, *INTGRID_LAYERS)

# entity 定義
PLAYER_SPAWN = "PlayerSpawn"
PLAYER_TAG = "player"
TELEPORT = "Teleport"
TELEPORT_TARGET = "targetPort"
PART = "Part"
PART_NAME = "part"
PART_YAW = "yaw"
ENTITY_TYPES = (PLAYER_SPAWN, TELEPORT, PART)

SLOPE_VALUE = "slope"
"""Slope 層唯一的值（1）的名稱。"""
MAX_HEIGHT = 64
"""圖格種類的 height 最多幾階；Height 層的值定義從 1 排到用到的最大階數，打錯字不會長出幾萬個值。"""

# 預覽顏色（只影響預覽圖與 LDtk 編輯畫面，引擎不讀）
SLOPE_COLOR = "#FFD54F"
HEIGHT_STEP_MIN = 0x20


def height_identifier(step: int) -> str:
    """Height 層第 `step` 階的值名稱，例如 `h2`。"""
    return f"h{step}"


def height_color(step: int) -> str:
    """Height 層第 `step` 階的預覽顏色：由淺到深的灰色，到 MAX_HEIGHT 每階都不一樣。"""
    level = max(HEIGHT_STEP_MIN, 0xE0 - 3 * (step - 1))
    return f"#{level:02X}{level:02X}{level:02X}"
