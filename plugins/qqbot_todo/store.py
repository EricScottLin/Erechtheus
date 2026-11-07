"""待办事项的存储层。

数据目录由 ``nonebot_plugin_localstore`` 提供（容器内 ``/app/data/qqbot_todo``，
对应宿主机 ``/srv/qqbot/data/qqbot_todo``）；localstore 缺失时退回
``cwd/data/qqbot_todo``。

目录结构::

    data/qqbot_todo/
    ├── tasks.json     # 待办事项
    └── export_done.json   # 上次 /待办 导出给 DSH 的快照（用于识别「已被处理」）

``tasks.json`` 结构::

    {
      "next_id": 12,
      "tasks": [
        {
          "id": 11,
          "title": "给群管插件加个 /烟 的冷却",
          "status": "open",          # open | doing | done | dropped
          "priority": 1,             # 1 紧急 / 2 高 / 3 中 / 4 低
          "tags": ["群管"],
          "note": "",
          "created_by": "10001",
          "created_at": 1761100000,
          "done_at": null,
          "remind_at": 1761186400,   # 可选：到点私聊提醒创建人
          "reminded": false,
          "source": {"type": "private", "user_id": "10001"},
          "dsh_task_id": "",         # 联动：DSH 任务 ID（安排下去做之后回填）
          "history": [{"at": 1761100000, "by": "10001", "action": "create", "detail": ""}]
        }
      ]
    }

设计要点：
- 单文件 JSON + ``asyncio.Lock``，写操作用临时文件原子替换，坏文件不会让命令崩；
- 已完成的待办不删除，保留在列表里（最多保留 ``KEEP_DONE`` 条，超出清最老的），
  这样 DSH 那边提前做完也能对得上账；
- 分号语法里的 ``.`` ``!`` ``#`` 是版本控制风格的状态标记，删除 / 改名用它表达。
"""

import asyncio
import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nonebot.log import logger

try:
    from nonebot_plugin_localstore import get_plugin_data_dir

    DATA_DIR: Path = get_plugin_data_dir()
except Exception:  # pragma: no cover - localstore 缺失时退回当前目录
    DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "qqbot_todo"

TASKS_FILE_NAME = "tasks.json"
EXPORT_FILE_NAME = "export_done.json"

STATUSES = ("open", "doing", "done", "dropped")
STATUS_LABELS = {
    "open": "待办",
    "doing": "进行中",
    "done": "已完成",
    "dropped": "已放弃",
}

# 已完成 / 已放弃的待办最多保留多少条（超出时清最老的）
KEEP_DONE = 200

_lock = asyncio.Lock()
_cache: dict[str, Any] | None = None


# ---------------------------------------------------------------- 路径
def tasks_file() -> Path:
    return DATA_DIR / TASKS_FILE_NAME


def export_file() -> Path:
    return DATA_DIR / EXPORT_FILE_NAME


def reset_cache() -> None:
    """测试用：清空内存缓存。"""
    global _cache
    _cache = None


