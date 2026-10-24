"""成绩结算图识别：OCR → 结构化字段。

只用来看「我自己打出来的成绩」：识别曲名、难度、达成率、判定、DX 分数、连击。
结果存本地档案，不上传游戏、不上传查分器，也不构造任何数据。

OCR 用 rapidocr（onnxruntime，本地 CPU，约 3~5 秒/张）。识别是启发式的：
按文本框坐标找「标签 + 右侧最近的数字」，缺失的判定用总物量倒推。
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

from nonebot.log import logger

try:
    from rapidocr_onnxruntime import RapidOCR

    AVAILABLE = True
    _ERROR = ""
except Exception as exc:  # pragma: no cover - 部署缺依赖时给出明确提示
    AVAILABLE = False
    _ERROR = f"{type(exc).__name__}: {exc}"
    RapidOCR = None

_ENGINE = None

DIFFICULTIES = {
    "BASIC": 0,
    "初级": 0,
    "ADVANCED": 1,
    "高级": 1,
    "EXPERT": 2,
    "专家": 2,
    "MASTER": 3,
    "大师": 3,
    "RE:MASTER": 4,
    "REMASTER": 4,
}

# 结算屏上会被 OCR 读到的非曲名文本，避免误当成歌名
_NOISE = {
    "CLEAR",
    "FULLCOMBO",
    "ALLPERFECT",
    "DX",
    "SCORE",
    "PERFECT",
    "GREAT",
    "GOOD",
    "MISS",
    "MASTER",
    "EXPERT",
    "ADVANCED",
    "BASIC",
    "CRITICAL",
    "DX分数",
    "最大连击",
    "达成率",
    "评分",
    "曲目",
    "新纪录",
}

_JUDGE_LABELS = (
    ("critical_perfect", ("CRITICALPERFECT", "CRITICAL", "CRIT")),
    ("perfect", ("PERFECT", "PEFECT", "PERFCT")),
    ("great", ("GREAT",)),
    ("good", ("GOOD",)),
    ("miss", ("MISS",)),
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_PERCENT = re.compile(r"(\d{1,3}\.\d{3,4})")
_COMBO = re.compile(r"(\d{1,4})\s*/\s*(\d{1,4})")


def unavailable_reason() -> str:
    return "" if AVAILABLE else _ERROR or "缺少 rapidocr-onnxruntime"


def _engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RapidOCR()
    return _ENGINE


def ocr(data: bytes) -> list[dict]:
    """图片字节 → [{text, score, x, y}, ...]；失败返回空列表。"""
    if not AVAILABLE:
        return []
    try:
        result, _elapse = _engine()(data)
    except Exception as exc:  # pragma: no cover - OCR 内部异常
        logger.warning(f"成绩图识别失败：{type(exc).__name__}: {exc}")
        return []
    items = []
    for box, text, score in result or []:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        items.append(
            {
                "text": str(text).strip(),
                "score": float(score),
                "x": int(min(xs)),
                "y": int(min(ys)),
                "x2": int(max(xs)),
            }
        )
    return items


def _numbers_near(
    items: list[dict],
    label: dict,
    *,
    same_row: int = 14,
    allow_ratio: bool = False,
    both_sides: bool = False,
) -> list[int]:
    """同一行、标签右侧的数字（按 x 由近到远）。"""
    found = []
    for item in items:
        if item is label or abs(item["y"] - label["y"]) > same_row:
            continue
        if not both_sides and item["x"] <= label["x"]:
            continue
        if not allow_ratio and (
            "/" in item["text"]
            or "连击" in item["text"]
            or item["text"].upper().startswith("DX")
        ):
            continue  # 判定数字不取「左/右」格式；连击和 DX 分数正好相反
        for token in _NUMBER.findall(item["text"].replace(",", "")):
            if "." in token:
                continue
            found.append((item["x"], int(float(token))))
    return [value for _x, value in sorted(found)]


def _find_label(items: list[dict], keywords, *, prefer_bottom: bool = False) -> Optional[dict]:
    matches = []
    for item in items:
        compact = re.sub(r"[^A-Za-z\u4e00-\u9fa5:]", "", item["text"]).upper()
        if not compact:
            continue
        if any(keyword in compact for keyword in keywords):
            matches.append(item)
    if not matches:
        return None
    # 判定标签在顶部明细表和底部汇总里都会出现，取最下面那个（结算面板）
    return max(matches, key=lambda item: item["y"]) if prefer_bottom else matches[0]


def parse(items: list[dict]) -> dict:
    """把 OCR 结果整理成结构化字段；识别不到的字段留 None。"""
    texts = [item["text"] for item in items]
    joined = " ".join(texts)

    # 达成率：4 位小数的百分比，取最大的那个（旁边可能有「我的最高」）
    percents = []
    for item in items:
        for token in _PERCENT.findall(item["text"]):
            value = float(token)
            if 0 < value <= 101:
                percents.append((value, item["y"]))
    achievement = max((value for value, _y in percents), default=None)

    # 难度 + 等级
    difficulty, level = None, None
    for item in items:
        compact = re.sub(r"[^A-Za-z\u4e00-\u9fa5:]", "", item["text"]).upper()
        for key, index in DIFFICULTIES.items():
            if key in compact:
                difficulty = index
                numbers = _numbers_near(items, item, same_row=45)
                level = numbers[0] if numbers and numbers[0] <= 15 else level
                break
        if difficulty is not None:
            break

    # 曲名：难度行与达成率之间、含拉丁字母的较长文本
    title = None
    if difficulty is not None:
        title_label = _find_label(items, ("MASTER", "EXPERT", "ADVANCED", "BASIC", "大师", "专家"))
        title_y = title_label["y"] if title_label else 0
        achievement_y = next((y for value, y in percents if value == achievement), 10**6)
        candidates = []
        for item in items:
            text = item["text"].strip()
            compact = re.sub(r"[^A-Za-z]", "", text).upper()
            if len(compact) < 4 or compact in _NOISE:
                continue
            if any(compact.startswith(noise) for noise in _NOISE):
                continue
            if not (title_y - 10 <= item["y"] <= achievement_y):
                continue
            if not re.search(r"[A-Za-z]{3}", text):
                continue
            candidates.append((len(compact), item["y"], text))
        if candidates:
            title = max(candidates)[2]

    # 判定：标签 + 同行右侧数字；缺失的用总物量倒推
    judgements = {}
    for key, keywords in _JUDGE_LABELS:
        label = _find_label(items, keywords, prefer_bottom=True)
        numbers = _numbers_near(items, label) if label else []
        if numbers:
            judgements[key] = numbers[0]

    # 连击与 DX 分数都是「左/右」格式，靠标签区分，不能随便取第一个
    combo = None
    total_notes = None
    combo_label = _find_label(items, ("连击",))
    if combo_label:
        numbers = _numbers_near(items, combo_label, same_row=60, allow_ratio=True)
        if len(numbers) >= 2 and 0 < numbers[0] <= numbers[1]:
            combo, total_notes = numbers[0], numbers[1]
    if total_notes is None:
        for item in items:
            if "连击" in item["text"]:
                numbers = [int(n) for n in _NUMBER.findall(item["text"].replace(",", ""))]
                if len(numbers) >= 2 and 0 < numbers[0] <= numbers[1]:
                    combo, total_notes = numbers[0], numbers[1]
                    break

    # DX 分数：同排含「/」的文本框，取离标签最近的那个（数字在标签左边）
    dx_score = None
    dx_label = _find_label(items, ("DX分数", "DXSCORE"))
    if dx_label:
        best = None
        for item in items:
            if "/" not in item["text"] or abs(item["y"] - dx_label["y"]) > 40:
                continue
            match = _COMBO.search(item["text"].replace(",", ""))
            if not match:
                continue
            left, right = int(match.group(1)), int(match.group(2))
            if not 0 < left <= right:
                continue
            distance = abs(item["x"] - dx_label["x"])
            if best is None or distance < best[0]:
                best = (distance, left, right)
        if best:
            dx_score = (best[1], best[2])

    derived = []
    if total_notes:
        derived = _solve_judgements(judgements, total_notes, achievement)
        for key, value in derived.items():
            judgements.setdefault(key, value)

    return {
        "title": title,
        "difficulty": difficulty,
        "level": level,
        "achievement": achievement,
        "judgements": judgements,
        "combo": combo,
        "total_notes": total_notes,
        "dx_score": dx_score,
        "derived": sorted(derived),
        "raw": texts,
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


_DIFF_NAMES = {0: "BASIC", 1: "ADVANCED", 2: "EXPERT", 3: "MASTER", 4: "Re:MASTER"}

# 判定权重：PERFECT（含 CRITICAL）1.0、GREAT 0.8、GOOD 0.5、MISS 0；
# BREAK 全部 CRITICAL 时额外 +1%（所以满达成率是 101%）。
_WEIGHT = {"critical_perfect": 1.0, "perfect": 1.0, "great": 0.8, "good": 0.5, "miss": 0.0}
_KEYS = ("critical_perfect", "perfect", "great", "good", "miss")


def achievement_of(judgements: dict, total: int, break_bonus: float = 1.0) -> float:
    """按判定算达成率（%）。break_bonus 是 BREAK 全 CRITICAL 时的额外百分比（0~1）。"""
    points = sum(_WEIGHT[key] * judgements.get(key, 0) for key in _KEYS)
    return round(points / total * 100 + break_bonus, 4)


def _solve_judgements(known: dict, total: int, achievement) -> dict:
    """总物量 + 达成率 → 补齐 OCR 漏掉的判定。

    达成率的 BREAK 加成在 0~1% 之间（全 CRITICAL 才是 1%），所以每个候选组合
    只要算出的达成率落在「基础分 ~ 基础分+1%」区间内就算一致；唯一解才采纳。
    """
    missing = [key for key in _KEYS if key not in known]
    if not missing or achievement is None:
        return {}
    remaining = total - sum(known.values())
    if remaining < 0:
        return {}
    candidates = _distribute(missing, remaining)
    matched = []
    for combo in candidates:
        merged = {**known, **combo}
        low = achievement_of(merged, total, 0.0)
        high = achievement_of(merged, total, 1.0)
        if low - 0.02 <= achievement <= high + 0.02:
            matched.append(combo)
    return matched[0] if len(matched) == 1 else {}


def _distribute(keys: list[str], total: int) -> list[dict]:
    """把 total 个判定分配给 keys 的所有组合（keys ≤ 2 时规模很小）。"""
    if not keys:
        return []
    if len(keys) == 1:
        return [{keys[0]: total}]
    if len(keys) == 2:
        return [{keys[0]: total - i, keys[1]: i} for i in range(total + 1)]
    # 三个以上不做穷举，交给 OCR
    return []


def summary(result: dict) -> str:
    """给人看的摘要；识别不到的字段用 ? 标记，不硬编。"""
    title = result.get("title") or "（没识别到曲名）"
    difficulty = _DIFF_NAMES.get(result.get("difficulty"), "?")
    level = result.get("level")
    achievement = result.get("achievement")
    judges = result.get("judgements") or {}
    cp, perfect = judges.get("critical_perfect"), judges.get("perfect")
    great, good, miss = judges.get("great"), judges.get("good"), judges.get("miss")
    lines = [f"🎵 {title}", f"{difficulty} {level if level is not None else '?'}"]
    if achievement is not None:
        lines[-1] += f" · 达成率 {achievement:.4f}%"
    derived = set(result.get("derived") or ())
    judge_parts = []
    if cp is not None or perfect is not None:
        judge_parts.append(f"Perfect {cp if cp is not None else '?'}+{perfect if perfect is not None else '?'}")
    for name, key, value in (("Great", "great", great), ("Good", "good", good), ("Miss", "miss", miss)):
        if value is not None:
            judge_parts.append(f"{name} {value}" + ("*" if key in derived else ""))
    if judge_parts:
        lines.append(" · ".join(judge_parts))
    extras = []
    dx = result.get("dx_score")
    if dx:
        extras.append(f"DX {dx[0]}/{dx[1]}")
    combo, total = result.get("combo"), result.get("total_notes")
    if combo is not None and total is not None:
        extras.append(f"连击 {combo}/{total}")
    if extras:
        lines.append(" · ".join(extras))
    if derived:
        lines.append("（* 按总物量 + 达成率推算；达成率含 BREAK 加成 0~1%）")
    if result.get("achievement") is None and not judges:
        lines.append("（这张图没识别出有效成绩，换一张正对屏幕的原图会好很多）")
    return "\n".join(lines)


def archive(user_id: str, result: dict) -> Path:
    """把识别结果存进本地档案（按用户分目录），返回文件路径。"""
    try:
        from nonebot_plugin_localstore import get_plugin_data_dir

        base = get_plugin_data_dir() / "scores"
    except Exception:  # pragma: no cover - localstore 缺失时退到 cwd
        base = Path.cwd() / "data" / "qqbot_maimai_dx" / "scores"
    directory = base / str(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
