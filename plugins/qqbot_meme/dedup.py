"""图片重复检测：内容哈希 + 感知哈希（近似重复）。

- ``content_hash``：字节 sha1，完全相同的图一定命中
- ``perceptual_hash``：缩放到 16x16 灰度后的 aHash（256 bit），
  用来识别「同一张图被重新压缩/转格式/改尺寸」这类近似重复

不依赖 numpy：用 Pillow 自带的 resize/convert 即可。
"""

from __future__ import annotations

import hashlib
import io

try:
    from PIL import Image
except Exception:  # noqa: BLE001 —— 没装 Pillow 时只做字节级查重
    Image = None  # type: ignore[assignment]

AHSH_SIZE = 16
# 汉明距离阈值：≤ 这个值视为同一张图（共 112 bit）
# 实测同一张图转 JPEG/WEBP/GIF 距离 0~11，不同图 ≥23，取 16 留余量
SIMILAR_THRESHOLD = 16


def content_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def perceptual_hash(data: bytes) -> int | None:
    """返回感知哈希（int，dHash 变体）；无法解码返回 None。

    做法：统一到 64x64 → 灰度 → 按 16 个像素分组取均值，
    再比较相邻两组的亮度关系（组间梯度），对重压缩/转格式更稳定。
    """
    img = _gray(data)
    if img is None:
        return None
    pixels = list(img.getdata())
    # 每行 16 个像素，分成 8 组，每组 2 像素取平均，得到 8 个列均值
    rows: list[list[float]] = []
    for row in range(AHSH_SIZE):
        vals = []
        base = row * AHSH_SIZE
        for col in range(0, AHSH_SIZE, 2):
            vals.append((pixels[base + col] + pixels[base + col + 1]) / 2)
        rows.append(vals)

    # 列方向相邻比较（8 组 → 7 位）
    bits = 0
    idx = 0
    for row in rows:
        for i in range(len(row) - 1):
            if row[i] > row[i + 1]:
                bits |= 1 << idx
            idx += 1
    # 行方向相邻比较（16 行 → 15 位）
    for col in range(len(rows[0])):
        for i in range(len(rows) - 1):
            if rows[i][col] > rows[i + 1][col]:
                bits |= 1 << idx
            idx += 1
    return bits


def _gray(data: bytes):
    """解码并缩放到 (AHSH_SIZE, AHSH_SIZE) 灰度图；失败返回 None。"""
    if Image is None:
        return None
    try:
        im = Image.open(io.BytesIO(data))
        im.seek(0)
        im = im.convert("RGB").resize((64, 64), Image.LANCZOS)
        return im.convert("L").resize((AHSH_SIZE, AHSH_SIZE), Image.LANCZOS)
    except Exception:  # noqa: BLE001
        return None


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def is_similar(a: int | None, b: int | None) -> bool:
    if a is None or b is None:
        return False
    return distance(a, b) <= SIMILAR_THRESHOLD
