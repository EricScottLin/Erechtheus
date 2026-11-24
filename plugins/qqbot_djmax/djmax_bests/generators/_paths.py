"""素材路径解析。

出图素材（Respect 字体与谱面覆盖图）约 56MB，不在仓库里，由
``qqbot_djmax.assets`` 下载到数据目录后生效。因此这里**不能在 import 时
固化路径** —— 素材可能在插件加载之后才下载完成。两个生成器模块通过这些
函数在每次出图时取路径。

找不到素材时退回包内 generators 目录，方便开发时把素材放进包里直接跑。
"""
from __future__ import annotations

import os
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent


def assets_root() -> Path:
    """素材根目录：优先 qqbot_djmax.assets 解析出的位置。"""
    try:
        from ...assets import assets_root as _resolve

        found = _resolve()
        if found is not None:
            return Path(found)
    except Exception:
        pass
    return _PKG_ROOT


def fonts() -> str:
    return os.path.join(str(assets_root()), "fonts")


def bests_assets() -> str:
    return os.path.join(str(assets_root()), "bests_generator", "assets")


def scorelist_assets() -> str:
    return os.path.join(str(assets_root()), "scorelist_generator", "assets")
