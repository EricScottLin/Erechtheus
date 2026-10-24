"""二维码解码：zxing-cpp 读码 + Pillow 解码图片，全程只在内存里。

只认二维码，最多返回一个结果；识别不到返回 None，由调用方提示换图。
"""

import io
from typing import Optional

from nonebot.log import logger

try:
    import zxingcpp
    from PIL import Image

    AVAILABLE = True
    _ERROR = ""
except Exception as exc:  # pragma: no cover - 部署缺依赖时给出明确提示
    AVAILABLE = False
    _ERROR = f"{type(exc).__name__}: {exc}"
    zxingcpp = None
    Image = None


def unavailable_reason() -> str:
    return "" if AVAILABLE else _ERROR or "缺少 zxing-cpp / Pillow"


def decode(data: bytes) -> Optional[str]:
    """从图片字节里解出一个二维码文本；没有二维码返回 None。"""
    if not AVAILABLE:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        logger.warning(f"舞萌绑定：图片无法解码（{type(exc).__name__}）")
        return None
    try:
        results = zxingcpp.read_barcodes(image)
    except Exception as exc:  # pragma: no cover - zxing 内部异常
        logger.warning(f"舞萌绑定：二维码识别失败（{type(exc).__name__}）")
        return None
    for result in results:
        text = (result.text or "").strip()
        if text:
            return text
    return None
