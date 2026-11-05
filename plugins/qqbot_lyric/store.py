"""本地歌词索引的落盘（``data/qqbot_lyric/library.json``）。

索引是「按需学习」的：每见过一首歌就记住它的每一行，下次群友发这句歌词时
零网络直接命中。所以落盘能显著减少启动后的冷启动网络请求。

写入策略：先写临时文件再 ``replace``，避免进程被杀时留下半截 JSON；
体积有上限（默认最多 500 首、每首 400 行），超出由 :class:`LineIndex` 控制。
"""

import json
from pathlib import Path
from typing import Optional

from nonebot.log import logger

from .library import LineIndex


class IndexStore:
    """歌词索引的持久化。"""

    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        self._dirty_songs: set[str] = set()

    # ------------------------------------------------------------- 读写
    def load_into(self, index: LineIndex) -> int:
        """把落盘索引灌进内存索引；返回加载的歌曲数。"""
        if self.path is None or not self.path.is_file():
            return 0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 —— 索引坏了就当没有
            logger.warning(f"[qqbot_lyric] 读取歌词索引失败，已忽略：{e}")
            return 0
        index.load(data)
        count = index.song_count
        if count:
            logger.info(f"[qqbot_lyric] 已从磁盘恢复 {count} 首歌的歌词索引")
        return count

    def save_from(self, index: LineIndex) -> None:
        if self.path is None:
            return
        try:
            data = index.dump()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_lyric] 写入歌词索引失败：{e}")
