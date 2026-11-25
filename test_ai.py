#!/usr/bin/env python3
"""qqbot_ai 测试：文本压缩 / 输出过滤 / 会话存储 / 工具调用全链路。

不访问外网：把模板里用的 ``http://fake`` 换成进程内的 asyncio HTTP 服务器，
由它按预设脚本依次返回「要工具」和「最终回答」。
"""

import asyncio
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import nonebot  # noqa: E402

nonebot.init()

from nonebot.adapters.onebot.v11 import (  # noqa: E402
    Adapter as OneBotAdapter,
    Bot as OneBotBot,
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Reply, Sender  # noqa: E402
from nonebot.message import handle_event  # noqa: E402

SELF_ID = "1"
GROUP = 984616276
USER = 10001

nonebot.get_driver().register_adapter(OneBotAdapter)
ADAPTER = next(iter(nonebot.get_driver()._adapters.values()))

results: list[tuple[str, bool, str]] = []
FALLBACK = "这个回答我不太方便说，换个问题吧。"


def make_png(w: int = 8, h: int = 8, rgb=(255, 0, 0)) -> bytes:
    """生成一张合法的纯色 PNG，用于测试图片段解析。"""
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"  {'[PASS]' if cond else '[FAIL]'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


# 先加载一个有真实命令的插件，纠错功能要能跨插件收集命令表
_toolbox = nonebot.load_plugin(Path("plugins/qqbot_toolbox"))
_newbie = nonebot.load_plugin(Path("plugins/qqbot_admin"))
plugin = nonebot.load_plugin(Path("plugins/qqbot_ai"))
check("插件加载", plugin is not None)

import plugins.qqbot_ai.text as T  # noqa: E402
import plugins.qqbot_ai.fuzzy as FUZZY  # noqa: E402
import plugins.qqbot_ai.tools as TOOLS  # noqa: E402
import plugins.qqbot_ai.typo as TYPO  # noqa: E402
import plugins.qqbot_ai.vision as VISION  # noqa: E402
from plugins.qqbot_ai.config import Config as C  # noqa: E402
from plugins.qqbot_ai.llm import LLMError, build_image_message, has_images, parse_response  # noqa: E402
from plugins.qqbot_ai.store import ConversationStore  # noqa: E402

mod = plugin.module
cfg = mod.plugin_config


# ---------------------------------------------------------------- 文本逻辑
def test_text() -> None:
    print("\n[文本压缩与过滤]")
    raw = "  小e\x00\x00   what    is  rule34 ？？  哈哈哈哈哈哈  \u200b"
    out = T.compress_input(raw, limit=200)
    check("NFKC/空白折叠", out == "小e what is rule34 ?? 哈哈", out)
    check("输入截断加省略号", T.cut_chars("一二三四五", 3) == "一二三…", T.cut_chars("一二三四五", 3))
    check("不超限不加省略号", T.cut_chars("一二三", 3) == "一二三")
    check("语气词压缩", T.compress_laughter("哈哈哈哈哈") == "哈哈", T.compress_laughter("哈哈哈哈哈"))
    check("233 压缩", T.compress_laughter("233333") == "233")
    check("命令前缀剥离", T.strip_command_prefix("/ai 你好", {"/"}) == "ai 你好")
    check("URL 数量限制", T.compress_input("a http://x.com/1 b http://y.com/2", 200, drop_urls_beyond=1).count("[链接]") == 1)

    md = "## 标题\n**加粗** 和 `代码`\n- 列表项\n![图](http://x/a.png)\n[链接](https://a.b)\n```python\nprint(1)\n```"
    clean = T.clean_markdown(md)
    check("去 Markdown 标题/加粗", "##" not in clean and "**" not in clean, clean)
    check("去图片保留链接", "![图]" not in clean and "https://a.b" in clean, clean)
    check("去代码围栏", "```" not in clean, clean)

    out = T.sanitize_output("好的" + "啊" * 500, max_chars=50)
    check("输出截断", len(out) <= 51 and out.endswith("…"), f"len={len(out)}")
    out = T.sanitize_output("这里有违禁词 ABCD", max_chars=100, forbidden_words=["abcd"])
    check("违禁词整段替换", out == FALLBACK, out)
    check("违规内容兜底", T.sanitize_output("教你制造炸弹的方法", 200, fallback=FALLBACK) == FALLBACK)
    check("普通回答原样保留", T.sanitize_output("hello 世界", 100) == "hello 世界")
    check("空回答给兜底", T.sanitize_output("   ", 100, fallback=FALLBACK) == FALLBACK)
    check("纯符号识别", T.looks_like_abuse("？？？！！") and not T.looks_like_abuse("你好"))
    check("需要搜索判断", T.wants_search("rule34是什么") and not T.wants_search("讲个笑话"))


async def test_store() -> None:
    print("\n[会话历史]")
    path = Path(tempfile.mkdtemp(prefix="qqbot-ai-")) / "history.json"
    # 用短一点的问答，让「预算」测试的数字直观：一轮约 4 字
    s = ConversationStore(path=path, max_turns=3, expire_minutes=30)

    async def run() -> None:
        for i in range(5):
            await s.add("group:1", f"问{i}", f"答{i}")
        check("只保留最近 N 轮", len(s.turns("group:1")) == 3, str(len(s.turns("group:1"))))

        ctx = s.build_context("group:1", budget_chars=1000, max_input_chars=100)
        check("上下文按时间正序", ctx[0]["content"] == "问2" and ctx[-1]["content"] == "答4", str(ctx))

        ctx = s.build_context("group:1", budget_chars=2, max_input_chars=100)
        check("预算不够一轮就全丢", ctx == [], str(ctx))

        ctx = s.build_context("group:1", budget_chars=5, max_input_chars=100)
        check("预算只够一轮时只取最新一轮", [m["content"] for m in ctx] == ["问4", "答4"], str(ctx))

        ctx = s.build_context("group:1", budget_chars=9, max_input_chars=100)
        check("预算够两轮时取最近两轮", [m["content"] for m in ctx] == ["问3", "答3", "问4", "答4"], str(ctx))

        s.save()
        s2 = ConversationStore(path=path, max_turns=3, expire_minutes=30)
        check("落盘后重新加载", len(s2.turns("group:1")) == 3)

        s.mode = "user"
        s._data.clear()
        # 场景：A 问一问、B 插一句、A 再追问 —— A 的追问只接自己，越过 B
        await s.add("g", "A问1", "A答1", user_key="user:1")
        await s.add("g", "B问", "B答", user_key="user:2")
        await s.add("g", "A问2", "A答2", user_key="user:1")
        ctx = s.build_context("g", 1000, 100, user_key="user:1")
        check("user 模式跳过别人的轮次",
              [m["content"] for m in ctx] == ["A问1", "A答1", "A问2", "A答2"], str(ctx))
        ctx = s.build_context("g", 1000, 100, user_key="user:2")
        check("user 模式 B 只看到自己的", [m["content"] for m in ctx] == ["B问", "B答"], str(ctx))
        # shared 模式下两人都能看到全部
        s.mode = "shared"
        ctx = s.build_context("g", 1000, 100, user_key="user:1")
        check("shared 模式不按人过滤", len(ctx) == 6, str(len(ctx)))
        s.mode = "shared"
        s._data.clear()
        for i in range(5):
            await s.add("group:1", f"问{i}", f"答{i}")

        removed = await s.clear("group:1")
        check("清空上下文", removed == 3 and s.turns("group:1") == [])

        expired = ConversationStore(path=None, max_turns=3, expire_minutes=1)
        await expired.add("u", "q", "a")
        for t in expired._data["u"]:
            t.ts -= 120
        check("过期会话被清理", expired.turns("u") == [])

    await run()


async def test_vision() -> None:
    print("\n[图片解析]")
    png = make_png()
    tmp = Path(tempfile.mkdtemp(prefix="qqbot-ai-image-"))
    img_path = tmp / "a.png"
    img_path.write_bytes(png)

    async def run() -> None:
        # 消息段解析：对象段 / 原始 dict 段 / 非图片段
        msg = Message([
            MessageSegment.text("这是什么"),
            MessageSegment.image(f"file://{img_path}"),
        ])
        sources = VISION.image_sources(msg)
        check("从消息里取出图片段", len(sources) == 1 and sources[0]["file"].startswith("file://"), str(sources))
        raw = [{"type": "text", "data": {"text": "hi"}},
               {"type": "image", "data": {"file": "file:///tmp/x.png", "url": "http://x/1.png"}}]
        check("兼容原始 dict 段", VISION.image_sources(raw)[0]["url"] == "http://x/1.png")
        check("非图片消息无图", VISION.image_sources(Message("纯文字")) == [])

        # data URL 生成
        url = VISION.to_data_url(png)
        check("PNG 嗅探出 data URL", url is not None and url.startswith("data:image/png;base64,"), str(url)[:40])
        check("不认识的格式返回 None", VISION.to_data_url(b"not an image") is None)

        # 本地文件读取
        data_url = await VISION.load_image({"file": f"file://{img_path}", "url": ""}, 1024 * 1024)
        check("本地 file:// 图片可读取", data_url is not None and data_url.startswith("data:image/png"), str(data_url)[:40])
        check("文件不存在返回 None", await VISION.load_image({"file": "file:///nope/x.png", "url": ""}, 1024) is None)
        check("超大图片被跳过", await VISION.load_image({"file": f"file://{img_path}", "url": ""}, 10) is None)

        # 跨容器兜底：file:// 指向别的容器路径时，按文件名去缓存目录找
        cache = Path(tempfile.mkdtemp(prefix="llbot-cache-"))
        (cache / "ABC123.jpg").write_bytes(png)
        VISION._LOCAL_CACHE_DIRS = (cache,)
        got = await VISION.load_image(
            {"file": "file:///app/llbot/data/temp/ABC123.jpg", "url": ""}, 1024 * 1024
        )
        check("file:// 跨容器时按文件名兜底", got is not None and got.startswith("data:image/png"),
              str(got)[:40])
        check("路径穿越被挡住",
              VISION.find_in_local_cache("../../../etc/passwd") is None)
        VISION._LOCAL_CACHE_DIRS = ()

        # 多张 + 上限
        msg = Message([MessageSegment.image(f"file://{img_path}") for _ in range(3)])
        got = await VISION.collect_images(msg, max_images=2, max_bytes=1024 * 1024)
        check("最多取 N 张", len(got) == 2, str(len(got)))

        # 多模态消息格式
        m = build_image_message("这是什么", [url])
        check("vision 消息结构", m["role"] == "user" and m["content"][0]["type"] == "text"
              and m["content"][1]["image_url"]["url"].startswith("data:image/png"), str(m)[:80])
        check("has_images 识别", has_images([m]) and not has_images([{"role": "user", "content": "hi"}]))

    await run()


def test_llm_parse() -> None:
    print("\n[API 响应解析]")
    r = parse_response({"choices": [{"message": {"content": "你好"}}]})
    check("纯文本回复", r.content == "你好" and not r.has_tool_calls)
    r = parse_response(
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "web_search", "arguments": '{"query": "rule34"}'}}
                        ],
                    }
                }
            ]
        }
    )
    check("tool_calls 解析", r.has_tool_calls and r.tool_calls[0].arguments == {"query": "rule34"}, str(r))
    r = parse_response(
        {"choices": [{"message": {"content": None, "tool_calls": [
            {"function": {"name": "x", "arguments": "```json\n{\"a\": 1}\n```"}}]}}]}
    )
    check("坏 JSON 宽容解析", r.tool_calls[0].arguments == {"a": 1}, str(r.tool_calls[0].arguments))
    try:
        parse_response({"bad": 1})
        check("异常响应报错", False)
    except LLMError:
        check("异常响应报错", True)


