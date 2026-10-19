"""B站视频「解析 → 压缩 → 发送」流水线。

流程：wbi/view 拿元信息 + cid → playurl 取 DASH 流 →
ffmpeg **直连 CDN 边下边转**成小体积 mp4（默认 480P / CRF30）→ 交给 OneBot 发到群里。
（2026-10 实测：边下边转比「先下载完再转」快一倍多，171s 视频 23s → 8s。）

设计约束（见 README）：
- 只在 NoneBot 容器里跑（容器内自带 ffmpeg 7.x）。
- 全程单并发（_lock），避免多个人同时发链接把 8 核 CPU 打满。
- 临时文件放 data/bili_video/（容器内 /app/data/bili_video，已持久化挂载），
  发送完立即删除；每次任务开始顺手清掉超过 1 小时的残留。
- 发送走 base64://（LLOneBot 的 uri2local 支持 base64/http/file 三种），
  这样不需要让 LLBot 容器和 NoneBot 容器共享目录。
"""
import asyncio
import base64
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from nonebot.log import logger

from .api import (
    SESSDATA,
    BiliError,
    _short_side,
    fetch_playurl,
    fetch_video,
    pick_streams,
)

# ---------------- 可配置项（环境变量，.env.prod 里可改） ----------------
ENABLE = os.getenv("BILI_VIDEO_ENABLE", "1") == "1"
MAX_DURATION = int(os.getenv("BILI_VIDEO_MAX_DURATION", "600"))   # 秒，超了不发
MAX_MB = float(os.getenv("BILI_VIDEO_MAX_MB", "20"))              # 压缩后上限
HEIGHT = int(os.getenv("BILI_VIDEO_HEIGHT", "480"))               # 压缩后最大高度
CRF = int(os.getenv("BILI_VIDEO_CRF", "30"))                      # 越大越糊越小
QN = int(os.getenv("BILI_VIDEO_QN", "64"))                        # 源画质(64=720P)

# y / y 720p / y 1080p 对应的档位
QUALITY_QN = {360: 16, 480: 32, 720: 64, 1080: 80}       # playurl 请求的 qn
QUALITY_MAX_MB = {360: 15.0, 480: 20.0, 720: 40.0, 1080: 80.0}
QUALITY_KBPS = {360: 160, 480: 280, 720: 650, 1080: 1300}  # 估算体积用
TMP_DIR = Path(os.getenv("BILI_VIDEO_TMP", "data/bili_video"))

# QQ 视频消息对时长/体积有限制（客户端与服务端都可能拒绝），这里保守一点
HARD_MAX_MINUTES = 30

_lock = asyncio.Lock()


class VideoSkip(BiliError):
    """无法发送（过长/过大/无画面等），消息直接给用户看。"""


def busy() -> bool:
    """当前是否有转码任务在跑（用于「稍后再试」提示）。"""
    return _lock.locked()


def is_enabled() -> bool:
    return ENABLE


def has_account() -> bool:
    """是否配置了 B站账号 Cookie（决定能否拿 720P/1080P）。"""
    return bool(SESSDATA)


def _cleanup_old(max_age: float = 3600) -> None:
    """清掉超过 1 小时的残留临时目录。"""
    try:
        if not TMP_DIR.exists():
            return
        now = time.time()
        for p in TMP_DIR.iterdir():
            try:
                if now - p.stat().st_mtime > max_age:
                    shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[bilibili] 清理临时目录失败: {e}")


def quality_of(height: int) -> int:
    """把任意高度归到 360/480/720/1080 档位。"""
    return min(QUALITY_QN, key=lambda k: abs(k - int(height)))


def estimate_mb(duration: int, height: int | None = None) -> float:
    """估算压缩后体积，给确认提示用（只是提示值，实际以转码结果为准）。"""
    if duration <= 0:
        return 0.0
    h = quality_of(height or HEIGHT)
    est = QUALITY_KBPS[h] * duration / 8 / 1024
    return round(min(QUALITY_MAX_MB[h], est), 1)


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


async def _run_ffmpeg(args: list, timeout: int = 600) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise BiliError("压缩超时（视频可能太长），已放弃")
    if proc.returncode != 0:
        tail = (err or b"").decode("utf-8", "ignore")[-800:]
        logger.warning(f"[bilibili] ffmpeg 失败 rc={proc.returncode}: {tail}")
        raise BiliError(f"视频转码失败：{tail.splitlines()[-1] if tail else '未知错误'}")


_FF_HEADERS = (
    "Referer: https://www.bilibili.com/\r\n"
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\r\n"
)


async def prepare(key: str, value: str) -> dict:
    """拿元信息，判断能不能发。返回 meta（含 cid/duration/标题）。"""
    meta = await fetch_video(key, value)
    if not meta.get("bvid"):
        raise BiliError("没拿到 BV 号，链接可能不是视频页")
    if meta.get("pages", 1) > 1:
        meta["note"] = f"（共 {meta['pages']} 个分P，只发 P1）"
    dur = int(meta.get("duration") or 0)
    if dur > MAX_DURATION:
        raise VideoSkip(
            f"视频时长 {dur // 60}:{dur % 60:02d}，超过 {MAX_DURATION // 60} 分钟上限，"
            f"没发。想改：.env 里 BILI_VIDEO_MAX_DURATION"
        )
    if dur <= 0:
        raise BiliError("视频时长为 0，可能已失效")
    return meta


