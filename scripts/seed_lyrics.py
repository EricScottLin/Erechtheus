#!/usr/bin/env python3
"""预载歌词曲库：从网易云榜单批量拉歌词进本地索引（纯非 AI 的判定地基）。

用法（在容器内执行）::

    docker cp scripts/seed_lyrics.py qqbot-nonebot:/tmp/seed_lyrics.py
    docker exec -i -w /app qqbot-nonebot python /tmp/seed_lyrics.py --max 600

说明：
- 只会「补充」索引，不会删除已有的；重复执行幂等（除非 --reseed）。
- 预载后数据落在 data/qqbot_lyric/library.json，bot 重启自动加载。
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import nonebot  # noqa: E402

nonebot.init()
nonebot.load_plugin("nonebot_plugin_localstore")
nonebot.load_plugin(Path("plugins/qqbot_group"))
nonebot.load_plugin(Path("plugins/qqbot_lyric"))


async def main(max_songs: int, reseed: bool) -> int:
    from nonebot import get_plugin

    mod = get_plugin("qqbot_lyric").module
    lib = mod.library
    print(f"预载前索引：{lib.index.song_count} 首（来自 {mod.index_store.path}）")
    if lib.index.song_count == 0:
        print("警告：磁盘索引为空，将只预载种子榜单")
    if reseed:
        for p in (mod.index_store.path, mod.index_store.seed_path):
            if p and p.is_file():
                p.unlink()
                print(f"已删除 {p.name}（--reseed）")
    # 关键：以**磁盘上的索引**为准并入新数据，不能拿空索引去拉（会覆盖掉已有曲库）
    loaded = await mod.index_store.build_from_seeds(
        lib.index,
        timeout=10,
        max_songs=max_songs,
        on_progress=lambda done, total: print(f"  进度 {done}/{total}", flush=True),
    )
    # build_from_seeds 内部已经落盘；这里再存一次保证 seeded 标记写入
    if loaded:
        mod._save_index()
    print(f"新载入 {loaded} 首，索引共 {lib.index.song_count} 首")
    await lib.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=400, help="最多预载多少首歌")
    parser.add_argument("--reseed", action="store_true", help="丢弃旧索引重新预载")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.max, args.reseed)))