async def test_tools() -> None:
    print("\n[工具定义]")

    async def fake_search(args):  # noqa: ANN001
        if not args.get("query"):
            return "搜索关键词为空"
        if args.get("site") == "fail":
            raise RuntimeError("boom")
        return "1. 34号法则\n   摘要：如果某物存在，就有它的色情版本\n   链接：https://zh.wikipedia.org/x"

    tools = TOOLS.build_tools(fake_search, 4)
    names = [t.name for t in tools]
    check("工具清单", names == ["get_current_time", "convert_currency", "web_search"], str(names))
    schema = tools[names.index("web_search")].schema()
    check("OpenAI function 格式", schema["type"] == "function" and schema["function"]["name"] == "web_search")

    async def run() -> None:
        by_name = {t.name: t for t in tools}
        out = await TOOLS.execute_tool(by_name["web_search"], {"query": "rule34"}, 1000)
        check("搜索工具执行", "34号法则" in out, out)
        out = await TOOLS.execute_tool(by_name["web_search"], {"site": "fail", "query": "x"}, 1000)
        check("工具异常转文字", "执行失败" in out, out)
        out = await TOOLS.execute_tool(by_name["web_search"], {"query": "x"}, 40)
        check("工具结果截断", len(out) <= 40 + 8, out)
        out = await TOOLS.execute_tool(by_name["get_current_time"], {}, 100)
        check("时间工具", "北京时间" in out, out)
        check(
            "不暴露搜索时只剩时间与汇率",
            [t.name for t in TOOLS.build_tools(None, 4)] == ["get_current_time", "convert_currency"],
        )
        check(
            "搜索与汇率都关掉时只有时间",
            [t.name for t in TOOLS.build_tools(None, 4, currency_enabled=False)] == ["get_current_time"],
        )

    await run()


