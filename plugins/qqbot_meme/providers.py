"""表情/金句来源注册表。

戳一戳、``/表情`` 不再固定读某一个目录，而是从「来源池」里抽取：

- 内置来源
  - ``local``：``data/qqbot_meme/stickers/`` 本地表情包
  - ``quotes``：金句库（文本，**按群隔离**）
- 外部来源（自动探测）
  - ``pigverse``：``plugins/qqbot_pigverse`` 的猪猪表情库（若已加载）
- 未来扩展：任何插件都可以在加载后调用
  ``register_image_provider(name, fn, weight=None)`` /
  ``register_text_provider(name, fn, weight=None)`` 注册来源。
  ``fn`` 接收 ``ProviderContext``（群号/用户号），返回 ``ReplyItem | None``；
  ``weight`` 是返回数量的同步/异步函数（用于按库大小加权）。

默认 ``qqbot_meme_source_weighting="count"``：每个来源被抽中的概率与它的
条目数成正比，等价于「从整个表情库里随机抽一张」；设为 ``uniform`` 则每个
来源等概率。
"""

import inspect
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal

import nonebot
from nonebot.log import logger

from . import library
from .config import plugin_config


@dataclass
class ProviderContext:
    """抽取时的会话上下文（金句按群隔离时会用到 group_id）。"""

    group_id: int | None = None
    user_id: str | None = None


@dataclass
class ReplyItem:
    """一次可发送的结果。"""

    kind: Literal["image", "text"]
    value: bytes | str
    source: str = ""


Provider = Callable[[ProviderContext], Awaitable[ReplyItem | None]]
WeightFn = Callable[[], "int | Awaitable[int]"]

_image_providers: list[tuple[str, Provider, WeightFn | None]] = []
_text_providers: list[tuple[str, Provider, WeightFn | None]] = []
_external_checked = False


# ---------------------------------------------------------------- 注册
def register_image_provider(
    name: str, provider: Provider, weight: WeightFn | None = None
) -> None:
    """注册一个图片来源。

    ``provider(ctx)`` 返回 ``ReplyItem(kind="image", value=bytes)`` 或 None；
    ``weight`` 可选，返回该来源的条目数，用于按库大小加权。
    """
    _image_providers[:] = [(n, p, w) for n, p, w in _image_providers if n != name]
    _image_providers.append((name, provider, weight))
    logger.info(f"[qqbot_meme] 已注册图片来源：{name}")


def register_text_provider(
    name: str, provider: Provider, weight: WeightFn | None = None
) -> None:
    """注册一个文本来源。"""
    _text_providers[:] = [(n, p, w) for n, p, w in _text_providers if n != name]
    _text_providers.append((name, provider, weight))
    logger.info(f"[qqbot_meme] 已注册文本来源：{name}")


# ---------------------------------------------------------------- 内置来源
async def _local_image(ctx: ProviderContext) -> ReplyItem | None:
    path = library.random_sticker(plugin_config.qqbot_meme_max_mb * 1024 * 1024)
    if path is None:
        return None
    return ReplyItem("image", path.read_bytes(), "local")


def _local_count() -> int:
    return len(library.list_stickers())


async def _quote_text(ctx: ProviderContext) -> ReplyItem | None:
    entry = library.random_quote_entry(ctx.group_id)
    if not entry:
        return None
    text = entry["text"]
    if entry.get("author"):
        text += f"\n—— {entry['author']}"
    return ReplyItem("text", text, "quotes")


def _quote_count() -> int:
    return len(library.load_quotes())


register_image_provider("local", _local_image, _local_count)
register_text_provider("quotes", _quote_text, _quote_count)


