#!/usr/bin/env python3
"""qqbot_group 群聊管理插件测试套件。

三层测试：
  1. 加载测试：nonebot.init + OneBot v11 适配器 + load_plugin
  2. 单元测试：helpers.py 纯函数（时长解析/格式化/目标解析）
  3. 端到端测试：FakeBot 模拟 OneBot API，用 nonebot.message.handle_event
     跑真实 matcher，验证每条命令的行为与拒绝路径

用法（工作区 venv，cwd 必须是本目录）：
    .venv/bin/python test_admin.py
"""

import asyncio
import os
from datetime import datetime
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import nonebot

# 与线上 .env.prod 保持一致：COMMAND_START=["/", ""]
os.environ.setdefault("COMMAND_START", '["/", ""]')
nonebot.init()

from nonebot.adapters.onebot.v11 import (
    ActionFailed,
    Adapter as OneBotAdapter,
    Bot as OneBotBot,
    GroupMessageEvent,
    PrivateMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.adapters.onebot.v11.event import Reply, Sender
from nonebot.message import handle_event

# 注册 OneBot v11 适配器（插件声明了 supported_adapters，必须注册才能通过 matcher 检查）
_DRIVER = nonebot.get_driver()
_DRIVER.register_adapter(OneBotAdapter)
_ADAPTER = next(iter(_DRIVER._adapters.values()))

# ---------------------------------------------------------------- 常量
SELF_ID = "3194243533"
GROUP = 999888
ADMIN = 10001
TARGET = 10002
OWNER = 10003

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  {'[PASS]' if cond else '[FAIL]'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------- 加载
print("== 1. 插件加载 ==")
plugin = nonebot.load_plugin(Path("plugins/qqbot_group"))
check("插件加载成功", plugin is not None)
check(
    "注册了 14 个响应器",
    plugin is not None and len(plugin.matcher) == 14,
    f"实际 {len(plugin.matcher) if plugin else 0}",
)
group_mod = plugin.module  # 加载后的模块实例（用于改配置做权限测试）
_features_tmp = Path(tempfile.mkdtemp(prefix="qqbot-features-"))
group_mod.features.DATA_DIR = _features_tmp
for _key in (
    "repeat", "poke", "quotes", "meme", "box",
    "selfmute", "greeting", "work",
):
    group_mod.features.set_enabled(GROUP, _key, True)

# ---------------------------------------------------------------- 命令名 vs 功能名
print("\n== 1.5 拿命令名去 /开启 的提示 ==")
_f = group_mod.features
# 只用本插件自己的命令（测试环境只加载了 qqbot_group）
check("识别命令 本群功能", _f._is_registered_command("本群功能"))
check("识别别名 群功能", _f._is_registered_command("群功能"))
check("带斜杠也能识别", _f._is_registered_command("/本群功能"))
check("功能名不算命令", not _f._is_registered_command("复读"))
check("乱写的词不算命令", not _f._is_registered_command("不存在的功能xyz"))


# ---------------------------------------------------------------- FakeBot
class FakeBot(OneBotBot):
    """拦截 call_api 的假 Bot，记录调用并按 api 表返回结果/抛异常。"""

    def __init__(self) -> None:
        super().__init__(_ADAPTER, SELF_ID)
        self.calls: list[tuple[str, dict]] = []
        self.api: dict = {}

    async def call_api(self, api: str, **data):  # type: ignore[override]
        self.calls.append((api, data))
        if api in self.api:
            r = self.api[api]
            if isinstance(r, Exception):
                raise r
            return r
        return {"message_id": 1}


def make_event(msg, role: str = "admin", user_id: int = ADMIN, reply_target: int | None = None) -> GroupMessageEvent:
    if isinstance(msg, str):
        msg = Message(msg)
    event = GroupMessageEvent(
        time=1700000000,
        self_id=SELF_ID,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        group_id=GROUP,
        message=msg,
        original_message=msg,
        message_type="group",
        raw_message=str(msg),
        font=1,
        message_id=12345,
        sender=Sender(user_id=user_id, nickname="测试", card="测试", role=role),
    )
    if reply_target is not None:
        event.reply = Reply(
            time=1699999999,
            message_type="group",
            message_id=999,
            real_id=999,
            sender=Sender(user_id=reply_target, nickname="被回复的人"),
            message=Message("之前的内容"),
        )
    return event


def api_call(bot: FakeBot, name: str) -> dict | None:
    for api, data in bot.calls:
        if api == name:
            return data
    return None


def reply_text(bot: FakeBot) -> str:
    for api, data in bot.calls:
        if api == "send_msg":
            msg = data.get("message")
            return msg.extract_plain_text() if isinstance(msg, Message) else str(msg)
    return ""


async def run(bot: FakeBot, event: GroupMessageEvent) -> None:
    await handle_event(bot, event)


def new_bot(bot_role: str = "admin") -> FakeBot:
    bot = FakeBot()
    bot.api["get_group_member_info"] = {"role": bot_role}
    return bot


def smoke_msg(target: int | None = TARGET) -> Message:
    segs = [MessageSegment.text("/烟 ")]
    if target is not None:
        segs.append(MessageSegment.at(target))
    return Message(segs)


# ---------------------------------------------------------------- 单元测试
print("== 2. helpers 单元测试 ==")
import importlib.util  # noqa: E402


def _load_module(name: str, path: Path):
    """按文件路径加载模块，避免走包导入而二次执行插件 __init__。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_helpers = _load_module("qqbot_group_helpers", ROOT / "plugins/qqbot_group/helpers.py")
_config_mod = _load_module("qqbot_group_config", ROOT / "plugins/qqbot_group/config.py")
extract_target = _helpers.extract_target
format_duration = _helpers.format_duration
parse_duration = _helpers.parse_duration
random_duration = _helpers.random_duration
Config = _config_mod.Config

check("format_duration(45)", format_duration(45) == "45秒", format_duration(45))
check("format_duration(90)", format_duration(90) == "1分钟30秒", format_duration(90))
check("format_duration(600)", format_duration(600) == "10分钟", format_duration(600))
check("format_duration(3600)", format_duration(3600) == "1小时", format_duration(3600))
check("format_duration(26423)", format_duration(26423) == "7小时20分钟23秒", format_duration(26423))
check("format_duration(86465)", format_duration(86465) == "1天1分钟5秒", format_duration(86465))
check("format_duration(0)", format_duration(0) == "0秒", format_duration(0))

check("parse_duration('45')", parse_duration("45") == 45)
check("parse_duration('30m')", parse_duration("30m") == 1800)
check("parse_duration('2小时')", parse_duration("2小时") == 7200)
check("parse_duration('1d')", parse_duration("1d") == 86400)
check("parse_duration('1.5h')", parse_duration("1.5h") == 5400)
check("parse_duration('30分钟')", parse_duration("30分钟") == 1800)
check("parse_duration('abc') is None", parse_duration("abc") is None)
check("parse_duration('30x') is None", parse_duration("30x") is None)
check("parse_duration('') is None", parse_duration("") is None)
check("parse_duration 超长裁剪到 30 天", parse_duration("999d") == 30 * 86400)

# 目标解析
ev = make_event(Message([MessageSegment.text("/烟 "), MessageSegment.at(TARGET)]))
check("extract_target: @ 目标", extract_target(ev, Message(), SELF_ID) == TARGET)
ev = make_event(Message([MessageSegment.text("/烟 "), MessageSegment.at(SELF_ID), MessageSegment.at(TARGET)]))
check("extract_target: 跳过 @机器人 自己", extract_target(ev, Message(), SELF_ID) == TARGET)
ev = make_event(Message([MessageSegment.text("/烟 "), MessageSegment.at("all")]))
check("extract_target: 忽略 @全体成员", extract_target(ev, Message(), SELF_ID) is None)
ev = make_event("/烟", reply_target=TARGET)
check("extract_target: 回复消息优先", extract_target(ev, Message(), SELF_ID) == TARGET)
ev = make_event(Message("/烟 123456"))
check("extract_target: 纯文本 QQ 号", extract_target(ev, Message("123456"), SELF_ID) == 123456)
ev = make_event(Message("/禁言 123456 30m"))
check("extract_target: QQ号+时长取第一段", extract_target(ev, Message("123456 30m"), SELF_ID) == 123456)

d = random_duration(60, 28800)
check("random_duration 在区间内", 60 <= d <= 28800, str(d))

cfg = Config()
check("Config 默认值", cfg.qqbot_group_smoke_max_seconds == 28800 and cfg.qqbot_group_smoke_min_seconds == 60)
cfg2 = Config(qqbot_group_extra_admins="123, 456")
check("extra_admins 字符串解析", cfg2.qqbot_group_extra_admins == {123, 456}, str(cfg2.qqbot_group_extra_admins))
try:
    Config(qqbot_group_smoke_min_seconds=100, qqbot_group_smoke_max_seconds=10)
    check("min>max 报错", False, "未抛异常")
except Exception:
    check("min>max 报错", True)

# ---------------------------------------------------------------- 端到端
print("== 3. 端到端（FakeBot + handle_event） ==")


async def e2e() -> None:
    # --- 核心：/烟 @用户 ---
    bot = new_bot("admin")
    await run(bot, make_event(smoke_msg(), role="admin"))
    ban = api_call(bot, "set_group_ban")
    check("/烟: 调用了 set_group_ban", ban is not None)
    if ban:
        check("/烟: 目标正确", ban.get("user_id") == TARGET, str(ban))
        check(
            "/烟: 随机时长在 8 小时内",
            60 <= ban.get("duration", 0) <= 28800,
            str(ban.get("duration")),
        )
    sent = next(
        (data["message"] for api, data in bot.calls
         if api == "send_msg" and isinstance(data.get("message"), Message)),
        None,
    )
    check(
        "/烟: 播报里 @ 了目标",
        sent is not None
        and any(s.type == "at" and s.data.get("qq") == str(TARGET) for s in sent),
    )

    # --- 核心：bot 不是管理员 -> 拒绝 ---
    bot = new_bot("member")
    await run(bot, make_event(smoke_msg(), role="admin"))
    check("/烟: bot 无管理员权限时拒绝", "还不是管理员" in reply_text(bot), reply_text(bot))
    check("/烟: 拒绝时不调用 set_group_ban", api_call(bot, "set_group_ban") is None)

    # --- 发送者不是管理员 -> 拒绝 ---
    bot = new_bot("admin")
    await run(bot, make_event(smoke_msg(), role="member"))
    check("/烟: 普通成员被拒绝", "只有群主" in reply_text(bot), reply_text(bot))
    check("/烟: 拒绝时不调用 set_group_ban", api_call(bot, "set_group_ban") is None)

    # --- 群主/超管也可用 ---
    bot = new_bot("admin")
    await run(bot, make_event(smoke_msg(), role="owner", user_id=OWNER))
    check("/烟: 群主可用", api_call(bot, "set_group_ban") is not None)

    # --- 回复目标 ---
    bot = new_bot("admin")
    await run(bot, make_event("/烟", role="admin", reply_target=TARGET))
    ban = api_call(bot, "set_group_ban")
    check("/烟: 回复消息定位目标", ban is not None and ban.get("user_id") == TARGET)

    # --- 别名 ---
    bot = new_bot("admin")
    await run(bot, make_event(Message(["/抽烟 ", MessageSegment.at(TARGET)]), role="admin"))
    check("/抽烟 别名生效", api_call(bot, "set_group_ban") is not None)

    # --- 无目标 ---
    bot = new_bot("admin")
    await run(bot, make_event("/烟", role="admin"))
    check("/烟: 无目标给用法提示", "用法" in reply_text(bot), reply_text(bot))

    # --- API 失败（禁言群主/其他管理） ---
    bot = new_bot("admin")
    bot.api["set_group_ban"] = ActionFailed(retcode=100, msg="群主不能被禁言")
    await run(bot, make_event(smoke_msg(), role="admin"))
    check("/烟: ActionFailed 友好提示", "禁言失败" in reply_text(bot), reply_text(bot))

    # --- /禁言 ---
    bot = new_bot("admin")
    await run(bot, make_event(Message(["/禁言 ", MessageSegment.at(TARGET), " 30m"]), role="admin"))
    ban = api_call(bot, "set_group_ban")
    check("/禁言: 30m -> 1800 秒", ban is not None and ban.get("duration") == 1800, str(ban))

    bot = new_bot("admin")
    await run(bot, make_event(Message(["/禁言 ", MessageSegment.at(TARGET)]), role="admin"))
    ban = api_call(bot, "set_group_ban")
    check("/禁言: 默认 600 秒", ban is not None and ban.get("duration") == 600, str(ban))

    bot = new_bot("admin")
    await run(bot, make_event(Message(["/禁言 ", MessageSegment.at(TARGET), " abc"]), role="admin"))
    check("/禁言: 非法时长提示", "看不懂时长" in reply_text(bot), reply_text(bot))

    bot = new_bot("admin")
    await run(bot, make_event("/禁言 123456 30m", role="admin"))
    ban = api_call(bot, "set_group_ban")
    check(
        "/禁言: QQ号+时长写法",
        ban is not None and ban.get("user_id") == 123456 and ban.get("duration") == 1800,
        str(ban),
    )

    # --- /解禁 ---
    bot = new_bot("admin")
    await run(bot, make_event(Message(["/解禁 ", MessageSegment.at(TARGET)]), role="admin"))
    ban = api_call(bot, "set_group_ban")
    check("/解禁: duration=0", ban is not None and ban.get("duration") == 0, str(ban))

    # --- /踢 ---
    bot = new_bot("admin")
    await run(bot, make_event(Message(["/踢 ", MessageSegment.at(TARGET)]), role="admin"))
    kick = api_call(bot, "set_group_kick")
    check("/踢: 调用了 set_group_kick", kick is not None and kick.get("user_id") == TARGET)

    # /踢 可按群关闭
    feat_kick = group_mod.features
    check("踢人: 未配置的群默认开启", feat_kick.is_enabled(GROUP, "kick") is True)
    feat_kick.set_enabled(GROUP, "kick", False)
    bot = new_bot("admin")
    await run(bot, make_event(Message(["/踢 ", MessageSegment.at(TARGET)]), role="admin"))
    check("踢人: 关闭后不调用踢人", api_call(bot, "set_group_kick") is None)
    check("踢人: 关闭后有提示", "关闭" in reply_text(bot), reply_text(bot))
    feat_kick.set_enabled(GROUP, "kick", True)

    # --- /全体禁言 + /解除全体 ---
    bot = new_bot("admin")
    await run(bot, make_event("/全体禁言", role="admin"))
    wa = api_call(bot, "set_group_whole_ban")
    check("/全体禁言: enable=True", wa is not None and wa.get("enable") is True, str(wa))

    bot = new_bot("admin")
    await run(bot, make_event("/解除全体", role="admin"))
    wa = api_call(bot, "set_group_whole_ban")
    check("/解除全体: enable=False", wa is not None and wa.get("enable") is False, str(wa))

    # --- 额外管理员（改加载后模块的配置） ---
    group_mod.config.plugin_config.qqbot_group_extra_admins = {10001}
    bot = new_bot("admin")
    await run(bot, make_event(smoke_msg(), role="member"))
    check("extra_admins 生效", api_call(bot, "set_group_ban") is not None)
    group_mod.config.plugin_config.qqbot_group_extra_admins = set()

    # --- /撤：撤回机器人上一条消息 ---
    group_mod.permissions._last_sent.clear()
    await group_mod.permissions._record_last_sent(
        None, None, "send_msg", {"group_id": GROUP}, {"message_id": 777}
    )
    check("记录钩子: 记下 message_id", group_mod.permissions._last_sent.get(GROUP) == 777)

    bot = new_bot("admin")
    await run(bot, make_event("/撤", role="admin"))
    dm = api_call(bot, "delete_msg")
    check("/撤: 调用 delete_msg", dm is not None and dm.get("message_id") == 777, str(dm))
    check(
        "/撤: 撤回后不回复",
        not any(a == "send_msg" for a, _ in bot.calls),
        str([a for a, _ in bot.calls]),
    )

    bot = new_bot("admin")
    await run(bot, make_event("/撤", role="admin"))
    check(
        "/撤: 已撤回后再发提示没有",
        "没有可撤回" in reply_text(bot) and api_call(bot, "delete_msg") is None,
        reply_text(bot),
    )

    # 非管理员拒绝
    group_mod.permissions._last_sent.clear()
    await group_mod.permissions._record_last_sent(
        None, None, "send_msg", {"group_id": GROUP}, {"message_id": 778}
    )
    bot = new_bot("admin")
    await run(bot, make_event("/撤", role="member"))
    check("/撤: 普通成员被拒绝", "只有群主" in reply_text(bot), reply_text(bot))
    check("/撤: 拒绝时不调用 delete_msg", api_call(bot, "delete_msg") is None)

    # 没有可撤回的消息
    group_mod.permissions._last_sent.clear()
    bot = new_bot("admin")
    await run(bot, make_event("/撤", role="admin"))
    check("/撤: 无消息提示", "没有可撤回" in reply_text(bot), reply_text(bot))

    # 撤回失败（超过 2 分钟等）
    group_mod.permissions._last_sent.clear()
    await group_mod.permissions._record_last_sent(
        None, None, "send_msg", {"group_id": GROUP}, {"message_id": 779}
    )
    bot = new_bot("admin")
    bot.api["delete_msg"] = ActionFailed(retcode=100, msg="消息已超过撤回时限")
    await run(bot, make_event("/撤", role="admin"))
    check("/撤: ActionFailed 友好提示", "撤回失败" in reply_text(bot), reply_text(bot))

    # 裸「撤」不触发（必须带 / 前缀）
    group_mod.permissions._last_sent.clear()
    await group_mod.permissions._record_last_sent(
        None, None, "send_msg", {"group_id": GROUP}, {"message_id": 780}
    )
    bot = new_bot("admin")
    await run(bot, make_event("撤了撤了", role="admin"))
    check("/撤: 裸「撤」不触发", api_call(bot, "delete_msg") is None)
    group_mod.permissions._last_sent.clear()

    # @机器人 触发（适配器会剥掉 @ 段并置 to_me）
    group_mod.permissions._last_sent.clear()
    await group_mod.permissions._record_last_sent(
        None, None, "send_msg", {"group_id": GROUP}, {"message_id": 781}
    )
    bot = new_bot("admin")
    await bot.handle_event(
        make_event(Message([MessageSegment.at(SELF_ID), " 撤"]), role="admin")
    )
    dm = api_call(bot, "delete_msg")
    check("/撤: @机器人 触发", dm is not None and dm.get("message_id") == 781, str(dm))
    group_mod.permissions._last_sent.clear()

    # --- 复读已拆到独立插件 qqbot_repeat ---
    check("复读已从 qqbot_group 拆出", not hasattr(group_mod, "repeat"))

    # --- 按群功能开关 ---
    feat = group_mod.features

    bot = new_bot("admin")
    await run(bot, make_event("/本群功能", role="admin"))
    text = reply_text(bot)
    check("/本群功能 列出功能", "表情包" in text and "金句" in text, text)
    check("/本群功能 管理员看到开关提示", "/开启" in text, text)

    # 普通成员也能查看（只读）
    bot = new_bot("admin")
    await run(bot, make_event("/本群功能", role="member"))
    text = reply_text(bot)
    check(
        "/本群功能 普通成员可查看",
        "表情包" in text and "只能查看" in text,
        text,
    )

    bot = new_bot("admin")
    await run(bot, make_event("关闭本群表情包", role="admin"))
    check("关闭本群表情包", "已关闭" in reply_text(bot), reply_text(bot))
    check("表情包开关状态", feat.is_enabled(GROUP, "meme") is False)

    bot = new_bot("admin")
    await run(bot, make_event("/开启 表情包", role="admin"))
    check("开启表情包", "已开启" in reply_text(bot), reply_text(bot))
    check("表情包开关恢复", feat.is_enabled(GROUP, "meme") is True)

    bot = new_bot("admin")
    await run(bot, make_event("关闭本群表情包", role="member"))
    check("非管理员不能开关", "只有群主" in reply_text(bot), reply_text(bot))

    bot = new_bot("admin")
    await run(bot, make_event("关闭本群电脑", role="admin"))
    # 自然语言「关闭本群X」不是命令，仍然不响应（避免日常聊天误触）
    check("自然语言未知功能不响应", reply_text(bot) == "", reply_text(bot))

    bot = new_bot("admin")
    await run(bot, make_event("/关闭 不存在的功能", role="admin"))
    check("命令式未知功能给提示", "没找到功能" in reply_text(bot), reply_text(bot))

    bot = new_bot("admin")
    await run(bot, make_event("/关闭 表情包", role="admin"))
    check("/关闭 表情包", "已关闭" in reply_text(bot), reply_text(bot))
    check("表情包开关状态", feat.is_enabled(GROUP, "meme") is False)

    # --- /盒 ---
    bot = new_bot("admin")
    await run(
        bot,
        make_event(
            Message([MessageSegment.text("/盒 "), MessageSegment.at(TARGET)]),
            role="member",
        ),
    )
    check("/盒 返回 QQ 号", str(TARGET) in reply_text(bot), reply_text(bot))

    bot = new_bot("admin")
    await run(bot, make_event("/盒", role="member"))
    check("/盒 无目标提示", "用法" in reply_text(bot), reply_text(bot))

    bot = new_bot("admin")
    await bot.handle_event(
        make_event(
            Message([MessageSegment.at(SELF_ID), " 盒 ", MessageSegment.at(TARGET)]),
            role="member",
        )
    )
    check("/盒 @机器人触发", str(TARGET) in reply_text(bot), reply_text(bot))

    bot = new_bot("admin")
    await run(bot, make_event("盒马鲜生", role="member"))
    check("/盒 裸词不触发", reply_text(bot) == "", reply_text(bot))

    feat.set_enabled(GROUP, "box", False)
    bot = new_bot("admin")
    await run(
        bot,
        make_event(
            Message([MessageSegment.text("/盒 "), MessageSegment.at(TARGET)]),
            role="member",
        ),
    )
    check("/盒 功能可关闭", "关闭" in reply_text(bot), reply_text(bot))
    feat.set_enabled(GROUP, "box", True)

    # 资产聚合：注册一个测试来源
    async def fake_assets(uid: str):
        return [f"测试资产：{uid}"]

    group_mod.assets.register_asset_provider("test_assets", fake_assets)
    bot = new_bot("admin")
    await run(
        bot,
        make_event(
            Message([MessageSegment.text("/盒 "), MessageSegment.at(TARGET)]),
            role="member",
        ),
    )
    text = reply_text(bot)
    check(
        "/盒 聚合资产",
        "测试资产：10002" in text and "资产" in text,
        text,
    )

    # --- 禁言套餐 / 精致睡眠 ---
    bot = new_bot("admin")
    await run(bot, make_event("精致睡眠", role="member"))
    ban = api_call(bot, "set_group_ban")
    check("精致睡眠: 禁言自己", ban is not None and ban.get("user_id") == ADMIN, str(ban))
    check(
        "精致睡眠: 6~12 小时",
        ban is not None and 6 * 3600 <= ban.get("duration", 0) <= 12 * 3600,
        str(ban),
    )

    bot = new_bot("admin")
    await run(bot, make_event("来份禁言套餐", role="member"))
    ban = api_call(bot, "set_group_ban")
    check(
        "禁言套餐: 1~60 分钟",
        ban is not None and 60 <= ban.get("duration", 0) <= 3600,
        str(ban),
    )

    # 管理员指定别人
    bot = new_bot("admin")
    await run(
        bot,
        make_event(
            Message([MessageSegment.text("给 "), MessageSegment.at(TARGET), " 来份精致睡眠"]),
            role="admin",
        ),
    )
    ban = api_call(bot, "set_group_ban")
    check("精致睡眠: 管理员指定他人", ban is not None and ban.get("user_id") == TARGET, str(ban))

    # 普通成员不能指定别人
    bot = new_bot("admin")
    await run(
        bot,
        make_event(
            Message([MessageSegment.text("给 "), MessageSegment.at(TARGET), " 来份精致睡眠"]),
            role="member",
        ),
    )
    check("精致睡眠: 普通成员不能指定他人", "只有群主" in reply_text(bot), reply_text(bot))
    check("精致睡眠: 拒绝时不调用禁言", api_call(bot, "set_group_ban") is None)

    # bot 不是管理员
    bot = new_bot("member")
    await run(bot, make_event("精致睡眠", role="member"))
    check("精致睡眠: bot 非管理员拒绝", "还不是管理员" in reply_text(bot), reply_text(bot))
    check("精致睡眠: 拒绝时不调用禁言", api_call(bot, "set_group_ban") is None)

    # 更多关键词套餐
    for keyword, lo, hi in (
        ("精致下午茶套餐", 3600, 3 * 3600),
        ("午睡", 30 * 60, 90 * 60),
        ("牢饭", 60, 60 * 60),
        ("豪华套餐", 2 * 3600, 6 * 3600),
        ("回笼觉", 6 * 3600, 12 * 3600),
    ):
        bot = new_bot("admin")
        await run(bot, make_event(keyword, role="member"))
        ban = api_call(bot, "set_group_ban")
        check(
            f"套餐关键词: {keyword}",
            ban is not None and lo <= ban.get("duration", 0) <= hi,
            str(ban),
        )

    # 极致睡眠：24~72 小时
    for keyword in ("极致睡眠", "来个大的", "冬眠"):
        bot = new_bot("admin")
        await run(bot, make_event(keyword, role="member"))
        ban = api_call(bot, "set_group_ban")
        check(
            f"极致睡眠: {keyword}",
            ban is not None and 24 * 3600 <= ban.get("duration", 0) <= 72 * 3600,
            str(ban),
        )

    bot = new_bot("admin")
    await run(bot, make_event("极致睡眠", role="member"))
    check("极致睡眠: 提示来真的", "来真的" in reply_text(bot), reply_text(bot))

    # 带对象写法：给张三来个极致睡眠（管理员）
    orig_call = FakeBot.call_api

    async def _call_with_members(self, api, **data):
        if api == "get_group_member_list":
            return [
                {"user_id": ADMIN, "card": "管理员", "nickname": "管理员"},
                {"user_id": TARGET, "card": "张三", "nickname": "张三儿"},
            ]
        return await orig_call(self, api, **data)

    FakeBot.call_api = _call_with_members
    try:
        bot = new_bot("admin")
        await run(bot, make_event("给张三来个极致睡眠", role="admin"))
        ban = api_call(bot, "set_group_ban")
        check(
            "带对象: 给张三来个极致睡眠",
            ban is not None and ban.get("user_id") == TARGET and 24 * 3600 <= ban.get("duration", 0),
            str(ban),
        )

        bot = new_bot("admin")
        await run(bot, make_event("给张三来个极致睡眠", role="member"))
        check("带对象: 普通成员不能指定别人", api_call(bot, "set_group_ban") is None)
    finally:
        FakeBot.call_api = orig_call

    # 日常聊天不误判（句子中间含关键词）
    bot = new_bot("admin")
    await run(bot, make_event("这个威力大，估计要坐牢", role="member"))
    check("禁言套餐: 句子中间不触发", api_call(bot, "set_group_ban") is None)

    bot = new_bot("admin")
    await run(bot, make_event("我自闭了", role="member"))
    check("禁言套餐: 我自闭了不触发", api_call(bot, "set_group_ban") is None)

    # 明确句式仍可触发
    bot = new_bot("admin")
    await run(bot, make_event("来份坐牢", role="member"))
    check("禁言套餐: 来份坐牢触发", api_call(bot, "set_group_ban") is not None)

    # 功能开关
    feat.set_enabled(GROUP, "selfmute", False)
    bot = new_bot("admin")
    await run(bot, make_event("精致睡眠", role="member"))
    check("精致睡眠: 功能可关闭", api_call(bot, "set_group_ban") is None)
    feat.set_enabled(GROUP, "selfmute", True)

    # --- 早安 / 晚安 / 睡眠奖励 ---
    greet = group_mod.greeting
    base = datetime(2026, 1, 1, 12, 0).timestamp()
    rewards: list[tuple[str, int]] = []

    async def fake_reward(uid: str, amount: int) -> int:
        rewards.append((uid, amount))
        return amount

    greet._reward = fake_reward
    greet._now = lambda: base - 12 * 3600  # 00:00
    greet.state_file().write_text("{}", encoding="utf-8")

    bot = new_bot("admin")
    await run(bot, make_event("晚安", role="member"))
    check("晚安: 记录成功", "已记录" in reply_text(bot), reply_text(bot))
    check("晚安: 不禁言", api_call(bot, "set_group_ban") is None)

    greet._now = lambda: base - 4 * 3600  # 08:00，睡 8 小时
    bot = new_bot("admin")
    await run(bot, make_event("早安", role="member"))
    check("早安: 8 小时奖励 60", "60 月币" in reply_text(bot), reply_text(bot))
    check("早安: 调用发币", rewards and rewards[-1] == (str(ADMIN), 60), str(rewards))

    # 同一天第二次不再发（08:30 睡到 16:30，仍算当天）
    greet._now = lambda: base - 3.5 * 3600
    await run(new_bot("admin"), make_event("晚安", role="member"))
    greet._now = lambda: base + 4.5 * 3600
    bot = new_bot("admin")
    await run(bot, make_event("早安", role="member"))
    check("早安: 当天只发一次", "已经领过月币" in reply_text(bot), reply_text(bot))

    # 睡眠太短：不发
    greet._now = lambda: base
    greet.state_file().write_text("{}", encoding="utf-8")
    await run(new_bot("admin"), make_event("晚安", role="member"))
    greet._now = lambda: base + 2 * 3600
    bot = new_bot("admin")
    await run(bot, make_event("早安", role="member"))
    check("早安: 睡眠不足不发", "没有月币" in reply_text(bot), reply_text(bot))

    # 没有晚安记录
    greet.state_file().write_text("{}", encoding="utf-8")
    bot = new_bot("admin")
    await run(bot, make_event("早安", role="member"))
    check("早安: 无记录", "☀️ 早安～" in reply_text(bot), reply_text(bot))

    # 裸「早」触发，「早上的课」不触发
    bot = new_bot("admin")
    await run(bot, make_event("早", role="member"))
    check("早安: 裸「早」触发", reply_text(bot) != "", reply_text(bot))

    bot = new_bot("admin")
    await run(bot, make_event("早上的课", role="member"))
    check("早安: 日常词不触发", reply_text(bot) == "", reply_text(bot))

    # 功能开关
    feat.set_enabled(GROUP, "greeting", False)
    greet.state_file().write_text("{}", encoding="utf-8")
    bot = new_bot("admin")
    await run(bot, make_event("晚安", role="member"))
    check("早安/晚安: 功能可关闭", reply_text(bot) == "", reply_text(bot))
    feat.set_enabled(GROUP, "greeting", True)

    # --- 上班 / 下班 / 打卡 ---
    wk = group_mod.work
    conf = group_mod.config.plugin_config

    async def wk_fake_reward(uid: str, amount: int) -> int:
        rewards.append((uid, amount))
        return amount

    wk._reward = wk_fake_reward
    wk.state_file().write_text("{}", encoding="utf-8")
    wk._now = lambda: datetime(2026, 1, 7, 9, 0).timestamp()
    bot = new_bot("admin")
    await run(bot, make_event("上班", role="member"))
    check("上班: 打卡成功", "上班打卡成功" in reply_text(bot), reply_text(bot))

    wk._now = lambda: datetime(2026, 1, 7, 18, 30).timestamp()
    bot = new_bot("admin")
    await run(bot, make_event("下班", role="member"))
    text = reply_text(bot)
    check("下班: 工时 9 小时 30 分", "9小时30分钟" in text, text)
    check("下班: 8+1.5 倍加班费", "102 月币" in text, text)
    check("下班: 发币", rewards[-1] == (str(ADMIN), 102), str(rewards[-1]))

    # 周末 2 倍
    wk.state_file().write_text("{}", encoding="utf-8")
    wk._now = lambda: datetime(2026, 1, 10, 10, 0).timestamp()  # 周六
    await run(new_bot("admin"), make_event("上班", role="member"))
    wk._now = lambda: datetime(2026, 1, 10, 14, 0).timestamp()
    bot = new_bot("admin")
    await run(bot, make_event("下班", role="member"))
    check("下班: 周末 2 倍", "80 月币" in reply_text(bot), reply_text(bot))

    # 法定节假日 3 倍
    old_holidays = conf.qqbot_group_work_holidays
    conf.qqbot_group_work_holidays = "2026-01-01"
    wk.state_file().write_text("{}", encoding="utf-8")
    wk._now = lambda: datetime(2026, 1, 1, 10, 0).timestamp()
    await run(new_bot("admin"), make_event("上班", role="member"))
    wk._now = lambda: datetime(2026, 1, 1, 12, 0).timestamp()
    bot = new_bot("admin")
    await run(bot, make_event("下班", role="member"))
    check("下班: 法定节假日 3 倍", "60 月币" in reply_text(bot), reply_text(bot))
    conf.qqbot_group_work_holidays = old_holidays

    # 打卡 toggle + 不足最短工时
    wk.state_file().write_text("{}", encoding="utf-8")
    wk._now = lambda: datetime(2026, 1, 7, 9, 0).timestamp()
    await run(new_bot("admin"), make_event("打卡", role="member"))
    wk._now = lambda: datetime(2026, 1, 7, 9, 5).timestamp()
    bot = new_bot("admin")
    await run(bot, make_event("打卡", role="member"))
    check("打卡: 不足 10 分钟不计薪", "不算工资" in reply_text(bot), reply_text(bot))

    # 未上班就下班
    wk.state_file().write_text("{}", encoding="utf-8")
    bot = new_bot("admin")
    await run(bot, make_event("下班", role="member"))
    check("下班: 未打卡提示", "还没上班打卡" in reply_text(bot), reply_text(bot))

    # 功能开关
    feat.set_enabled(GROUP, "work", False)
    bot = new_bot("admin")
    await run(bot, make_event("上班", role="member"))
    check("上班: 功能可关闭", reply_text(bot) == "", reply_text(bot))
    feat.set_enabled(GROUP, "work", True)

    # 整活类功能默认关闭（未单独配置的群）
    check(
        "整活功能默认关闭",
        feat.is_enabled(123456, "box") is False
        and feat.is_enabled(123456, "selfmute") is False
        and feat.is_enabled(123456, "greeting") is False
        and feat.is_enabled(123456, "work") is False,
    )

    # --- 私聊不触发（构造私聊事件不应命中群管命令） ---
    ev = PrivateMessageEvent(
        time=1700000000, self_id=SELF_ID, post_type="message", sub_type="friend",
        user_id=ADMIN, message_type="private", message=smoke_msg(),
        original_message=smoke_msg(), raw_message="/烟 ", font=1, message_id=1,
        sender=Sender(user_id=ADMIN, nickname="测试"),
    )
    bot = new_bot("admin")
    await handle_event(bot, ev)
    check("私聊消息不触发群管命令", api_call(bot, "set_group_ban") is None)


def main() -> int:
    try:
        asyncio.run(e2e())
    except Exception:
        traceback.print_exc()
        check("端到端测试无异常", False, "见上方堆栈")
    print("\n" + "=" * 60)
    failed = [n for n, ok, _ in results if not ok]
    print(f"总计 {len(results)} 项，失败 {len(failed)} 项")
    if failed:
        for n in failed:
            print(f"  - {n}")
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