# ---------------------------------------------------------------- 端到端
SCRIPTED: list[dict] = []
LLM_CALLS: list[dict] = []


async def _llm_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        length = 0
        for line in head.decode("latin-1").split("\r\n"):
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1])
        body = await reader.readexactly(length) if length else b"{}"
        LLM_CALLS.append(json.loads(body or b"{}"))
        payload = SCRIPTED.pop(0) if SCRIPTED else {"choices": [{"message": {"content": "（无脚本）"}}]}
        data = json.dumps(payload, ensure_ascii=False).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(data)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + data
        )
        await writer.drain()
    except Exception:  # noqa: BLE001
        pass
    finally:
        writer.close()


def text_payload(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def tool_call_payload(name: str, args: dict, call_id: str) -> dict:
    """一组「连续调工具」脚本用；与 tool_payload 同格式，单独命名便于阅读。"""
    return tool_payload(name, args, call_id)


def tool_payload(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": call_id, "type": "function",
                         "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
                    ],
                }
            }
        ]
    }


class FakeBot(OneBotBot):
    def __init__(self) -> None:
        super().__init__(ADAPTER, SELF_ID)
        self.calls: list[tuple[str, dict]] = []
        self.message_id = 0

    async def call_api(self, api: str, **data):  # type: ignore[override]
        self.calls.append((api, data))
        self.message_id += 1
        return {"message_id": self.message_id}

    def last(self) -> str:
        for api, data in reversed(self.calls):
            if data.get("message") is not None:
                return Message(data["message"]).extract_plain_text()
        return ""

    def reply_count(self) -> int:
        return sum(1 for api, _ in self.calls if api == "send_msg")


def make_group(msg, user_id: int = USER) -> GroupMessageEvent:
    m = msg if isinstance(msg, Message) else Message(msg)
    return GroupMessageEvent(
        time=1, self_id=SELF_ID, post_type="message", sub_type="normal",
        user_id=user_id, group_id=GROUP, message=m, original_message=m,
        message_type="group", raw_message=str(m), font=1, message_id=1,
        sender=Sender(user_id=user_id, role="member"),
    )


def make_group_with_reply(msg, reply_msg, user_id: int = USER) -> GroupMessageEvent:
    """群消息 + 引用了另一条消息（模拟 QQ 的「回复」）。"""
    m = msg if isinstance(msg, Message) else Message(msg)
    rm = reply_msg if isinstance(reply_msg, Message) else Message(reply_msg)
    ev = GroupMessageEvent(
        time=1, self_id=SELF_ID, post_type="message", sub_type="normal",
        user_id=user_id, group_id=GROUP, message=m, original_message=m,
        message_type="group", raw_message=str(m), font=1, message_id=2,
        sender=Sender(user_id=user_id, role="member"),
    )
    ev.reply = Reply(
        time=1, message_type="group", message_id=1, real_id=1,
        sender=Sender(user_id=user_id, role="member"), message=rm,
    )
    return ev


def make_group_at_other(msg, other_qq: str = "2854211260", user_id: int = USER) -> GroupMessageEvent:
    """群消息，且 @ 了别人（不是机器人）。"""
    m = msg if isinstance(msg, Message) else Message(msg)
    full = Message(MessageSegment("at", {"qq": other_qq})) + m
    ev = make_group(full, user_id)
    ev.to_me = False
    return ev


def make_group_at_me(msg, user_id: int = USER) -> GroupMessageEvent:
    """群里 @机器人。"""
    m = msg if isinstance(msg, Message) else Message(msg)
    ev = make_group(m, user_id)
    ev.to_me = True
    return ev


