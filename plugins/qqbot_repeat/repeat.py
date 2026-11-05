"""连续复读：连续相同文本 / 表情包时跟着复读一次。

规则：

- 同一群内，连续 **不同用户** 发来相同的文本或同一张图/表情，人数达到
  ``qqbot_repeat_times``（默认 3）时复读一次；
- 同一个人重复发不增加人数（避免单人刷屏触发）；
- 图片按 ``file``（QQ 的内容哈希文件名）识别，表情（mface）按 ``id``/``summary`` 识别；
- **同一内容只复读一次**：bot 参与过某句话/某张图后，后面再刷同样的内容不再跟发
  （``qqbot_repeat_once_per_content``，默认开）；
- 触发后进入 ``qqbot_repeat_cooldown`` 秒冷却（默认 60 秒），避免不同内容连续刷屏；
- 命令消息（以 / 开头）、空消息、超长文本不参与；图文混合消息会重置计数；
- 「我说X你说Y」教学命令本身不参与复读。
"""

import time
from collections import OrderedDict
from dataclasses import dataclass, field

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment

from . import features
from .config import plugin_config

# 优先级放低：命令响应器（priority=5, block=True）先处理，复读只关心普通聊天。
# 教学/触发响应器在 priority=10/11 且 block=True，命中时不会走到这里。
repeat = on_message(priority=90, block=False)

IMAGE_TYPES = ("image", "mface")


@dataclass
class _Run:
    """一次连续复读候选。"""

    key: tuple
    seg: MessageSegment
    users: set[str] = field(default_factory=set)


# group_id -> 当前连续相同的消息
_state: dict[int, _Run] = {}
# group_id -> 冷却截止时间戳
_cooldown: dict[int, float] = {}
# group_id -> 已复读过的内容 key -> 最后复读时间戳（OrderedDict 当 LRU 用）
_repeated: dict[int, OrderedDict[tuple, float]] = {}


def _already_repeated(group_id: int, key: tuple, now: float) -> bool:
    """这个内容是不是已经由 bot 复读过了（TTL 过期则视为没复读过）。"""
    bucket = _repeated.get(group_id)
    if not bucket or key not in bucket:
        return False
    ttl = plugin_config.qqbot_repeat_memory_ttl
    if ttl > 0 and now - bucket[key] > ttl:
        bucket.pop(key, None)
        return False
    bucket.move_to_end(key)
    return True


def _mark_repeated(group_id: int, key: tuple, now: float) -> None:
    """记住「这个内容 bot 已经复读过了」，超出容量时淘汰最久没出现的。"""
    bucket = _repeated.setdefault(group_id, OrderedDict())
    bucket[key] = now
    bucket.move_to_end(key)
    limit = plugin_config.qqbot_repeat_memory_size
    while len(bucket) > limit:
        bucket.popitem(last=False)


def _image_key(seg: MessageSegment) -> tuple | None:
    data = seg.data or {}
    if seg.type == "image":
        ident = str(data.get("file") or "")
        if not ident:
            url = str(data.get("url") or "")
            ident = url.split("?")[0] if url else ""
        return ("image", ident) if ident else None
    ident = str(data.get("id") or data.get("summary") or data.get("url") or "")
    return ("mface", ident) if ident else None


def _candidate(event: GroupMessageEvent) -> tuple[tuple, MessageSegment] | None:
    """判断这条消息是否参与复读，返回 (识别 key, 复读时要发的段)。"""
    segs = [s for s in event.get_message() if s.type != "reply"]
    if not segs:
        return None

    if len(segs) == 1:
        seg = segs[0]
        if seg.type == "text":
            text = str(seg.data.get("text") or "").strip()
            if not text or text.startswith("/"):
                return None
            # 教学命令不参与复读，避免「我说X你说Y」被当成普通聊天跟发
            if text.startswith("我说") and "你说" in text:
                return None
            if len(text) > plugin_config.qqbot_repeat_max_len:
                return None
            return ("text", text), MessageSegment.text(text)
        if seg.type in IMAGE_TYPES:
            if not plugin_config.qqbot_repeat_include_images:
                return None
            key = _image_key(seg)
            if key is None:
                return None
            return key, seg
        return None

    # 多段：全部是文本时拼接（reduce 后一般不会出现），否则视为图文混合，不参与
    if all(s.type == "text" for s in segs):
        text = "".join(str(s.data.get("text") or "") for s in segs).strip()
        if not text or text.startswith("/"):
            return None
        if text.startswith("我说") and "你说" in text:
            return None
        if len(text) > plugin_config.qqbot_repeat_max_len:
            return None
        return ("text", text), MessageSegment.text(text)
    return None


async def _send_repeat(seg: MessageSegment) -> None:
    """按原消息类型复读；图片优先用 url 让协议端重新取图。"""
    if seg.type == "image":
        url = str((seg.data or {}).get("url") or "")
        if url:
            await repeat.finish(MessageSegment.image(url))
    await repeat.finish(MessageSegment(seg.type, dict(seg.data or {})))


@repeat.handle()
async def handle_repeat(bot: Bot, event: GroupMessageEvent) -> None:
    """连续相同文本/图片达到人数阈值时复读一次。"""
    cfg = plugin_config
    if not cfg.qqbot_repeat_enabled:
        return
    if not features.is_enabled(event.group_id, "repeat"):
        return

    candidate = _candidate(event)
    if candidate is None:
        _state.pop(event.group_id, None)
        return
    key, seg = candidate

    uid = event.get_user_id()
    if uid == bot.self_id:
        return

    run = _state.get(event.group_id)
    if run is not None and run.key == key:
        run.users.add(uid)
    else:
        run = _Run(key=key, seg=seg, users={uid})
        _state[event.group_id] = run

    if len(run.users) < cfg.qqbot_repeat_times:
        return

    now = time.time()
    # 同一内容 bot 只参与一次：刷过「你好」之后，再凑 3 个人刷「你好」也不跟
    if cfg.qqbot_repeat_once_per_content and _already_repeated(
        event.group_id, key, now
    ):
        _state.pop(event.group_id, None)
        return
    if now < _cooldown.get(event.group_id, 0.0):
        _state.pop(event.group_id, None)
        return

    _cooldown[event.group_id] = now + cfg.qqbot_repeat_cooldown
    if cfg.qqbot_repeat_once_per_content:
        _mark_repeated(event.group_id, key, now)
    _state.pop(event.group_id, None)
    await _send_repeat(seg)
