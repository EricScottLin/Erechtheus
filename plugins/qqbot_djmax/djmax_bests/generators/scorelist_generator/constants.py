import os


LAYOUT_WIDTH = 10
CARD_GAP = 20
GROUP_SEP_HEIGHT = 3
L_SPACE = 310
T_SPACE = 536
R_SPACE = 110
B_SPACE = 94 + 20 # bottom margin
CARD_SIZE = (160, 200)
MC_IMG_SIZE = (60, 60)
MC_POS_OFFSET = (-12, 12)
BMODE_STRIP_BOX = (70, 20, 89, 464)

from .. import _paths

# 素材在仓库外，路径每次出图时解析。


def ASSET_PATH() -> str:
    return _paths.scorelist_assets()


def FONT_PATH() -> str:
    return _paths.fonts()


def DIFF_STAR_PATH() -> str:
    return os.path.join(_paths.scorelist_assets(), "diff_stars")