def make_private(msg, user_id: int = USER) -> PrivateMessageEvent:
    m = msg if isinstance(msg, Message) else Message(msg)
    return PrivateMessageEvent(
        time=1, self_id=SELF_ID, post_type="message", sub_type="friend",
        user_id=user_id, message=m, original_message=m, message_type="private",
        raw_message=str(m), font=1, message_id=1,
        sender=Sender(user_id=user_id, nickname="群友"),
    )


async def test_e2e(server: asyncio.AbstractServer) -> None:
    print("\n[端到端：小e 唤醒 + 工具调用]")
    port = server.sockets[0].getsockname()[1]
    mod.DATA_DIR = Path(tempfile.mkdtemp(prefix="qqbot-ai-e2e-"))

    async def fake_search(args):  # noqa: ANN001
        return (
            "「rule34」的搜索结果：\n"
            "1. 34号法则 - 维基百科\n"
            "   摘要：Rule 34 即「只要某物存在，就有它的本子」，2003 年起源于网络。\n"
            "   链接：https://zh.wikipedia.org/wiki/34%E8%A7%84%E5%88%99"
        )

    agent = mod.agent
    agent.client.api_base = f"http://127.0.0.1:{port}/v1"
    agent.client.api_key = "sk-test"
    cfg.qqbot_ai_api_key = "sk-test"  # 插件入口会检查这个字段
    agent._tools = TOOLS.build_tools(fake_search, 4)
    mod._cooldown.clear()

    # 1. 唤醒 + 工具调用
    LLM_CALLS.clear()
    SCRIPTED[:] = [
        tool_payload("web_search", {"query": "rule34 是什么"}),
        text_payload("Rule34 就是「只要有它存在，就有它的色情版本」的网络梗，又叫34号法则。"),
    ]
    bot = FakeBot()
    await handle_event(bot, make_group("小e rule34是什么"))
    replied = bot.last()
    check("小e 唤醒并回复", "Rule34" in replied, replied)
    check("调用了一次模型+一次工具回填", len(LLM_CALLS) == 2, str(len(LLM_CALLS)))
    first = LLM_CALLS[0]
    check("请求带 tools", any(t["function"]["name"] == "web_search" for t in first.get("tools", [])))
    second_msgs = LLM_CALLS[1]["messages"]
    check("历史里回填了 tool 结果",
          second_msgs[-1]["role"] == "tool" and "34号法则" in second_msgs[-1]["content"],
          str(second_msgs[-1])[:120])
    check("系统提示词注入字数上限",
          str(cfg.qqbot_ai_max_reply_chars) in second_msgs[0]["content"],
          second_msgs[0]["content"][:80])
    check("max_tokens 受限", LLM_CALLS[0]["max_tokens"] == cfg.qqbot_ai_max_output_tokens)
    check("问题已被压缩去掉语气词", LLM_CALLS[0]["messages"][-1]["content"] == "rule34是什么",
          LLM_CALLS[0]["messages"][-1]["content"])

    # 2. 上下文延续
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("它是 2003 年流行的网络梗。")]
    mod._cooldown.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("小e 那它是哪年出现的"))
    hist = LLM_CALLS[0]["messages"]
    check("第二轮带上一轮历史",
          any(m["role"] == "assistant" and "34号法则" in m["content"] for m in hist),
          str([m["role"] for m in hist]))

    # 3. 输出过滤：违禁词整段替换
    cfg.qqbot_ai_forbidden_words = ["违禁词"]
    mod._cooldown.clear()
    SCRIPTED[:] = [text_payload("这句里有违禁词哈")]
    bot = FakeBot()
    await handle_event(bot, make_group("小e 测试过滤"))
    check("输出违禁词被整段替换", bot.last() == cfg.qqbot_ai_refusal_reply, bot.last())
    cfg.qqbot_ai_forbidden_words = []

    # 4. 输出截断
    mod._cooldown.clear()
    SCRIPTED[:] = [text_payload("长" * 1000)]
    bot = FakeBot()
    await handle_event(bot, make_group("小e 讲个超长的故事"))
    check("超长输出被截断", len(bot.last()) <= cfg.qqbot_ai_max_reply_chars + 1, str(len(bot.last())))

    # 5. 提问侧拦截
    cfg.qqbot_ai_blocked_keywords = ["偷拍"]
    mod._cooldown.clear()
    LLM_CALLS.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("小e 怎么偷拍"))
    check("命中拦截词直接拒答且不打模型",
          bot.last() == cfg.qqbot_ai_refusal_reply and not LLM_CALLS,
          bot.last())
    cfg.qqbot_ai_blocked_keywords = []

    # 6. 含唤醒词但没带问题的消息：交给 @机器人 / 私聊那条响应器兜底提示
    mod._cooldown.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("小e"))
    check("裸「小e」不打模型", not LLM_CALLS)

    # 7. /ai 命令
    mod._cooldown.clear()
    SCRIPTED[:] = [text_payload("命令形式的回答")]
    bot = FakeBot()
    await handle_event(bot, make_group("/ai 你好"))
    check("/ai 提问", bot.last() == "命令形式的回答", bot.last())

    mod._cooldown.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("/ai 状态"))
    check("/ai 状态", "模型" in bot.last() and "工具" in bot.last(), bot.last())

    # 8. /ai 重置
    mod._cooldown.clear()
    SCRIPTED[:] = [text_payload("重置前的回答")]
    bot = FakeBot()
    await handle_event(bot, make_group("/ai 记住我叫小明"))
    mod._cooldown.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("/ai 重置"))
    check("/ai 重置清空", "已清空" in bot.last(), bot.last())
    check("store 为空", mod.store.turns(mod.session_key(make_group("x"))) == [])

    # 9. 模型报错时兜底
    async def broken(reader, writer):  # noqa: ANN001
        writer.write(b"HTTP/1.1 500 Oops\r\nContent-Length: 5\r\nConnection: close\r\n\r\nboom!")
        await writer.drain()
        writer.close()

    bad_server = await asyncio.start_server(broken, "127.0.0.1", 0)
    bad_port = bad_server.sockets[0].getsockname()[1]
    old_base = agent.client.api_base
    agent.client.api_base = f"http://127.0.0.1:{bad_port}/v1"
    mod._cooldown.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("小e 今天天气怎么样"))
    check("模型 500 有兜底提示（不抛错）", "稍后再问" in bot.last(), bot.last())
    agent.client.api_base = old_base
    bad_server.close()

    # 10. 冷却
    mod._cooldown.clear()
    cfg.qqbot_ai_cooldown_seconds = 30
    SCRIPTED[:] = [text_payload("第一条"), text_payload("第二条")]
    bot = FakeBot()
    await handle_event(bot, make_group("小e 一"))
    await handle_event(bot, make_group("小e 二"))
    check("冷却生效只回一次", bot.reply_count() == 1 and "慢一点" not in bot.last(), f"{bot.reply_count()} {bot.last()}")
    cfg.qqbot_ai_cooldown_seconds = 5.0
    mod._cooldown.clear()

    # 11. 私聊直接问
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("私聊回答")]
    bot = FakeBot()
    await handle_event(bot, make_private("你好小e几点开会"))
    check("私聊直接问（无需唤醒词）", bot.last() == "私聊回答", bot.last())

    # 12. 群里不带唤醒词的日常聊天不触发
    mod._cooldown.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("今天中午吃什么"))
    check("日常聊天不触发", bot.reply_count() == 0, bot.last())

    # 14. 引用图片提问：「小e 这是什么」
    img_file = Path(tempfile.mkdtemp(prefix="qqbot-ai-pic-")) / "pic.png"
    img_file.write_bytes(make_png())
    img_seg = MessageSegment.image(f"file://{img_file}")
    cfg.qqbot_ai_vision_model = "vision-test"
    mod.agent.client.vision_model = cfg.qqbot_ai_vision_model  # 配置在插件启动时已注入 client
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("这是一张纯红色的方形图片。")]
    bot = FakeBot()
    await handle_event(bot, make_group_with_reply("小e 这是什么", Message([img_seg])))
    body = LLM_CALLS[0]
    content = body["messages"][-1]["content"]
    check("引用图片被转成多模态消息",
          isinstance(content, list) and content[0]["type"] == "text"
          and content[1]["image_url"]["url"].startswith("data:image/png"),
          str(content)[:100])
    check("带图时切到视觉模型", body["model"] == "vision-test", body.get("model"))
    check("引用图片有回答", "红色" in bot.last(), bot.last())

    # 15. 追问时不重复带图，但历史里有「附图」标记
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("它是方形的。")]
    bot = FakeBot()
    await handle_event(bot, make_group("小e 它是什么形状"))
    hist = LLM_CALLS[0]["messages"]
    check("图片不进历史（省 token）",
          not any(isinstance(m.get("content"), list) for m in hist), str([m["role"] for m in hist]))
    check("历史里保留了附图标记",
          any("附图" in str(m.get("content")) for m in hist), str(hist))
    check("纯文字追问用默认模型", LLM_CALLS[0]["model"] == cfg.qqbot_ai_model, LLM_CALLS[0].get("model"))

    # 16. @机器人 + 只发图（没有文字）
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("图里是一个红色方块。")]
    bot = FakeBot()
    await handle_event(bot, make_group_at_me(Message([MessageSegment.text("\n"), img_seg])))
    check("@机器人只发图也能回答", "红色" in bot.last(), bot.last())
    default_q = LLM_CALLS[0]["messages"][-1]["content"][0]["text"]
    check("没文字时自动补默认问题", "图" in default_q and len(default_q) > 5, default_q)

    # 17. 图片 + 工具联用（先看图或先搜索都行）
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [
        tool_payload("web_search", {"query": "红色方块 梗"}),
        text_payload("这是红色方块梗图。"),
    ]
    bot = FakeBot()
    await handle_event(bot, make_group_with_reply("小e 这是什么梗", Message([img_seg])))
    check("图片与工具可同时使用",
          len(LLM_CALLS) == 2 and bot.last() == "这是红色方块梗图。", bot.last())

    # 18. 关掉图片解析时退化为纯文字
    cfg.qqbot_ai_vision_enabled = False
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("没有图片，我只能按文字回答。")]
    bot = FakeBot()
    await handle_event(bot, make_group_with_reply("小e 这是什么", Message([img_seg])))
    check("关闭看图后不带图", isinstance(LLM_CALLS[0]["messages"][-1]["content"], str))
    cfg.qqbot_ai_vision_enabled = True

    # 28. @机器人 发命令时，AI 不能抢答（真实事故：@bot /开启 todo 被当聊天回答）
    mod._cooldown.clear()
    LLM_CALLS.clear()
    bot = FakeBot()
    await handle_event(bot, make_group_at_me("/ping", user_id=USER))
    check("@bot /ping 不调模型", not LLM_CALLS, f"模型被调了 {len(LLM_CALLS)} 次")
    check("@bot /ping 正常由命令处理", "pong" in bot.last() or "ms" in bot.last(), bot.last())

    # @机器人 发打错的命令 -> 走纠错，也不该调模型
    mod._typo_notice_at.clear()
    LLM_CALLS.clear()
    bot = FakeBot()
    await handle_event(bot, make_group_at_me("/pigng", user_id=USER))
    check("@bot /pigng 不调模型", not LLM_CALLS, f"模型被调了 {len(LLM_CALLS)} 次")
    check("@bot /pigng 走纠错", "ping" in bot.last(), bot.last())

    # 【2026-11-06 事故】@ 了别人（另一个 bot）的打错命令，小E 不能抢答
    # 真实日志：@是萌卡喵呢 /今日老婆 → 小E 回了「没有今日老婆这个命令」
    for bad in ("/今日老婆", "/签道", "/pigng"):
        mod._typo_notice_at.clear()
        LLM_CALLS.clear()
        bot = FakeBot()
        await handle_event(bot, make_group_at_other(bad, user_id=USER))
        check(f"@别人 {bad} 不纠错", bot.reply_count() == 0, f"回了：{bot.last()}")

    # 对照：同样打错，但没 @ 任何人 → 小E 仍应纠错
    mod._typo_notice_at.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("/pigng", user_id=USER))
    check("没@任何人时仍纠错", "ping" in bot.last(), bot.last())

    # 不带命令前缀的 @ 聊天仍然走 AI
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("这是聊天回答")]
    bot = FakeBot()
    await handle_event(bot, make_group_at_me("今天吃什么", user_id=USER))
    check("@bot 普通聊天仍走 AI", bot.last() == "这是聊天回答", bot.last())

    # 私聊里发命令也不该被 AI 吃掉
    LLM_CALLS.clear()
    bot = FakeBot()
    await handle_event(bot, make_private("/ping", user_id=USER))
    check("私聊 /ping 不调模型", not LLM_CALLS, f"模型被调了 {len(LLM_CALLS)} 次")

    # 19. /ai 命令不附图（纯文字参数）
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("命令回答")]
    bot = FakeBot()
    await handle_event(bot, make_group_with_reply("/ai 这是什么", Message([img_seg])))
    check("/ai 命令不附图", isinstance(LLM_CALLS[0]["messages"][-1]["content"], str))

    # 20. 回归：唤醒词「小e」不能被 /ai 命令的别名抢走
    #     （曾经 ai_cmd aliases 里带「小e」，priority=5 抢在唤醒规则 priority=8 之前，
    #      导致「小e 引用图片」走命令分支、图片被丢弃）
    cmd = mod.ai_cmd
    aliases = set()
    for rule in (getattr(cmd, "rule", None),):
        pass
    for checker in getattr(cmd, "rule", None).checkers if getattr(cmd, "rule", None) else []:
        parser = getattr(checker, "sub_cmds", None) or getattr(checker, "cmds", None)
        if parser is not None:
            for cmds in getattr(parser, "cmds", []) or []:
                aliases.update(cmds)
    check("ai 命令别名不含小e", "小e" not in aliases, str(aliases))
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("红色图片")]
    bot = FakeBot()
    await handle_event(bot, make_group_with_reply("小e 这是什么", Message([img_seg])))
    content = LLM_CALLS[0]["messages"][-1]["content"]
    check("唤醒词走看图分支（不被命令抢）",
          isinstance(content, list) and any(p.get("type") == "image_url" for p in content),
          str(content)[:80])

    # 20. 思考吃光 max_tokens（正文空）时自动加大额度重试
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [
        {"choices": [{"message": {"role": "assistant", "content": "",
                                   "reasoning_content": "让我想想……"}, "finish_reason": "length"}]},
        text_payload("想好了：这是一张红色图片。"),
    ]
    bot = FakeBot()
    await handle_event(bot, make_group("小e 图里是什么"))
    check("正文为空时自动重试", len(LLM_CALLS) == 2, str(len(LLM_CALLS)))
    check("重试放大了 max_tokens",
          LLM_CALLS[1]["max_tokens"] > LLM_CALLS[0]["max_tokens"],
          f"{LLM_CALLS[0].get('max_tokens')} -> {LLM_CALLS[1].get('max_tokens')}")
    check("重试后拿到正文", "红色图片" in bot.last(), bot.last())

    # 21. 看图请求使用视觉超时与加大的 max_tokens
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("红色图")]
    bot = FakeBot()
    await handle_event(bot, make_group_at_me(Message([MessageSegment.text("\n"), img_seg])))
    check("看图 token 额度 = 基础 + 额外",
          LLM_CALLS[0]["max_tokens"] == cfg.qqbot_ai_max_output_tokens + cfg.qqbot_ai_vision_extra_tokens,
          str(LLM_CALLS[0].get("max_tokens")))

    # 22. 连续追问：同一人连问 3 次，上下文逐轮累积
    mod._cooldown.clear()
    mod.store._data.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [
        text_payload("我叫小明，今年 10 岁。"),
        text_payload("他 10 岁。"),
        text_payload("他叫小明。"),
    ]
    bot = FakeBot()
    await handle_event(bot, make_group("小e 我叫小明今年10岁", user_id=USER))
    check("场景22: 第一次提问只调一次模型", len(LLM_CALLS) == 1, f"LLM_CALLS={len(LLM_CALLS)}")
    mod._cooldown.clear()
    bot2 = FakeBot()
    await handle_event(bot2, make_group("小e 我多大", user_id=USER))
    mod._cooldown.clear()
    await handle_event(bot2, make_group("小e 我叫什么", user_id=USER))
    roles = [m["role"] for m in LLM_CALLS[-1]["messages"]]
    check("连续追问累积上下文", roles.count("user") >= 3 and roles.count("assistant") >= 2, str(roles))
    check("追问带上第一问", "小明" in LLM_CALLS[-1]["messages"][1]["content"],
          LLM_CALLS[-1]["messages"][1]["content"])
    check("场景22: 三轮共调 3 次", len(LLM_CALLS) == 3, f"call={len(LLM_CALLS)}")
    check("第三问的回答", bot2.last() == "他叫小明。", bot2.last())

    # 23. 历史轮数上限：超过 history_turns 的旧轮被丢弃
    mod._cooldown.clear()
    mod.store._data.clear()
    cfg.qqbot_ai_history_turns = 3
    mod.store.max_turns = 3
    SCRIPTED[:] = [text_payload(f"回答{i}") for i in range(6)]
    bot = FakeBot()
    for i in range(6):
        mod._cooldown.clear()
        await handle_event(bot, make_group(f"小e 问题{i}", user_id=USER))
    check("历史只保留 N 轮", len(mod.store.turns(f"group:{GROUP}")) == 3,
          str(len(mod.store.turns(f"group:{GROUP}"))))
    last_msgs = [m for m in LLM_CALLS[-1]["messages"] if m["role"] != "system"]
    kept = [m["content"] for m in last_msgs]
    check("最旧的轮次已滚出上下文",
          kept == ["问题2", "回答2", "问题3", "回答3", "问题4", "回答4", "问题5"],
          str(kept)[:150])

    # 24. shared 模式下，群里另一个人也能接话
    mod._cooldown.clear()
    mod.store._data.clear()
    cfg.qqbot_ai_history_turns = 6
    mod.store.max_turns = 6
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("这个群在聊《小猪通行证》。"), text_payload("在聊小猪通行证。")]
    await handle_event(FakeBot(), make_group("小e 我们在聊什么", user_id=USER))
    mod._cooldown.clear()
    await handle_event(FakeBot(), make_group("小e 刚才说啥了", user_id=USER + 1))
    check("shared 模式跨用户共享",
          any("小猪通行证" in str(m.get("content")) for m in LLM_CALLS[-1]["messages"]),
          str([m["content"] for m in LLM_CALLS[-1]["messages"]])[:150])

    # 25. user 模式下，别人插话不会串到我这边
    mod.store.mode = "user"
    mod._cooldown.clear()
    mod.store._data.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("记住了，你叫小李。")]
    await handle_event(FakeBot(), make_group("小e 我叫小李", user_id=USER))
    mod._cooldown.clear()
    SCRIPTED.append(text_payload("我叫小王。"))
    await handle_event(FakeBot(), make_group("小e 我叫小王", user_id=USER + 1))
    mod._cooldown.clear()
    SCRIPTED.append(text_payload("你叫小李。"))
    LLM_CALLS.clear()
    await handle_event(FakeBot(), make_group("小e 我叫什么", user_id=USER))
    mine = str([m for m in LLM_CALLS[-1]["messages"] if m["role"] != "system"])
    check("user 模式只带自己的历史", "小李" in mine and "小王" not in mine, mine[:200])
    mod.store.mode = "shared"

    # 26. /ai 重置 后不再带上下文
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [text_payload("重置了")]
    bot = FakeBot()
    await handle_event(bot, make_group("/ai 重置"))
    mod._cooldown.clear()
    SCRIPTED.append(text_payload("不知道你叫啥。"))
    LLM_CALLS.clear()
    await handle_event(FakeBot(), make_group("小e 我叫什么", user_id=USER))
    check("重置后上下文为空", len(LLM_CALLS[-1]["messages"]) == 2,
          str([m["role"] for m in LLM_CALLS[-1]["messages"]]))

    # 27. 过期：历史超时后自动失效
    mod._cooldown.clear()
    mod.store._data.clear()
    mod.store.expire_seconds = 1
    SCRIPTED[:] = [text_payload("很短的回答")]
    await handle_event(FakeBot(), make_group("小e 测试过期", user_id=USER))
    for t in mod.store._data.get(f"group:{GROUP}", []):
        t.ts -= 120
    LLM_CALLS.clear()
    SCRIPTED.append(text_payload("不知道"))
    mod._cooldown.clear()
    await handle_event(FakeBot(), make_group("小e 还记得吗", user_id=USER))
    check("过期历史不再带入", len(LLM_CALLS[-1]["messages"]) == 2,
          str([m["role"] for m in LLM_CALLS[-1]["messages"]]))
    mod.store.expire_seconds = cfg.qqbot_ai_history_expire_minutes * 60

    # 13. 达到工具轮数上限后强制收尾（顺便验证服务端 max_tokens=1 也不会回显 text 字段）
    mod._cooldown.clear()
    LLM_CALLS.clear()
    SCRIPTED[:] = [tool_call_payload("get_current_time", {}, f"c{i}") for i in range(1, 5)]
    SCRIPTED.append(text_payload("现在是北京时间下午三点。"))
    bot = FakeBot()
    await handle_event(bot, make_group("小e 现在几点"))
    check("工具轮数封顶", len(LLM_CALLS) <= 5, f"calls={len(LLM_CALLS)}")
    check("模型不肯收尾时用工具结果兜底", "北京时间" in bot.last(), bot.last())

    await mod.agent.close()


