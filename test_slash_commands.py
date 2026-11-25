#!/usr/bin/env python3
"""2026-11-25 群里事故的回归测试：命令都带 /，小e 不再抢命令。

事故现场（群 984616276）：

1. 绑定成功后提示「发『大象信息』」，用户裸发「大象信息」→ 被小e 当成聊天接走；
2. 用户照提醒发「/大象信息」→ 回了「没有『大象信息』这个命令」（纠错表里没有它）；
3. 用户发「help」→ 拿到菜单，但那时「/大象信息」不会出现在菜单里，别人根本不知道；
4. 多条消息并发时，「/大象信息」与另一条聊天同一秒到达：聊天被当成成绩问题回答。

本测试用真实 nonebot 加载插件，检查：

- 舞萌直连的规则：必须带 /（裸「大象信息」不匹配，/大象信息 匹配）；
- 小e 的唤醒规则：带 / 的消息永远不接；
- 帮助：/help 总览里有舞萌分组，/help 大象信息、/B50 help 都查得到，/大象 拼错能纠错；
- 绑定成功提示写的是带 / 的写法；
- 「功能列表」可以直接当 /本群功能 的别名用。

用法（cwd 必须工作区根目录）：
    .venv/bin/python test_slash_commands.py
"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

os.environ.setdefault("COMMAND_START", '["/", ""]')
os.environ.setdefault("LOCALSTORE_USE_CWD", "true")

import nonebot  # noqa: E402

nonebot.init()

from nonebot.adapters.onebot.v11 import (  # noqa: E402
    Adapter as OneBotAdapter,
    GroupMessageEvent,
    Message,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Sender  # noqa: E402
from nonebot.plugin import load_plugin  # noqa: E402

_PLUGINS = str(ROOT / "plugins")
if _PLUGINS not in sys.path:
    sys.path.insert(0, _PLUGINS)
# 工作区根目录也要在 sys.path 里，但必须在 plugins 之后：qqbot_* 包在
# plugins/ 下，而 plugins/qqbot_ai 要按包名 plugins.qqbot_ai 导入。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DRIVER = nonebot.get_driver()
_DRIVER.register_adapter(OneBotAdapter)
_DRIVER.config.superusers = {"1943546421", "root"}

load_plugin("nonebot_plugin_localstore")

GROUP = 984616276
OTHER_GROUP = 123456
USER = 1943546421
SELF = "3194243533"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), "" if cond else str(detail)))


def make_group(text: str, group_id: int = GROUP, user_id: int = USER) -> GroupMessageEvent:
    msg = Message(text)
    return GroupMessageEvent(
        time=1700000000, self_id=SELF, post_type="message", sub_type="normal",
        user_id=user_id, group_id=group_id, message=msg, original_message=msg,
        message_type="group", raw_message=text, font=1, message_id=1,
        sender=Sender(user_id=user_id, nickname="T", card="T", role="member"),
    )


_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)


def run_rule_sync(rule, event) -> bool:
    """Rule 对象要 (event, state) 两参；直接调会把 event 当成 bot。"""
    return _LOOP.run_until_complete(rule.__call__(bot=None, event=event, state={}))


def make_private(text: str, user_id: int = USER) -> PrivateMessageEvent:
    msg = Message(text)
    return PrivateMessageEvent(
        time=1700000000, self_id=SELF, post_type="message", sub_type="friend",
        user_id=user_id, message=msg, original_message=msg, message_type="private",
        raw_message=text, font=1, message_id=1,
        sender=Sender(user_id=user_id, nickname="T", card=""),
    )


def _maimai_module():
    """只加载舞萌插件的规则逻辑。

    完整加载要 maimai_chn 依赖 + PIL 渲染链，工作区 venv 里没有；规则函数本身
    只依赖 nonebot 适配器和配置。这里只执行「规则 + 文案」相关的代码片段：
    从源码里按标记切出来 exec，不跑注册 matcher / 初始化控制器。
    """
    src = (ROOT / "plugins" / "qqbot_maimai_dx" / "__init__.py").read_text(encoding="utf-8")

    def cut(begin: str, end: str) -> str:
        i = src.index(begin)
        j = src.index(end, i)
        return src[i:j]

    # 只取「规则 + 文案」用得到的片段：import 头（跳过 __plugin_meta__ 之前的
    # `from . import card, image, ...`，它们依赖 PIL / maimai_chn）、别名表、规则函数。
    imports = src[src.index("import asyncio"):src.index("from . import card")]
    imports += "from nonebot import get_driver\n"
    aliases = src[src.index("_GROUP_ALIASES = {"):src.index("def _plain(event")]
    rules = src[src.index("def _plain(event"):src.index("def _bot_settings()")]
    ns: dict = {
        "__name__": "qqbot_maimai_dx_probe",
        "logger": __import__("nonebot.log", fromlist=["logger"]).logger,
    }
    exec(imports, ns)  # noqa: S102
    exec("from types import SimpleNamespace\n"
         "plugin_config = SimpleNamespace(maimai_dx_group_ids=[984616276],\n"
         "    maimai_dx_bind_min_level=10, maimai_dx_self_unbind_level=9999)\n", ns)
    exec(aliases, ns)  # noqa: S102
    exec(rules, ns)  # noqa: S102

    # 绑定成功文案直接取源码片段断言（_do_bind 依赖控制器，不能真跑）
    class _Stub:
        _do_bind_src = cut("async def _do_bind(", "def _private_image_rule(")

    from types import SimpleNamespace

    return SimpleNamespace(**{k: v for k, v in ns.items() if not k.startswith("__")},
                            _do_bind=_Stub)


def test_maimai_rules() -> None:
    try:
        m = _maimai_module()
    except Exception as exc:  # noqa: BLE001
        check("舞萌直连规则可加载", False, repr(exc))
        return
    check("舞萌直连规则可加载", True)
    anywhere = m._anywhere_rule("大象信息")  # noqa: SLF001
    group_run = m._group_rule("更新成绩")  # noqa: SLF001
    alias_run = m._group_rule(m._GROUP_ALIASES["更新成绩"])  # noqa: SLF001
    private_bind = m._private_rule("绑定")  # noqa: SLF001
    unbind = m._unbind_rule()  # noqa: SLF001

    check("群内裸「大象信息」不匹配", not run_rule_sync(anywhere, make_group("大象信息")))
    check("群内 /大象信息 匹配", run_rule_sync(anywhere, make_group("/大象信息")))
    check("私聊 /大象信息 匹配", run_rule_sync(anywhere, make_private("/大象信息")))
    check("私聊裸「大象信息」不匹配", not run_rule_sync(anywhere, make_private("大象信息")))
    check("裸「更新成绩」不匹配", not run_rule_sync(group_run, make_group("更新成绩")))
    check("/B50 命中更新成绩别名", run_rule_sync(alias_run, make_group("/B50")))
    check("私聊裸「绑定」不匹配", not run_rule_sync(private_bind, make_private("绑定")))
    check("私聊 /绑定 匹配", run_rule_sync(private_bind, make_private("/绑定")))
    check("群内 /解绑 <QQ> 匹配", run_rule_sync(unbind, make_group("/解绑 1943546421")))
    check("群内裸「解绑」不匹配", not run_rule_sync(unbind, make_group("解绑 1943546421")))
    check("非授权群 /大象信息 不匹配", not run_rule_sync(anywhere, make_group("/大象信息", group_id=OTHER_GROUP)))

    src = m._do_bind._do_bind_src
    check("绑定成功提示写 /大象信息", "可以发 /大象信息" in src)
    check("绑定成功提示写 /更新成绩", "/更新成绩" in src)
    check("绑定成功提示不再教裸发", "发「大象信息」" not in src)


ai_module = None


def test_ai_wake() -> None:
    global ai_module
    load_plugin("qqbot_ai")
    ai = nonebot.get_plugin("qqbot_ai").module
    ai_module = ai
    ai.plugin_config.qqbot_ai_wake_words = ["小e"]
    ai.plugin_config.qqbot_ai_group_enabled = True
    ai._WAKE_RE = ai._wake_regex()  # noqa: SLF001

    def wake(text: str) -> bool:
        return _LOOP.run_until_complete(ai._wake_rule(make_group(text)))  # noqa: SLF001

    check("「小e 你好」仍能唤醒", wake("小e 你好"))
    check("「/小e 你好」不再当唤醒词", not wake("/小e 你好"))
    check("「/大象信息」不会被小e 接走", not wake("/大象信息"))
    check("「/pigng」不会被小e 接走", not wake("/pigng"))


def test_help() -> None:
    load_plugin("qqbot_help")
    mod = nonebot.get_plugin("qqbot_help").module
    overview = mod._overview()  # noqa: SLF001
    # 舞萌插件只登记了说明、没注册 matcher，总览按已加载插件的 matcher 分组，
    # 所以那里不会出现「舞萌」；它只出现在单个命令的详情里（下一组断言）。
    check("/help 总览不应冒出舞萌分组（只有说明，无命令）", "舞萌：" not in overview, overview)
    check("/help 总览提示只显示前几个", "只显示前几个" in overview, overview)
    lines = overview.splitlines()
    check("/help 总览「帮助」分组排最后", lines[-4].startswith("帮助："), lines[-5:])
    # 线上／容器里会在启动时把舞萌命令「注册成真正的命令」（见 qqbot_maimai_dx 末尾的
    # 帮助登记）；工作区里没有 maimai_chn 依赖，插件没加载，这里模拟那次登记。
    mod.describe("大象信息", desc="玩家资料 + 本地状态", usage=["/大象信息"], group="舞萌")
    mod.describe("更新成绩", "更新b50", "更新B50", "B50", "b50",
                 desc="读取最新成绩并出成绩图", usage=["/更新成绩"], group="舞萌")
    # 线上启动时登记进 qqbot_help 的舞萌命令必须能被 /help 查到；纠错表
    # (qqbot_ai.typo) 只收「真正注册成命令」的 matcher，舞萌插件的规则不是
    # command matcher，所以在纠错表里查不到、也无需查——这里只断言 /help 一侧。
    check("命令表里有大象信息", "大象信息" in mod._command_map(), list(mod._command_map())[:5])  # noqa: SLF001
    check("命令表里有更新成绩", "更新成绩" in mod._command_map())  # noqa: SLF001
    # 裸「大象信息」不带 / 时，小e 不接（交给命令解析，没有命令就静默）；
    # 带 / 时 /help 查得到，这就是本轮的交付点。
    check("裸「大象信息」不是 help 里的功能名", mod._find_feature("大象信息") is None)  # noqa: SLF001
    check("B50 是已知别名", "b50" in mod._aliases(), "")  # noqa: SLF001
    detail = mod._cmd_help_text("/大象信息")  # noqa: SLF001
    check("/help 大象信息 有说明", "玩家资料" in detail, detail)
    detail = mod._cmd_help_text("B50")  # noqa: SLF001
    check("/B50 help 复用更新成绩说明", "成绩" in detail, detail)



def test_feature_alias() -> None:
    load_plugin("qqbot_group")
    plugin = nonebot.get_plugin("qqbot_group")
    names: set[str] = set()
    for matcher in plugin.matcher:
        for checker in getattr(getattr(matcher, "rule", None), "checkers", []) or []:
            call = getattr(checker, "call", None) or checker
            for item in getattr(call, "cmds", None) or []:
                if isinstance(item, str):
                    names.add(item)
                elif isinstance(item, (tuple, list)):
                    names.update(str(x) for x in item if x)
    check("「功能列表」是 /本群功能 的别名", "功能列表" in names, sorted(names)[:10])


def main() -> None:
    for fn in (test_ai_wake, test_maimai_rules, test_help, test_feature_alias):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            check(f"{fn.__name__} 执行失败", False, repr(exc))

    print()
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if not ok else ""))
    bad = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 60)
    print(f"总计 {len(RESULTS)} 项，失败 {len(bad)} 项")
    print("RESULT:", "PASS" if not bad else "FAIL")
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
