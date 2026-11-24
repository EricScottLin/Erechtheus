"""出图素材（字体与图片）的获取。

字体与谱面素材合计约 56MB，不适合放进 git 仓库（其中 Respect 字体是
DJMAX 游戏字体，公开分发需谨慎）。因此素材托管在 CDN，首次运行时下载到
可写目录；也支持直接指定本地已有目录，避免重复下载。

查找顺序（先命中先用）：

1. 环境变量 ``DJMAX_ASSETS_DIR`` / 插件配置 ``djmax_assets_dir``：本地目录；
2. 数据目录下已下载好的 ``assets/``（带版本标记，版本不符才重下）；
3. 从 ``DJMAX_ASSETS_URL`` 下载压缩包并解压。

素材包内容（相对根目录）：

    fonts/Respect_bd.ttf  fonts/Respect_rg.ttf  fonts/Respect_lt.ttf
    bests_generator/assets/...
    scorelist_generator/assets/...

上游素材来自 https://github.com/SoreHait/djmax_bests_generate（MIT）。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tarfile
import zipfile
from pathlib import Path

#: 素材包版本；结构变化时递增，触发已部署环境重新下载。
ASSETS_VERSION = "1"

#: 默认素材包地址；未设置环境变量时使用。
DEFAULT_ASSETS_URL = ""

_REQUIRED_FONTS = ("Respect_bd.ttf", "Respect_rg.ttf", "Respect_lt.ttf")


def _conf(name: str) -> str:
    """读配置：环境变量优先，其次 NoneBot 的 .env 配置。

    NoneBot 的 ``.env`` / ``.env.<env>`` 不会写进 ``os.environ``，
    只出现在 ``driver.config`` 的 extra 字段里，所以两处都要看。
    """
    value = os.environ.get(name)
    if value:
        return value
    try:
        from nonebot import get_driver

        extra = getattr(get_driver().config, "model_extra", None) or {}
        found = extra.get(name.lower()) or getattr(get_driver().config, name.lower(), None)
        return str(found) if found else ""
    except Exception:
        return ""


def _data_root() -> Path:
    if env := _conf("DJMAX_DATA_DIR"):
        return Path(env)
    try:
        from nonebot_plugin_localstore import get_plugin_data_dir

        return Path(get_plugin_data_dir())
    except Exception:
        return Path.cwd() / "data" / "qqbot_djmax"


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    if value := _conf("DJMAX_ASSETS_DIR"):
        roots.append(Path(value))
    try:
        from .config import config

        if config.djmax_assets_dir:
            roots.append(Path(config.djmax_assets_dir))
    except Exception:
        pass
    roots.append(_data_root() / "assets")
    # 兼容：素材就在插件包内（开发时仓库里带了素材）
    roots.append(Path(__file__).resolve().parent / "djmax_bests" / "generators")
    return roots


def _is_ready(root: Path) -> bool:
    fonts = root / "fonts"
    if not fonts.is_dir():
        return False
    if not all((fonts / name).exists() for name in _REQUIRED_FONTS):
        return False
    return (root / "bests_generator" / "assets").is_dir()


def assets_root() -> Path | None:
    """返回已就绪的素材根目录；都没有就返回 None（此时不阻塞调用方）。"""
    for root in _candidate_roots():
        try:
            if _is_ready(root):
                return root
        except OSError:
            continue
    return None


def _extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
        return
    with tarfile.open(archive) as tf:
        # 防目录穿越
        base = dest.resolve()
        for member in tf.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(base)):
                raise RuntimeError(f"素材包含非法路径：{member.name}")
        tf.extractall(dest)


def _flatten_if_nested(dest: Path) -> None:
    """压缩包若带一层顶层目录，把它提上来。"""
    if _is_ready(dest):
        return
    for child in dest.iterdir():
        if child.is_dir() and _is_ready(child):
            for item in child.iterdir():
                shutil.move(str(item), str(dest / item.name))
            child.rmdir()
            return


async def ensure_assets() -> Path:
    """确保素材就绪，返回素材根目录；失败抛异常由调用方提示用户。"""
    ready = assets_root()
    if ready is not None:
        return ready

    url = _conf("DJMAX_ASSETS_URL") or DEFAULT_ASSETS_URL
    if not url:
        raise RuntimeError(
            "未找到 DJMAX 出图素材。请设置环境变量 DJMAX_ASSETS_URL 指向素材包，"
            "或设置 DJMAX_ASSETS_DIR 指向本地素材目录。"
        )

    import httpx

    dest = _data_root() / "assets"
    stamp = dest / ".version"
    if stamp.exists() and stamp.read_text(encoding="utf-8").strip() == ASSETS_VERSION and _is_ready(dest):
        return dest

    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / ".download"
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            archive.write_bytes(response.content)
        await asyncio.to_thread(_extract, archive, dest)
        await asyncio.to_thread(_flatten_if_nested, dest)
        stamp.write_text(ASSETS_VERSION, encoding="utf-8")
    finally:
        archive.unlink(missing_ok=True)

    if not _is_ready(dest):
        raise RuntimeError("素材包解压后结构不正确，缺少 fonts 或 assets 目录。")
    return dest