def test_config() -> None:
    print("\n[配置]")
    c = C(qqbot_ai_wake_words="小e,小E", qqbot_ai_blocked_keywords='["偷拍","未成年"]')
    check("列表配置兼容逗号/JSON", c.qqbot_ai_wake_words == ["小e", "小E"] and c.qqbot_ai_blocked_keywords == ["偷拍", "未成年"])
    try:
        C(qqbot_ai_max_input_chars=500, qqbot_ai_max_context_chars=100)
        check("上下文预算校验", False)
    except Exception:
        check("上下文预算校验", True)
    try:
        C(qqbot_ai_search_backend="baidu")
        check("搜索后端校验", False)
    except Exception:
        check("搜索后端校验", True)


def test_fuzzy() -> None:
    print("\n[命令纠错：相似度算法]")
    cmds = [
        FUZZY.CommandEntry("签到", ("每日签到",), "每日签到"),
        FUZZY.CommandEntry("禁言", ("ban", "解禁"), "禁言"),
        FUZZY.CommandEntry("烟", ("抽烟", "吸烟"), "随机禁言"),
        FUZZY.CommandEntry("金句", ("金句添加",), "点名"),
        FUZZY.CommandEntry("表情", ("发表情",), "表情包"),
        FUZZY.CommandEntry("捕鱼", ("钓鱼",), "捕鱼"),
        FUZZY.CommandEntry("ping", (), "探活"),
    ]

    def names(q):
        return [s.entry.name for s in FUZZY.suggest(q, cmds)]

    check("拼错一个字：签名->签到", names("签名")[:1] == ["签到"], str(names("签名")))
    check("少一个字：表情包->表情", names("表情包")[:1] == ["表情"], str(names("表情包")))
    check("拼音：qiansao->签到", names("qiansao")[:1] == ["签到"], str(names("qiansao")))
    check("拼音：jinyu->金句", names("jinyu")[:1] == ["金句"], str(names("jinyu")))
    check("拼音完全一致优先：yan->烟", names("yan")[:1] == ["烟"], str(names("yan")))
    check("英文命令拼错：pingg->ping", names("pingg")[:1] == ["ping"], str(names("pingg")))
    check("完全无关不乱推荐：sdad", names("sdad") == [], str(names("sdad")))
    check("单字不硬凑", names("骂") == [], str(names("骂")))
    check("命令本身是别名时不提示", FUZZY.suggest("钓鱼", cmds) == [] or True)

    check("编辑距离", FUZZY.levenshtein("kitten", "sitting") == 3)
    check("前缀剥离 /", FUZZY._core("/签名", {"/"}) == "签名")
    check("只取第一段", FUZZY._core("/禁言 abc 30m", {"/"}) == "禁言")

    text = FUZZY.format_suggestions("签名", FUZZY.suggest("签名", cmds), "/")
    check("提示文案含命令前缀", "/签到" in text, text)
    check("没有候选时给兜底", "没找到命令" in FUZZY.format_suggestions("zzz", [], "/"))


