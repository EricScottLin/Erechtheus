"""接龙会话状态：记住「这个群最近在接哪首歌」，避免轻易换歌。

群友接龙不是每句都重新搜一遍歌。经验规则：

- bot 刚接过一次之后，有一段 **连续接** 窗口（默认 120 秒）；
  窗口内别人再发歌词，就默认还是这首歌，先在**同一首歌里**找位置，
  找到就接着往下接 —— 不再去别的歌里乱找；
- 窗口过了，但如果这首歌还在「最近 15 分钟」里（置顶候选），
  下一句歌词优先匹配它，匹配不上才换歌；
- 显式点名一首新歌（``/歌词 小幸运`` 或模型给出不同歌名）时立刻换歌。

状态只在内存里（进程重启就清空），可以不落盘 —— 重启后重新识别一次成本很低。
"""
# 作为独立顶层包导入时（单测 `import qqbot_lyric.xxx`）不允许，走插件包 __init__
if not (__package__ or "").endswith("qqbot_lyric"):
    raise ImportError("qqbot_lyric 子模块只能以 qqbot_lyric.<module> 形式导入")


import time
from dataclasses import dataclass, field
from typing import Optional

from .library import Song


@dataclass
class RelayState:
    """某个群当前的接龙上下文。"""

    group_id: int
    song: Optional[Song] = None  # 正在接的歌
    last_reply_at: float = 0.0  # bot 上次接龙的时间（monotonic）
    last_song_key: str = ""  # 上次接的是哪首歌（冷却只挡同一首歌）
    last_hit_key: str = ""  # 上次接的是哪一处（歌#起行-止行；同一句重复发不刷屏）
    last_sender: str = ""  # 上次被接的人（自己唱自己接就没意思了）
    replies: int = 0  # 这一轮接了几次
    recent: list[Song] = field(default_factory=list)  # 最近出现过的歌（新的在前）

    def in_window(self, now: float, window: float) -> bool:
        """是否处在「连续接」窗口内。"""
        return self.song is not None and (now - self.last_reply_at) <= window

    def recently_seen(self, now: float, ttl: float) -> bool:
        return self.song is not None and (now - self.last_reply_at) <= ttl


class RelayStore:
    """按群保存接龙状态 + 冷却。"""

    def __init__(self, max_recent: int = 5) -> None:
        self.max_recent = max_recent
        self._states: dict[int, RelayState] = {}

    # ------------------------------------------------------------- 读取
    def get(self, group_id: int) -> RelayState:
        state = self._states.get(group_id)
        if state is None:
            state = RelayState(group_id=group_id)
            self._states[group_id] = state
        return state

    def cooldown_left(
        self, group_id: int, now: float, cooldown: float, song: Optional[Song] = None
    ) -> float:
        """还剩多少秒冷却。

        传了 ``song`` 时只算「同一首歌」的冷却：群里换歌/接着对唱不受影响。
        不传则按「本群上次接龙」算（兼容旧调用）。
        """
        if cooldown <= 0:
            return 0.0
        state = self.get(group_id)
        if song is not None and state.last_song_key and state.last_song_key != song.key:
            return 0.0
        elapsed = now - state.last_reply_at
        return max(0.0, cooldown - elapsed)

    def in_window(self, group_id: int, now: float, window: float) -> bool:
        return self.get(group_id).in_window(now, window)

    def pinned_song(self, group_id: int, now: float, ttl: float) -> Optional[Song]:
        state = self.get(group_id)
        if state.song is not None and state.recently_seen(now, ttl):
            return state.song
        return None

    # ------------------------------------------------------------- 写入
    def mark_replied(self, group_id: int, song: Song, sender: str, now: float) -> None:
        state = self.get(group_id)
        if state.song is None or state.song.key != song.key:
            state.song = song
            state.replies = 0
        # 冷却按「同一首歌」算：换歌（或同一首歌换了另一个版本）立刻可以再接。
        # 原来按「同一群」算，群里连着对唱时第二句就被冷却挡住，看着像「接不上」。
        if state.last_song_key != song.key:
            state.last_song_key = song.key
            state.last_reply_at = 0.0
        state.last_reply_at = now
        state.last_sender = sender
        state.replies += 1
        self._remember(state, song)

    def set_song(self, group_id: int, song: Song) -> None:
        """显式换歌（/歌词、模型指出新歌）。"""
        state = self.get(group_id)
        state.song = song
        self._remember(state, song)

    def reset(self, group_id: int) -> None:
        self._states.pop(group_id, None)

    def _remember(self, state: RelayState, song: Song) -> None:
        state.recent = [s for s in state.recent if s.key != song.key][: self.max_recent - 1]
        state.recent.insert(0, song)


def now_ts() -> float:
    return time.monotonic()