# ---------------------------------------------------------------- 外部来源
def _register_pigverse() -> bool:
    """尝试把 pigverse 的猪猪表情库接进来（插件已加载才生效）。"""
    plugin = nonebot.get_plugin("qqbot_pigverse")
    if plugin is None:
        return False
    memes = getattr(plugin.module, "memes", None)
    random_meme = getattr(memes, "random_meme", None) if memes is not None else None
    if not callable(random_meme):
        return False

    async def _pig(_ctx: ProviderContext) -> ReplyItem | None:
        path = await random_meme()
        if not path:
            return None
        return ReplyItem("image", Path(path).read_bytes(), "pigverse")

    count_fn = getattr(memes, "count", None)
    register_image_provider(
        "pigverse", _pig, count_fn if callable(count_fn) else None
    )
    return True


def sync_external_providers() -> None:
    """探测并注册外部来源（只做一次，poke / /表情 调用前触发）。"""
    global _external_checked
    if _external_checked:
        return
    _external_checked = True
    try:
        _register_pigverse()
    except Exception as e:  # noqa: BLE001 —— 外部插件异常不应影响表情功能
        logger.warning(f"[qqbot_meme] 注册 pigverse 表情来源失败：{e}")


# ---------------------------------------------------------------- 抽取
async def _safe_weight(weight: WeightFn | None) -> int | None:
    if weight is None:
        return None
    try:
        value = weight()
        if inspect.isawaitable(value):
            value = await value
        return max(0, int(value))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[qqbot_meme] 读取来源数量失败：{e}")
        return None


async def _pick(
    providers: list[tuple[str, Provider, WeightFn | None]],
    ctx: ProviderContext,
) -> ReplyItem | None:
    if not providers:
        return None

    if plugin_config.qqbot_meme_source_weighting == "count":
        weighted: list[tuple[float, tuple[str, Provider, WeightFn | None]]] = []
        for entry in providers:
            count = await _safe_weight(entry[2])
            w = 1.0 if count is None else float(count)
            weighted.append((max(w, 1.0), entry))
        weighted.sort(key=lambda item: random.random() ** (1.0 / item[0]), reverse=True)
        order = [entry for _, entry in weighted]
    else:
        order = random.sample(providers, len(providers))

    for name, fn, _ in order:
        try:
            item = await fn(ctx)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_meme] 来源 {name} 抽取失败：{e}")
            continue
        if item is not None:
            return item
    return None


async def pick_image(ctx: ProviderContext | None = None) -> ReplyItem | None:
    """从所有图片来源里随机取一张。"""
    sync_external_providers()
    return await _pick(_image_providers, ctx or ProviderContext())


async def pick_text(ctx: ProviderContext | None = None) -> ReplyItem | None:
    """从所有文本来源里随机取一条。"""
    sync_external_providers()
    return await _pick(_text_providers, ctx or ProviderContext())


async def pick_any(ctx: ProviderContext | None = None) -> ReplyItem | None:
    """按权重在「图片」与「文本」两类来源里抽一个（戳一戳用）。"""
    sync_external_providers()
    ctx = ctx or ProviderContext()
    image_weight = max(0, int(plugin_config.qqbot_meme_poke_image_weight))
    quote_weight = max(0, int(plugin_config.qqbot_meme_poke_quote_weight))
    categories: list[tuple[str, int]] = []
    if image_weight > 0:
        categories.append(("image", image_weight))
    if quote_weight > 0:
        categories.append(("text", quote_weight))
    if not categories:
        return None

    kinds = [k for k, _ in categories]
    weights = [w for _, w in categories]
    first = random.choices(kinds, weights=weights, k=1)[0]
    for kind in (first, *(k for k in kinds if k != first)):
        item = await (
            _pick(_image_providers, ctx) if kind == "image" else _pick(_text_providers, ctx)
        )
        if item is not None:
            return item
    return None


async def describe_sources() -> list[tuple[str, str, int | None]]:
    """返回 ``[(来源名, 类型, 条目数)]``，供 /表情列表 展示。"""
    sync_external_providers()
    result: list[tuple[str, str, int | None]] = []
    for name, _, weight in _image_providers:
        result.append((name, "image", await _safe_weight(weight)))
    for name, _, weight in _text_providers:
        result.append((name, "text", await _safe_weight(weight)))
    return result