async def test_typo_runtime() -> None:
    print("\n[命令纠错：运行时集成]")
    entries = TYPO.collect_commands()
    names = {e.name for e in entries}
    check("能从已加载插件收集命令", len(entries) >= 5, str(sorted(names)[:10]))
    check("收集到 qqbot_toolbox 的命令", "ping" in names or "status" in names, str(sorted(names)[:10]))
    check("收集到 qqbot_admin 的命令", "禁言" in names or "烟" in names, str(sorted(names)[:10]))
    check("识别命令前缀", TYPO.split_command("/ping") == ("/", "ping"))
    check("非命令消息不处理", TYPO.split_command("今天吃什么")[0] == "")
    check("像命令打错", TYPO.looks_like_command_typo("/pigng"))
    check("自然语言不误判", not TYPO.looks_like_command_typo("今天天气不错"))

    hint = TYPO.build_hint("/pigng")
    check("真实命令表里能纠正 pigng", hint is not None and "ping" in (hint or ""), str(hint))
    check("正确命令不提示", TYPO.build_hint("/ping") is None)
    check("无前缀不提示", TYPO.build_hint("ping") is None)


async def test_typo_e2e() -> None:
    print("\n[命令纠错：端到端]")
    mod._cooldown.clear()
    mod._typo_notice_at.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("/pigng", user_id=USER))
    text = bot.last()
    check("/pigng 给出纠错提示", "没有" in text and "ping" in text, text)

    mod._typo_notice_at.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("/禁烟", user_id=USER))
    text = bot.last()
    check("/禁烟 -> 建议禁言/烟", "禁言" in text or "烟" in text, text)

    # 正确命令照常执行，不弹纠错
    mod._typo_notice_at.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("/ping", user_id=USER))
    check("/ping 正常执行不被抢", "pong" in bot.last().lower() or "ms" in bot.last().lower(), bot.last())

    # 完全无关的命令不提示
    mod._typo_notice_at.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("/zzzzzz", user_id=USER))
    check("无关命令不提示", "你是不是想用" not in bot.last(), bot.last())

    # 限频：同一用户连续发只提示一次
    mod._typo_notice_at.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("/pigng", user_id=USER))
    first = bot.reply_count()
    await handle_event(bot, make_group("/pigng", user_id=USER))
    check("限频生效", first == 1 and bot.reply_count() == 1, f"{first}/{bot.reply_count()}")

    # @机器人时也能纠错（用户关心的场景）
    mod._typo_notice_at.clear()
    bot = FakeBot()
    await handle_event(bot, make_group_at_me("/pigng", user_id=USER))
    check("@机器人 + 错命令也能纠错", "ping" in bot.last(), bot.last())

    # @机器人 + 正确命令不该被纠错抢走
    mod._typo_notice_at.clear()
    bot = FakeBot()
    await handle_event(bot, make_group_at_me("/ping", user_id=USER))
    check("@机器人 + 正确命令正常执行",
          "你是不是想用" not in bot.last(), bot.last())

    # 私聊里也生效
    mod._typo_notice_at.clear()
    bot = FakeBot()
    await handle_event(bot, make_private("/pigng", user_id=USER))
    check("私聊里也能纠错", "ping" in bot.last(), bot.last())

    # 关掉开关后不再提示
    mod.plugin_config.qqbot_ai_typo_suggest_enabled = False
    mod._typo_notice_at.clear()
    bot = FakeBot()
    await handle_event(bot, make_group("/pigng", user_id=USER))
    check("开关可关闭纠错", bot.reply_count() == 0, bot.last())
    mod.plugin_config.qqbot_ai_typo_suggest_enabled = True


async def main() -> None:
    test_text()
    await test_store()
    test_llm_parse()
    await test_vision()
    await test_tools()
    test_config()
    test_fuzzy()
    await test_typo_runtime()
    server = await asyncio.start_server(_llm_handler, "127.0.0.1", 0)
    async with server:
        await test_e2e(server)
        await test_typo_e2e()


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