def _input_args(url: str) -> list:
    """ffmpeg 直接读 CDN 流：带上 Referer/UA，边下边转（比先下载再转快一倍）。"""
    return ["-headers", _FF_HEADERS, "-i", url]


def _candidates(play: dict, video, audio) -> tuple[list, list]:
    """返回 (URL 组合列表, 输入映射参数)。

    先 base+base，失败再换 backupUrl（B站 CDN 节点偶尔抽风）。
    """
    if video is None:
        durls = play.get("durl") or []
        if not durls:
            raise VideoSkip("这个视频拿不到播放地址（可能付费/番剧/已下架）")
        return [[durls[0]["url"]]], ["-map", "0:v:0", "-map", "0:a:0?"]

    vurls = [video.get("baseUrl")] + list(video.get("backupUrl") or [])
    aurls = ([audio.get("baseUrl")] + list(audio.get("backupUrl") or [])) if audio else [None]
    combos = []
    for v in [u for u in vurls if u][:2]:
        for a in [u for u in aurls if u][:2] or [None]:
            combos.append([v] + ([a] if a else []))
    maps = ["-map", "0:v:0"] + (["-map", "1:a:0"] if audio else [])
    return combos, maps


async def render(meta: dict, height: int | None = None,
                 on_progress=None) -> tuple[Path, int]:
    """取播放地址 → ffmpeg 直连边下边转 → 返回 (mp4 路径, 实际高度)。

    height: 目标高度（360/480/720/1080），None 用默认 HEIGHT。
    """
    h = quality_of(height or HEIGHT)
    max_mb = QUALITY_MAX_MB[h]
    qn = QUALITY_QN[h]
    async with _lock:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        _cleanup_old()
        tmp = Path(tempfile.mkdtemp(prefix="job_", dir=str(TMP_DIR)))
        try:
            play = await fetch_playurl(meta["bvid"], meta["cid"], qn=qn)
            video, audio, duration = pick_streams(play, h)
            actual_h = _short_side(video) if video else 0
            dur = duration or int(meta.get("duration") or 0)
            combos, maps = _candidates(play, video, audio)

            out = tmp / "out.mp4"
            vbr = max(150, int(max_mb * 8 * 1024 / dur) - 64) if dur > 0 else 400
            # 横屏：高 480（约 854x480）；竖屏：宽约 270（270x480）
            vf = (f"scale=w={h * 2}:h={h}:force_original_aspect_ratio=decrease,"
                  f"scale=trunc(iw/2)*2:trunc(ih/2)*2")
            last: BiliError | None = None
            for i, urls in enumerate(combos):
                cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
                for u in urls:
                    cmd += _input_args(u)
                cmd += maps + [
                    "-vf", vf,
                    "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", str(CRF), "-maxrate", f"{vbr}k", "-bufsize", f"{vbr * 2}k",
                    "-c:a", "aac", "-b:a", "64k", "-ac", "2",
                    "-movflags", "+faststart",
                    str(out),
                ]
                try:
                    await _run_ffmpeg(cmd, timeout=max(180, dur))
                except BiliError as e:
                    last = e
                    logger.warning(f"[bilibili] 直连转码第 {i + 1} 次失败: {e}")
                    await asyncio.sleep(1.0 * (i + 1))
                    continue
                if not out.exists() or out.stat().st_size < 1024:
                    last = BiliError("转码输出为空，可能视频源异常")
                    continue
                size_mb = out.stat().st_size / 1024 / 1024
                if size_mb > max_mb * 1.15:
                    # 码率给多了：降一半再压一次
                    vbr2 = max(120, vbr // 2)
                    cmd[cmd.index("-maxrate") + 1] = f"{vbr2}k"
                    cmd[cmd.index("-bufsize") + 1] = f"{vbr2 * 2}k"
                    cmd[cmd.index("-crf") + 1] = str(min(CRF + 4, 36))
                    logger.info(f"[bilibili] 首次 {size_mb:.1f}M 超限，降码率重试")
                    await _run_ffmpeg(cmd, timeout=max(180, dur))
                    size_mb = out.stat().st_size / 1024 / 1024
                    if size_mb > max_mb * 1.15:
                        raise VideoSkip(f"压缩后仍有 {size_mb:.1f}M，超过 {max_mb:g}M 上限，没发")
                logger.info(f"[bilibili] 转码完成 {meta.get('bvid')} "
                            f"{actual_h}P(请求{h}P) {size_mb:.2f}M / {dur}s")
                return out, actual_h
            raise last or BiliError("视频处理失败")
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise


def video_segment_string(path: Path) -> str:
    """读 mp4 转 base64:// 字符串（LLOneBot 支持）。"""
    data = path.read_bytes()
    return "base64://" + base64.b64encode(data).decode()


def cleanup(path: Path) -> None:
    try:
        if path.parent.name.startswith("job_") and path.parent.parent == TMP_DIR:
            shutil.rmtree(path.parent, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except Exception:
        pass
