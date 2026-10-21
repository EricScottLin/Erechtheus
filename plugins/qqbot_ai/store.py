"""会话历史存储（内存 + 落盘，进程重启不丢上下文）。

- 每个会话（群 / 私聊）保留最近 ``max_turns`` 轮「问-答」；
- 送去模型前按**字数预算**压缩：从最新一轮往前取，超预算的旧轮直接丢弃；
- 超时（``expire_minutes``）的会话在读取时惰性清理，并定时落盘。

上下文模式（``mode``）：

- ``shared``（默认）：整群共用一条上下文，群里任何人都能接着聊
  （「小e X」「小e Y」算同一个话题）；
- ``user``：每个人的追问只接在**自己**上一问后面，互不串台
  —— 从最新的往前找，遇到别人的轮次就停，只取同一提问者连续的那几轮。
  提问者用 ``user_key`` 区分（``user:<QQ号>`` / ``group:<群号>``）。
"""

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from nonebot.log import logger

from . import text as text_utils


@dataclass
class Turn:
    question: str
    answer: str
    ts: float = field(default_factory=time.time)
    user_key: str = ""


class ConversationStore:
    """按会话键（如 ``group:123`` / ``user:456``）保存历史。"""

    def __init__(
        self,
        path: Optional[Path] = None,
        max_turns: int = 4,
        expire_minutes: int = 30,
        mode: str = "shared",
    ) -> None:
        self.path = path
        self.max_turns = max(0, int(max_turns))
        self.expire_seconds = max(1, int(expire_minutes)) * 60
        self.mode = "user" if mode == "user" else "shared"
        self._data: dict[str, list[Turn]] = {}
        self._lock = asyncio.Lock()
        self._load()

    # ------------------------------------------------------------ 落盘
    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_ai] 读取会话历史失败，已忽略：{e}")
            return
        now = time.time()
        for key, turns in (raw or {}).items():
            items = []
            for item in turns if isinstance(turns, list) else []:
                try:
                    turn = Turn(
                        question=str(item.get("question", "")),
                        answer=str(item.get("answer", "")),
                        ts=float(item.get("ts", now)),
                        user_key=str(item.get("user_key", "")),
                    )
                except (AttributeError, TypeError, ValueError):
                    continue
                if now - turn.ts <= self.expire_seconds:
                    items.append(turn)
            if items:
                self._data[key] = items[-self.max_turns :] if self.max_turns else []

    def save(self) -> None:
        if self.path is None:
            return
        self.prune()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {k: [asdict(t) for t in v] for k, v in self._data.items()},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_ai] 保存会话历史失败：{e}")

    # ------------------------------------------------------------ 读写
    def prune(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        for key in list(self._data):
            kept = [
                t for t in self._data[key] if now - t.ts <= self.expire_seconds
            ]
            if kept:
                self._data[key] = kept[-self.max_turns :] if self.max_turns else []
            else:
                self._data.pop(key, None)

    def turns(self, key: str) -> list[Turn]:
        self.prune()
        return list(self._data.get(key, []))

    def build_context(
        self,
        key: str,
        budget_chars: int,
        max_input_chars: int,
        user_key: str = "",
    ) -> list[dict[str, str]]:
        """把历史压成 ``messages``（不含本次提问）。

        压缩规则：
        1. 从最新一轮往前取，累计超过 ``budget_chars`` 就停止；
        2. 历史里的问/答也按输入规则压缩（折叠空白、压缩语气词）；
        3. 预算不够时宁可少一轮，也不截半句；
        4. ``user`` 模式下只取同一 ``user_key`` 的轮次，别人的轮次跳过。
        """
        if budget_chars <= 0 or self.max_turns <= 0:
            return []
        turns = self.turns(key)
        if self.mode == "user" and user_key:
            # 只带自己的轮次；别人插话的轮次直接跳过（不中断自己的上下文）
            turns = [
                t for t in turns if not t.user_key or t.user_key == user_key
            ]
        picked: list[Turn] = []
        used = 0
        for turn in reversed(turns):
            q = text_utils.compress_input(turn.question, max_input_chars)
            a = text_utils.compress_input(turn.answer, max_input_chars)
            cost = len(q) + len(a)
            if used + cost > budget_chars:
                break
            picked.append(Turn(question=q, answer=a, ts=turn.ts))
            used += cost
        messages: list[dict[str, str]] = []
        for turn in reversed(picked):
            messages.append({"role": "user", "content": turn.question})
            if turn.answer:
                messages.append({"role": "assistant", "content": turn.answer})
        return messages

    async def add(
        self,
        key: str,
        question: str,
        answer: str,
        user_key: str = "",
    ) -> None:
        if self.max_turns <= 0:
            return
        async with self._lock:
            self.prune()
            self._data.setdefault(key, []).append(
                Turn(question=question, answer=answer, user_key=user_key)
            )
            self._data[key] = self._data[key][-self.max_turns :]

    async def clear(self, key: str) -> int:
        async with self._lock:
            return len(self._data.pop(key, []))
