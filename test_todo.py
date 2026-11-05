#!/usr/bin/env python3
"""qqbot_todo 待办插件测试。

直接跑：``.venv/bin/python test_todo.py``。
需要 nonebot_plugin_apscheduler（本地 venv 若没有，会借用 /srv/qqbot/.venv）。
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
for extra in ("/srv/qqbot/.venv/lib/python3.10/site-packages",):
    if extra not in sys.path and Path(extra).is_dir():
        sys.path.append(extra)
os.environ.setdefault("COMMAND_START", '["/", ""]')

import nonebot

nonebot.init(superusers={"10001"})

from nonebot.adapters.onebot.v11 import (
    Adapter as OneBotAdapter,
    Bot as OneBotBot,
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Sender
from nonebot.message import handle_event

SELF_ID = "1"
SUPER = 10001
OTHER = 20002
GROUP = 999888

results: list[tuple[str, bool, str]] = []


def normalize(text: str) -> str:
    """QQ 客户端会在每个汉字间插空格，测试里比较前先去掉（合并连续空格 + 去字间空格）。"""
    import re as _re

    text = _re.sub(r"\s+", " ", (text or "").strip())
    prev = None
    while prev != text:
        prev = text
        text = _re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", text)
    return text


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail if not cond else ""))
    print(f"  {'[PASS]' if cond else '[FAIL]'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)
ADAPTER = next(iter(driver._adapters.values()))
if not any(m.module_name == "nonebot_plugin_apscheduler" for m in nonebot.get_loaded_plugins()):
    nonebot.load_plugin("nonebot_plugin_apscheduler")
plugin = nonebot.load_plugin("plugins.qqbot_todo")
check("插件加载", plugin is not None and len(plugin.matcher) == 4, str(plugin and len(plugin.matcher)))

from plugins.qqbot_todo import helpers as H  # noqa: E402  （必须在插件加载之后再导入，否则会被当作提前 import）


class FakeBot(OneBotBot):
    def __init__(self) -> None:
        super().__init__(ADAPTER, SELF_ID)
        self.calls: list[tuple[str, dict]] = []
        self.message_id = 0

    async def call_api(self, api: str, **data):  # type: ignore[override]
        self.calls.append((api, data))
        self.message_id += 1
        return {"message_id": self.message_id}

    def texts(self) -> list[str]:
        out = []
        for api, data in self.calls:
            msg = data.get("message")
            if msg is not None:
                out.append(Message(msg).extract_plain_text())
        return out

    def last(self) -> str:
        texts = self.texts()
        return texts[-1] if texts else ""

    def last_message(self):
        for api, data in reversed(self.calls):
            if data.get("message") is not None:
                return Message(data["message"])
        return None


def make_private(msg, user_id: int = SUPER) -> PrivateMessageEvent:
    m = msg if isinstance(msg, Message) else Message(msg)
    return PrivateMessageEvent(
        time=int(time.time()), self_id=SELF_ID, post_type="message", sub_type="friend",
        user_id=user_id, message=m, original_message=m, message_type="private",
        raw_message=str(m), font=1, message_id=1,
        sender=Sender(user_id=user_id, nickname="月见"),
    )


def make_group(msg, user_id: int = SUPER) -> GroupMessageEvent:
    m = msg if isinstance(msg, Message) else Message(msg)
    return GroupMessageEvent(
        time=int(time.time()), self_id=SELF_ID, post_type="message", sub_type="normal",
        user_id=user_id, group_id=GROUP, message=m, original_message=m,
        message_type="group", raw_message=str(m), font=1, message_id=1,
        sender=Sender(user_id=user_id, role="owner"),
    )


async def send(text, *, user_id=SUPER, group=False) -> FakeBot:
    bot = FakeBot()
    await handle_event(bot, make_group(text, user_id) if group else make_private(text, user_id))
    return bot


async def main() -> None:
    store = plugin.module.store
    cfg = plugin.module.plugin_config

    tmp = Path(tempfile.mkdtemp(prefix="qqbot-todo-"))
    store.DATA_DIR = tmp
    store.reset_cache()
    store.bootstrap()
    check("数据文件初始化", store.tasks_file().exists())

    # ---------------- 纯逻辑：解析 ----------------
    q = H.parse_quick("!!修复 /烟 的冷却 #群管 #插件")
    check("parse_quick: 优先级+标签", q == {"title": "修复 /烟 的冷却", "priority": 2, "tags": ["群管", "插件"]}, str(q))
    q = H.parse_quick("!!高优先级")
    check("parse_quick: !! 高", q and q["priority"] == 2, str(q))
    q = H.parse_quick("!紧急上线")
    check("parse_quick: ! 紧急", q and q["priority"] == 1, str(q))
    q = H.parse_quick("~顺手看看文档")
    check("parse_quick: ~ 低", q and q["priority"] == 4, str(q))
    q = H.parse_quick("#只有标签")
    check("parse_quick: 只有标签返回 None", q is None, str(q))

    now = datetime(2026, 10, 21, 14, 0)
    cases = {
        "18:00": "2026-10-21 18:00",
        "9点": "2026-10-22 09:00",
        "明天 9 点": "2026-10-22 09:00",
        "后天10点": "2026-10-23 10:00",
        "3天后": "2026-10-24 20:00",
        "3天后 9点": "2026-10-24 09:00",
        "2小时后": "2026-10-21 16:00",
        "2026-11-11 20:00": "2026-11-11 20:00",
        "11月11日": "2026-11-11 20:00",
        "下周三": "2026-10-28 20:00",
        "后天下午3点": "2026-10-23 15:00",
    }
    bad = {}
    for text, expected in cases.items():
        ts, err = H.parse_when(text, now)
        got = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else f"ERR {err}"
        if got != expected:
            bad[text] = f"{got} != {expected}"
    check("parse_when 各类时间", not bad, str(bad))
    ts, err = H.parse_when("abc", now)
    check("parse_when 非法输入给提示", ts is None and "时间看不懂" in err, err)

    a = H.parse_command("我做完了", ["3"])
    check("parse_command: 我做完了 3", a.name == "done" and a.target == "3", str(a))
    a = H.parse_command("提醒", ["3", "明天", "9点"])
    check("parse_command: 提醒 3 明天 9点", a.name == "remind" and a.target == "3" and normalize(a.text) == "明天 9点", str(a))
    check("parse_command: 未知子命令", H.parse_command("瞎写", []).error != "")

    # ---------------- 权限 ----------------
    bot = await send("我要做 偷偷加一条", user_id=OTHER)
    check("非超管被拒", "超级用户" in bot.last(), bot.last())
    check("非超管没落盘", store.all_tasks() == [])
    bot = await send("/待办", user_id=OTHER)
    check("非超管 /待办 给拒绝提示", "超级用户" in bot.last(), str(bot.calls))
    bot = await send("随便聊两句", user_id=OTHER)
    check("非超管普通聊天不触发", bot.calls == [], str(bot.calls))

    # ---------------- 新增 ----------------
    bot = await send("我要做 给群管插件加个冷却")
    check("我要做 新增", store.get_task(1) is not None and store.get_task(1)["title"] == "给群管插件加个冷却", bot.last())
    check("新增有回执", "#1" in bot.last(), bot.last())

    bot = await send("!!我要做 修复 /烟 的冷却 #群管")
    t2 = store.get_task(2)
    check("我要做 带优先级和标签", t2 is not None and t2["priority"] == 2 and t2["tags"] == ["群管"], str(t2))

    bot = await send("!我要做 紧急上线")
    check("! 紧急优先级", store.get_task(3)["priority"] == 1, str(store.get_task(3)))

    bot = await send("~我要做 顺手看看文档")
    check("~ 低优先级", store.get_task(4)["priority"] == 4, str(store.get_task(4)))

    bot = await send("我要做 写周报 #周报")
    check("#标签无优先级", store.get_task(5)["tags"] == ["周报"] and store.get_task(5)["priority"] == 3)

    bot = await send("/我要做")
    check("只有「我要做」给提示", "要做啥" in bot.last(), bot.last())
    bot = await send("我要做")
    check("裸「我要做」不吞消息", bot.calls == [], str(bot.calls))
    before = len(store.all_tasks())
    bot = await send("我要做点作业不然要挨骂了")
    check("裸「我要做点…」当聊天不记", len(store.all_tasks()) == before, bot.last())
    bot = await send("/我要做 明确带前缀的")
    check("/我要做 显式前缀可记", "记下了" in bot.last(), bot.last())
    await send(f"/待办 完成 {store.all_tasks()[-1]['id']}")
    await send("/待办 删除 结束的全部")

    # ---------------- 列表 ----------------
    bot = await send("/待办")
    text = bot.last()
    check(
        "/待办 按优先级排序",
        text.index("#3 紧急上线") < text.index("#2 修复") < text.index("#5 写周报") < text.index("#4 顺手"),
        text,
    )
    check("/待办 有统计", "待办 5" in text, text)

    # ---------------- 完成 ----------------
    bot = await send("/待办 完成 1")
    check("/待办 完成 1", store.get_task(1)["status"] == "done" and "已完成" in bot.last(), bot.last())
    bot = await send("/待办 完成 1")
    check("重复完成有提示", "之前就完成过了" in bot.last(), bot.last())

    bot = await send("我做完了 5")
    check("我做完了 5", store.get_task(5)["status"] == "done", bot.last())
    bot = await send("我做完了")
    check("我做完了 无编号不响应", bot.calls == [], str(bot.calls))

    bot = await send("/待办 完成 周报")
    check("按关键词完成（已完成的会再提示）", "之前就完成过了" in bot.last(), bot.last())

    bot = await send("/待办 完成 999")
    check("完成不存在编号", "没有 #999" in bot.last(), bot.last())

    # 关键词：唯一命中 -> 直接完成
    await store.set_status(2, "open", str(SUPER))
    bot = await send("/待办 完成 冷却")
    check("按关键词完成（唯一命中）", store.get_task(2)["status"] == "done", bot.last())

    # 关键词歧义：两条都含「冷却」
    await store.set_status(2, "open", str(SUPER))
    bot = await send("/待办 做 修 复 冷 却 的 另 一 处")
    check("字间空格的新标题被合并", "冷却的另一处" in store.all_tasks()[-1]["title"], store.all_tasks()[-1]["title"])
    bot = await send("/待办 完成 冷却")
    check("关键词多个命中要求用编号", "匹配到 2 条" in bot.last(), bot.last())
    await send(f"/待办 放弃 {store.all_tasks()[-1]['id']}")

    # ---------------- 放弃 / 删除 ----------------
    bot = await send("/待办 放弃 4")
    check("/待办 放弃 4", store.get_task(4)["status"] == "dropped", bot.last())
    active_ids = [t["id"] for t in store.active_tasks()]
    check("放弃后不在待办里", 4 not in active_ids, str(active_ids))

    bot = await send("/待办 删除 3")
    check("未结束不能删", "还没结束" in bot.last() and store.get_task(3)["status"] != "dropped", bot.last())
    bot = await send("/待办 删除 4")
    check("已放弃可以删", store.get_task(4)["status"] == "dropped", bot.last())

    bot = await send("/待办 删除 结束的全部")
    check("批量清理结束的", "清掉了" in bot.last(), bot.last())

    # ---------------- 修改 ----------------
    bot = await send("/待办 开始 2")
    check("/待办 开始 2", store.get_task(2)["status"] == "doing", bot.last())
    bot = await send("/待办 改 2 修复 /烟 的冷却（已改）")
    check("/待办 改", store.get_task(2)["title"] == "修复 /烟的冷却（已改）", bot.last())
    bot = await send("/待办 优先级 2 紧急")
    check("/待办 优先级", store.get_task(2)["priority"] == 1, bot.last())
    bot = await send("/待办 优先级 2 瞎写")
    check("优先级非法给提示", "优先级写" in bot.last(), bot.last())
    bot = await send("/待办 备注 2 记得先看 pigverse 的写法")
    check("/待办 备注", "pigverse" in normalize(store.get_task(2)["note"]), bot.last())
    bot = await send("/待办 标签 2 #群管 #bug")
    check("/待办 标签", store.get_task(2)["tags"] == ["群管", "bug"], bot.last())

    # ---------------- 提醒 ----------------
    bot = await send("/待办 提醒 2 3天后")
    t2 = store.get_task(2)
    check("/待办 提醒 编号 时间", t2["remind_at"] and not t2["reminded"], str(t2.get("remind_at")))
    check("提醒回执含日期", "将在" in bot.last(), bot.last())

    bot = await send("/待办 提醒 2 昨天")
    check("提醒：过去时间", "过去了" in bot.last() or "时间看不懂" in bot.last(), bot.last())

    # 到点提醒：把 remind_at 改到过去
    await store.update_task(2, str(SUPER), remind_at=int(time.time()) - 60, reminded=False)
    bot = FakeBot()
    driver._bots["1"] = bot  # 让 _pick_bot 拿到 FakeBot
    await plugin.module._remind_tick()
    private_calls = [d for a, d in bot.calls if a == "send_private_msg"]
    check("到点提醒私聊", bool(private_calls) and private_calls[0]["user_id"] == SUPER, str(bot.calls))
    check("提醒后标记已提醒", store.get_task(2)["reminded"] is True)
    bot = FakeBot()
    driver._bots["1"] = bot
    await plugin.module._remind_tick()
    check("已提醒不会重复发", not [d for a, d in bot.calls if a == "send_private_msg"], str(bot.calls))

    # ---------------- 导出（DSH 联动） ----------------
    out_dir = tmp / "dsh"
    cfg.qqbot_todo_dsh_dir = str(out_dir)
    bot = await send("/待办 交接 本周要做的事")
    check("/待办 交接 设置标题", "本周要做的事" in normalize(bot.last()), bot.last())
    bot = await send("/待办 导出")
    files = list(out_dir.glob("todo_handoff.*"))
    check("导出写文件", len(files) == 1 and files[0].suffix == ".md", str(files))
    body = files[0].read_text(encoding="utf-8") if files else ""
    check("导出文件带标题", body.startswith("# 本周要做的事"), body[:80])
    check("导出文件是勾选清单", "- [ ] #2" in normalize(body) and "[ ] #3" in body, normalize(body)[:400])
    check("默认只导未完成的", "- [x]" not in body, body[:400])
    created = await store.add_task("已经做完的一条", by=str(SUPER))
    await store.set_status(created["id"], "done", str(SUPER))
    bot = await send("/待办 导出 全部")
    body_all = (out_dir / "todo_handoff.md").read_text(encoding="utf-8")
    check("导出全部含已完成 [x]", f"- [x] #{created['id']}" in normalize(body_all), normalize(body_all)[-400:])
    check("导出也把全文发给超管", "# 本周要做的事" in bot.last(), bot.last()[:200])
    check("导出快照落盘", store.export_file().exists())

    bot = await send("/待办 导出 json")
    jsons = list(out_dir.glob("todo_handoff.json"))
    check("导出 json 写文件", bool(jsons), str(list(out_dir.iterdir())))
    data = json.loads(jsons[0].read_text(encoding="utf-8")) if jsons else {}
    check("json 结构", data.get("source") == "qqbot_todo" and isinstance(data.get("tasks"), list), str(data)[:200])

    cfg.qqbot_todo_dsh_dir = ""

    # ---------------- 「我 要做」拆开两条（QQ 输入法） ----------------
    # 单发「我」不触发
    bot = await send("我")
    check("单发「我」不触发", bot.calls == [], str(bot.calls))
    # 「我 / 要做 任务」：两条分开发，第二条单独也能记（"要做 X" 本身就是常见说法）
    bot = await send("要做 把 pigverse 的 README 补一下")
    check("「要做 X」可单独录入", "记下了" in bot.last(), bot.last())

    # ---------------- 群聊 ----------------
    bot = await send("/待办", group=True)
    check("群默认关闭：/待办 不响应", bot.calls == [], str(bot.calls))
    bot = await send("我要做 群里试试", group=True)
    check("群默认关闭：我要做 不响应", bot.calls == [], str(bot.calls))
    cfg.qqbot_todo_group_enabled = True
    bot = await send("我要做 群里试试", group=True)
    check("群开关打开后可用", "记下了" in bot.last(), bot.last())
    src = store.active_tasks()
    check("群来源记录了 group_id", any(t["source"].get("group_id") == GROUP for t in src), str(src[-1].get("source")))
    bot = await send("我要做 非超管乱来", user_id=OTHER, group=True)
    check("群里非超管仍被拒并提示", "超级用户" in bot.last(), str(bot.calls))
    cfg.qqbot_todo_group_enabled = False

    # ---------------- 每日推送 ----------------
    bot = FakeBot()
    driver._bots["1"] = bot
    cfg.qqbot_todo_daily_enabled = False
    await plugin.module._daily_push()
    check("每日推送默认关闭", bot.calls == [], str(bot.calls))
    cfg.qqbot_todo_daily_enabled = True
    await plugin.module._daily_push()
    check("每日推送发给超管", any(a == "send_private_msg" for a, _ in bot.calls), str(bot.calls))

    import nonebot_plugin_apscheduler as _ap
    check("定时任务已注册", _ap.scheduler.get_job("qqbot_todo_remind") is not None and _ap.scheduler.get_job("qqbot_todo_daily") is not None)

    # ---------------- 健壮性 ----------------
    store.tasks_file().write_text("{坏 json", encoding="utf-8")
    store.reset_cache()
    check("坏文件不崩", store.all_tasks() == [])
    store.bootstrap()

    check("最大长度截断", len(H.parse_quick("长" * 500)["title"]) <= H.TITLE_MAX)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        check("测试无异常", False, "见堆栈")
    print("\n" + "=" * 60)
    failed = [n for n, ok, _ in results if not ok]
    print(f"总计 {len(results)} 项，失败 {len(failed)} 项")
    for n in failed:
        print(f"  - {n}")
    print("RESULT:", "PASS" if not failed else "FAIL")
    sys.exit(0 if not failed else 1)
