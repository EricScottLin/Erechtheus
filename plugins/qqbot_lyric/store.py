"""本地歌词索引的落盘（``data/qqbot_lyric/library.json``）。

索引是「按需学习」的：每见过一首歌就记住它的每一行，下次群友发这句歌词时
零网络直接命中。所以落盘能显著减少启动后的冷启动网络请求。

写入策略：先写临时文件再 ``replace``，避免进程被杀时留下半截 JSON；
体积有上限（默认最多 500 首、每首 400 行），超出由 :class:`LineIndex` 控制。
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

import httpx
from nonebot.log import logger

from .library import LineIndex, parse_lrc

# 网易云歌单 / 排行榜接口（未登录可读，榜单每天更新）
NETEASE_URL = "https://music.163.com/api"
NETEASE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
# 网易云官方英文歌单（欧美热歌 / 国粤语热歌），给预载一个默认种子
DEFAULT_SEEDS: tuple[tuple[str, str], ...] = (
    ("3778678", "云音乐热歌榜"),
    ("19723756", "云音乐飙升榜"),
    ("3779629", "云音乐新歌榜"),
    ("2884035", "云音乐原创榜"),
)


class IndexStore:
    """歌词索引的持久化。"""

    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        # 预载曲库单独一个文件：运行中的 bot 只写 path（学习索引）和它自己的
        # 快照，永远不会覆盖 seed_path，所以「预载完被覆盖成旧数据」不会再发生。
        self.seed_path: Optional[Path] = path.with_name("seeded.json") if path else None
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
        seeded = 0
        if self.seed_path is not None and self.seed_path.is_file():
            try:
                seeded = self.load_seed_into(index)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[qqbot_lyric] 读取预载曲库失败：{e}")
        count = index.song_count
        if count:
            logger.info(
                f"[qqbot_lyric] 已从磁盘恢复 {count} 首歌的歌词索引"
                f"（其中预载曲库 {seeded} 首）"
            )
        return count

    def load_seed_into(self, index: LineIndex) -> int:
        """把预载曲库灌进索引；返回新增的歌曲数。"""
        if self.seed_path is None or not self.seed_path.is_file():
            return 0
        data = json.loads(self.seed_path.read_text(encoding="utf-8"))
        before = index.song_count
        index.load(data)
        return index.song_count - before

    async def build_from_seeds(
        self,
        index: LineIndex,
        timeout: float = 8.0,
        max_songs: int = 300,
        on_progress=None,
    ) -> int:
        """从网易云榜单批量拉歌词，灌进本地索引（纯非 AI 的曲库）。

        这是整个「不用模型也能接」的地基：群里常见的歌基本都在热歌榜里,
        预载之后群友发一句，本地索引直接命中，零网络零 AI。

        Args:
            index: 目标索引
            timeout: 单次请求超时（秒）
            max_songs: 最多预载多少首歌（保护带宽/内存）
            on_progress: 可选回调 ``(done, total)``，用于日志
        """
        if self.seed_path is not None and self.seed_path.is_file():
            logger.info("[qqbot_lyric] 曲库已预载过，跳过种子拉取（--reseed 强制重拉）")
            return 0

        headers = {
            "User-Agent": NETEASE_UA,
            "Referer": "https://music.163.com/",
            "Cookie": "NMTID=00O",
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0), headers=headers, trust_env=False
        ) as client:
            seeds = await self._seed_song_ids(client, max_songs)
            if not seeds:
                logger.warning("[qqbot_lyric] 种子歌单拉取失败（网易云接口不可用）")
                return 0
            done = loaded = 0
            for song_id, title, artist in seeds:
                try:
                    resp = await client.get(
                        f"{NETEASE_URL}/song/lyric",
                        params={"id": song_id, "lv": -1, "kv": -1, "tv": -1},
                    )
                    data = resp.json() if resp.status_code == 200 else {}
                except Exception:  # noqa: BLE001 —— 单首失败不影响整体
                    done += 1
                    continue
                raw = (data.get("lrc") or {}).get("lyric") or ""
                lines = parse_lrc(raw)
                if len(lines) >= 8:  # 太短的多半是伴奏/纯音乐
                    from .library import Song

                    index.add_song(
                        Song(id=str(song_id), title=title, artist=artist,
                             source="netease", has_lyrics=True, from_library=True),
                        lines,
                    )
                    loaded += 1
                done += 1
                if on_progress is not None and done % 10 == 0:
                    on_progress(done, len(seeds))
        logger.info(f"[qqbot_lyric] 预载完成：{loaded}/{len(seeds)} 首歌的歌词进了本地索引")
        if loaded:
            # 写**独立的预载文件 seeded.json**：运行中的 bot 只写 library.json，
            # 永远不会覆盖它 —— 避免「预载完被旧快照覆盖成 24 首」这类事故。
            data = {
                "version": 2,
                "seeded": True,
                "songs": [
                    {"song": song.to_json(), "lines": index.lines_of(song)[:400]}
                    for song in index.songs()
                ],
            }
            self._write(data, self.seed_path)
        return loaded

    async def _seed_song_ids(self, client: httpx.AsyncClient, max_songs: int) -> list[tuple[str, str, str]]:
        """从榜单里取 (歌曲 id, 歌名, 歌手) 列表，去重。"""
        out: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for playlist_id, name in DEFAULT_SEEDS:
            try:
                # 注意：只有 /api/v6/playlist/detail 会返回完整 tracks；
                # 老接口 /api/playlist/detail 现在返回空列表。
                resp = await client.get(
                    f"{NETEASE_URL}/v6/playlist/detail", params={"id": playlist_id, "n": 300}
                )
                playlist = (resp.json() or {}).get("playlist") or {}
                tracks = playlist.get("tracks") or []
                if not tracks:
                    # 兜底：只有 id 列表时再逐首查歌曲详情
                    ids = [str(t.get("id")) for t in (playlist.get("trackIds") or [])][:max_songs]
                    if ids:
                        detail = await client.get(
                            f"{NETEASE_URL}/song/detail?ids={','.join(ids)}"
                        )
                        tracks = (detail.json() or {}).get("songs") or []
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[qqbot_lyric] 拉榜单 {name} 失败：{e}")
                continue
            for track in tracks:
                sid = str(track.get("id") or "")
                title = str(track.get("name") or "")
                artists = "、".join(
                    str(a.get("name") or "")
                    for a in (track.get("ar") or track.get("artists") or [])
                )
                if not sid or not title or sid in seen:
                    continue
                seen.add(sid)
                out.append((sid, title, artists))
                if len(out) >= max_songs:
                    return out
        return out

    def save_from(self, index: LineIndex) -> None:
        if self.path is None:
            return
        # 防止「运行中的 bot 用旧的内存索引覆盖掉刚预载的大曲库」：
        # 内存里明显比磁盘少时，先不动磁盘（等它自己重启加载）。
        try:
            if self.path.is_file():
                old = json.loads(self.path.read_text(encoding="utf-8"))
                old_count = len(old.get("songs") or [])
                if old_count > index.song_count and old_count - index.song_count > 50:
                    logger.warning(
                        f"[qqbot_lyric] 跳过写盘：内存 {index.song_count} 首 < 磁盘 {old_count} 首"
                        f"（预载曲库需要重启 bot 才会加载）"
                    )
                    return
        except Exception:  # noqa: BLE001
            pass
        data = index.dump()
        # 保留 seeded 标记（预载过就不再重复拉榜单）
        try:
            if self.path.is_file():
                old = json.loads(self.path.read_text(encoding="utf-8"))
                if old.get("seeded"):
                    data["seeded"] = True
        except Exception:  # noqa: BLE001
            pass
        self._write(data)

    def _write(self, data: dict, path: Optional[Path] = None) -> None:
        target = path or self.path
        if target is None:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(target)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_lyric] 写入歌词索引失败：{e}")
