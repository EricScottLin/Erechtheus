"""群红包：bot 主从自己钱包发月币红包，群友抢，随机拆分，超时退回。

设计约束：
  * 不凭空增发：创建时从发送者钱包扣除，抢完/超时未领的部分原路退回
  * 每人每个红包只能抢一次
  * 内存态：进程重启会丢失未抢完的红包（金额不退，短时活动可接受）
"""

import asyncio
import random
import string
import time
from dataclasses import dataclass, field

from nonebot.log import logger

from . import wallet


@dataclass
class RedPacket:
    id: str
    group_id: int
    sender: str
    total: int
    remaining: int
    slots: int  # 剩余份数
    title: str
    claims: dict[str, int] = field(default_factory=dict)
    expires_at: float = 0.0
    done: bool = False


_PACKETS: dict[str, RedPacket] = {}
_lock = asyncio.Lock()


def _new_id() -> str:
    while True:
        rid = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        if rid not in _PACKETS:
            return rid


async def create(
    sender: str, group_id: int, total: int, slots: int, title: str = "", ttl: int = 1800
) -> RedPacket | None:
    """创建红包；发送者余额不足返回 None。"""
    if total <= 0 or slots <= 0 or total < slots:
        return None
    if not await wallet.spend(sender, "drachma", total):
        return None
    packet = RedPacket(
        id=_new_id(),
        group_id=group_id,
        sender=sender,
        total=total,
        remaining=total,
        slots=slots,
        title=title or "恭喜发财",
        expires_at=time.time() + ttl,
    )
    async with _lock:
        _PACKETS[packet.id] = packet
    asyncio.create_task(_expire(packet.id))
    return packet


async def _expire(rid: str) -> None:
    await asyncio.sleep(_PACKETS[rid].expires_at - time.time())
    async with _lock:
        packet = _PACKETS.get(rid)
        if packet is None or packet.done:
            return
        packet.done = True
        refund = packet.remaining
        if refund > 0:
            await wallet.earn(packet.sender, "drachma", refund, source="redpacket refund")
            logger.info(f"[qqbot_core] 红包 {rid} 超时，退回 {refund}")


def _split(remaining: int, slots: int) -> int:
    """二倍均值法：每次随机 1 ~ min(剩余/剩余份数*2, 剩余-份数+1)。"""
    if slots <= 1:
        return remaining
    upper = min(max(1, remaining * 2 // slots), remaining - slots + 1)
    return random.randint(1, max(1, upper))


async def grab(rid: str, group_id: int, uid: str) -> tuple[str, int]:
    """抢红包。返回 (状态, 金额)，状态 ∈ ok/already/empty/expired/wrong_group。"""
    async with _lock:
        packet = _PACKETS.get(rid)
        if packet is None:
            return "empty", 0
        if packet.group_id != group_id:
            return "wrong_group", 0
        if packet.done or time.time() > packet.expires_at or packet.remaining <= 0:
            return "empty", 0
        if uid in packet.claims:
            return "already", 0
        amount = _split(packet.remaining, packet.slots)
        packet.remaining -= amount
        packet.slots -= 1
        packet.claims[uid] = amount
        done = packet.slots <= 0 or packet.remaining <= 0
        if done:
            packet.done = True
    await wallet.earn(uid, "drachma", amount, source=f"redpacket {rid}")
    return "ok", amount


def latest_in_group(group_id: int) -> RedPacket | None:
    """取群里最近一个未抢完的红包（方便 /抢红包 不带编号）。"""
    now = time.time()
    candidates = [
        p
        for p in _PACKETS.values()
        if p.group_id == group_id and not p.done and p.remaining > 0 and p.expires_at > now
    ]
    return max(candidates, key=lambda p: p.expires_at) if candidates else None


def get(rid: str) -> RedPacket | None:
    return _PACKETS.get(rid)