def bootstrap() -> None:
    """确保数据目录与文件存在（幂等），并清掉内存缓存。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not tasks_file().exists():
        _write_raw({"next_id": 1, "tasks": []})
    reset_cache()


# ---------------------------------------------------------------- 读写
def _write_raw(data: dict[str, Any]) -> None:
    path = tasks_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)  # 原子替换，避免写一半损坏


def _int_or(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_task(raw: dict[str, Any], fallback_id: int) -> dict[str, Any] | None:
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    status = raw.get("status")
    if status not in STATUSES:
        status = "open"
    tags = raw.get("tags")
    history = raw.get("history")
    source = raw.get("source")
    return {
        "id": _int_or(raw.get("id"), fallback_id),
        "title": title.strip(),
        "status": status,
        "priority": min(4, max(1, _int_or(raw.get("priority"), 3))),
        "tags": [str(t) for t in tags if str(t).strip()] if isinstance(tags, list) else [],
        "note": str(raw.get("note") or ""),
        "created_by": str(raw.get("created_by") or ""),
        "created_at": _int_or(raw.get("created_at"), int(time.time())),
        "done_at": _int_or(raw.get("done_at")) or None,
        "remind_at": _int_or(raw.get("remind_at")) or None,
        "reminded": bool(raw.get("reminded")),
        "source": source if isinstance(source, dict) else {},
        "dsh_task_id": str(raw.get("dsh_task_id") or ""),
        "history": [h for h in history if isinstance(h, dict)] if isinstance(history, list) else [],
    }


def _normalize(raw: Any) -> dict[str, Any]:
    """把 JSON 内容规整成 ``{"next_id": int, "tasks": [...]}``。"""
    if not isinstance(raw, dict):
        return {"next_id": 1, "tasks": []}
    tasks: list[dict[str, Any]] = []
    seen: set[int] = set()
    next_id = max(1, _int_or(raw.get("next_id"), 1))
    for index, item in enumerate(raw.get("tasks") or [], start=1):
        if not isinstance(item, dict):
            continue
        task = _normalize_task(item, index)
        if task is None or task["id"] in seen:
            continue
        seen.add(task["id"])
        next_id = max(next_id, task["id"] + 1)
        tasks.append(task)
    return {"next_id": next_id, "tasks": tasks}


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    path = tasks_file()
    if not path.exists():
        _cache = {"next_id": 1, "tasks": []}
        return _cache
    try:
        _cache = _normalize(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001 —— 坏文件不应让命令崩溃
        logger.warning(f"[qqbot_todo] 读取待办文件失败：{e}，按空列表处理")
        _cache = {"next_id": 1, "tasks": []}
    return _cache


def _save(data: dict[str, Any]) -> None:
    _prune(data)
    _write_raw(data)


def _prune(data: dict[str, Any]) -> None:
    """已结束（完成/放弃）的待办只保留最近 KEEP_DONE 条。"""
    finished = [t for t in data["tasks"] if t["status"] in ("done", "dropped")]
    if len(finished) <= KEEP_DONE:
        return
    finished.sort(key=lambda t: (t.get("done_at") or t["created_at"], t["id"]))
    drop_ids = {t["id"] for t in finished[: len(finished) - KEEP_DONE]}
    data["tasks"] = [t for t in data["tasks"] if t["id"] not in drop_ids]


# ---------------------------------------------------------------- 查询
def all_tasks() -> list[dict[str, Any]]:
    """全部待办（按 id 升序），返回副本。"""
    return [dict(t) for t in sorted(_load()["tasks"], key=lambda t: t["id"])]


def active_tasks() -> list[dict[str, Any]]:
    """未结束（待办 / 进行中）的待办，按优先级、id 排序。"""
    tasks = [t for t in all_tasks() if t["status"] in ("open", "doing")]
    return sort_active(tasks)


def _last_touched_ms(task: dict[str, Any]) -> int:
    stamps = [int(task.get("created_at") or 0)]
    stamps += [int(h.get("at") or 0) for h in task.get("history") or []]
    return max(stamps) if stamps else 0


def sort_active(tasks: list[dict[str, Any]], order: str = "priority") -> list[dict[str, Any]]:
    """未完成待办的排序。

    - ``priority``（默认）：优先级优先，同级里放得越久越靠前（先清陈年老账）
    - ``new``：最近新建 / 变更的在前
    - ``old``：搁置最久的在前
    """
    if order == "new":
        return sorted(tasks, key=lambda t: (_last_touched_ms(t), t["id"]), reverse=True)
    if order == "old":
        return sorted(tasks, key=lambda t: (_last_touched_ms(t), t["id"]))
    return sorted(tasks, key=lambda t: (t["priority"], _last_touched_ms(t), t["id"]))


def get_task(task_id: int) -> dict[str, Any] | None:
    for task in _load()["tasks"]:
        if task["id"] == task_id:
            return dict(task)
    return None


def find_task(keyword: str) -> list[dict[str, Any]]:
    """按标题 / 备注 / 标签模糊查找（先找未结束的，找不到再看已结束的）。"""
    keyword = keyword.strip()
    if not keyword:
        return []

    def _hit(t: dict[str, Any]) -> bool:
        return keyword in t["title"] or keyword in t["note"] or keyword in " ".join(t["tags"])

    active = [t for t in _load()["tasks"] if _hit(t) and t["status"] in ("open", "doing")]
    if active:
        return sort_active([dict(t) for t in active])
    return sort_active([dict(t) for t in _load()["tasks"] if _hit(t) and t["status"] != "dropped"])


def pending_reminders(now: float | None = None) -> list[dict[str, Any]]:
    """到点未提醒的待办。"""
    now = time.time() if now is None else now
    return [
        dict(t)
        for t in _load()["tasks"]
        if t["status"] in ("open", "doing") and t["remind_at"] and not t["reminded"] and t["remind_at"] <= now
    ]


def next_reminder_at() -> float | None:
    """最近一条待提醒的时间戳（用于日志 / 调试）。"""
    times = [
        t["remind_at"]
        for t in _load()["tasks"]
        if t["status"] in ("open", "doing") and t["remind_at"] and not t["reminded"]
    ]
    return min(times) if times else None


# ---------------------------------------------------------------- 修改
async def add_task(
    title: str,
    *,
    priority: int = 3,
    tags: list[str] | None = None,
    note: str = "",
    by: str,
    source: dict[str, Any] | None = None,
    remind_at: int | None = None,
) -> dict[str, Any]:
    """新增一条待办，返回落盘后的完整记录。"""
    async with _lock:
        data = _load()
        task_id = data["next_id"]
        data["next_id"] = task_id + 1
        now = int(time.time())
        task = {
            "id": task_id,
            "title": title.strip(),
            "status": "open",
            "priority": min(4, max(1, int(priority))),
            "tags": list(tags or []),
            "note": note,
            "created_by": str(by),
            "created_at": now,
            "done_at": None,
            "remind_at": remind_at,
            "reminded": False,
            "source": dict(source or {}),
            "dsh_task_id": "",
            "history": [{"at": now, "by": str(by), "action": "create", "detail": ""}],
        }
        data["tasks"].append(task)
        _save(data)
        return dict(task)


async def update_task(task_id: int, by: str, **fields: Any) -> dict[str, Any] | None:
    """更新字段；任务不存在返回 ``None``。"""
    async with _lock:
        data = _load()
        for task in data["tasks"]:
            if task["id"] != task_id:
                continue
            task.update(fields)
            _save(data)
            return dict(task)
        return None


async def set_status(task_id: int, status: str, by: str, detail: str = "") -> dict[str, Any] | None:
    """改状态并记一条历史。"""
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    async with _lock:
        data = _load()
        for task in data["tasks"]:
            if task["id"] != task_id:
                continue
            task["status"] = status
            task["done_at"] = int(time.time()) if status in ("done", "dropped") else None
            task.setdefault("history", []).append(
                {"at": int(time.time()), "by": str(by), "action": status, "detail": detail}
            )
            _save(data)
            return dict(task)
        return None


async def mark_reminded(task_ids: set[int]) -> None:
    if not task_ids:
        return
    async with _lock:
        data = _load()
        changed = False
        for task in data["tasks"]:
            if task["id"] in task_ids and not task["reminded"]:
                task["reminded"] = True
                changed = True
        if changed:
            _save(data)


async def append_history(task_id: int, by: str, action: str, detail: str = "") -> None:
    async with _lock:
        data = _load()
        for task in data["tasks"]:
            if task["id"] == task_id:
                task.setdefault("history", []).append(
                    {"at": int(time.time()), "by": str(by), "action": action, "detail": detail}
                )
                _save(data)
                return


async def set_reminder(task_id: int, by: str, remind_at: int) -> dict[str, Any] | None:
    """设置提醒时间并重置已提醒标记（可以给同一件事反复设提醒）。"""
    async with _lock:
        data = _load()
        for task in data["tasks"]:
            if task["id"] != task_id:
                continue
            task["remind_at"] = int(remind_at)
            task["reminded"] = False
            task.setdefault("history", []).append(
                {"at": int(time.time()), "by": str(by), "action": "remind",
                 "detail": datetime.fromtimestamp(int(remind_at)).strftime("%m-%d %H:%M")}
            )
            _save(data)
            return dict(task)
        return None


def snapshot(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """导出用的轻量快照 ``{id, title, status, updated_at, dsh_task_id}``。"""
    snapshot_rows: list[dict[str, Any]] = []
    for t in tasks:
        snapshot_rows.append(
            {
                "id": t["id"],
                "title": t["title"],
                "status": t["status"],
                "updated_at": datetime.fromtimestamp(_last_touched(t)).isoformat(),
                "dsh_task_id": t.get("dsh_task_id") or "",
            }
        )
    return snapshot_rows


def _last_touched(task: dict[str, Any]) -> int:
    stamps = [int(task.get("created_at") or 0)]
    stamps += [int(h.get("at") or 0) for h in task.get("history") or []]
    return max(stamps)


async def replace_all(data: dict[str, Any]) -> None:
    """联动接口：整体写入（DSH 侧回写用）。"""
    async with _lock:
        _write_raw(_normalize(data))
        reset_cache()


# ---------------------------------------------------------------- 导出（联动 DSH）
def load_export_snapshot() -> dict[int, str]:
    """读上次导出的快照 ``{task_id: updated_at}``。"""
    path = export_file()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_todo] 读取导出快照失败：{e}")
        return {}
    tasks = raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(tasks, list):
        return {}
    snapshot: dict[int, str] = {}
    for item in tasks:
        if isinstance(item, dict):
            snapshot[_int_or(item.get("id"))] = str(item.get("updated_at") or "")
    return snapshot


def save_export_snapshot(tasks: list[dict[str, Any]]) -> None:
    path = export_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"exported_at": int(time.time()), "tasks": tasks}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def today_iso() -> str:
    return date.today().isoformat()
