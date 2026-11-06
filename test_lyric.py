"""qqbot_lyric 离线单测（不联网、不依赖 NoneBot 运行时）。

覆盖：
- 文本归一化 / 噪音过滤 / 片段拼接
- 本地版式闸门（歌词 vs 聊天 vs 台词）
- 韵脚判定
- 匹配（精确行命中 / 滑窗 / 候选歌 / 网易云兜底 / 阈值）
- 接龙引擎（模型判定、连续接窗口、冷却、粘性换歌、模型兜底歌名）
- 模型输出解析（宽容 JSON）

网络与模型全部用 ``FakeLibrary`` / ``FakeJudge`` 注入。
"""

import asyncio
import sys
import types
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent / "plugins"
sys.path.insert(0, str(PLUGIN_DIR))

# ------------------------------------------------------------------ 依赖打桩
# 插件包 __init__ 里有 NoneBot 装配代码，单测不导入它（见文件末的 ImportError 断言）。


def _stub(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


_quiet = types.SimpleNamespace(
    debug=lambda *a, **k: None,
    info=lambda *a, **k: None,
    warning=lambda *a, **k: None,
    error=lambda *a, **k: None,
    exception=lambda *a, **k: None,
)
# 依赖可能真的装了（开发机），也可能没有（最小环境）；缺的直接打桩
try:  # pragma: no cover
    import nonebot  # noqa: F401
except ImportError:  # pragma: no cover
    _stub("nonebot")
    _stub("nonebot.log", logger=_quiet)
try:  # pragma: no cover
    import httpx  # noqa: F401
except ImportError:  # pragma: no cover
    _stub("httpx", HTTPError=type("HTTPError", (Exception,), {}),
          AsyncClient=object, Timeout=object)
try:  # pragma: no cover
    import pypinyin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # rhyme 有兜底表，不装也能跑

# 用假包骗过插件 __init__（真正的装配代码需要 NoneBot 运行时，单测不加载）
_pkg = types.ModuleType("qqbot_lyric")
_pkg.__path__ = [str(Path(__file__).resolve().parent / "plugins" / "qqbot_lyric")]
sys.modules["qqbot_lyric"] = _pkg

from qqbot_lyric import gate, judge, library, relay, rhyme, search, state, text  # noqa: E402
from qqbot_lyric.config import Config  # noqa: E402


# ------------------------------------------------------------------ 假曲库
class FakeLibrary:
    """只实现 engine/search 用到的那几个方法。"""

    def __init__(self, songs: dict[str, list[str]], netease: dict[str, list[library.Song]] | None = None):
        self.index = library.LineIndex(10000)
        self.songs = {}
        for key, lines in songs.items():
            title, artist = key.split("|", 1)
            song = library.Song(id=title, title=title, artist=artist, has_lyrics=True)
            self.index.add_song(song, lines)
            self.songs[title] = song
        self.lines = {title: lines for title, lines in
                      ((k.split("|")[0], v) for k, v in songs.items())}
        self.netease = object() if netease else None
        self._netease = netease or {}
        self.search_calls = []

    async def ensure_lyrics(self, song):
        return self.lines.get(song.title)

    async def search_songs(self, q, limit=6):
        self.search_calls.append(q)
        return [s for t, s in self.songs.items() if t in q or q in t]

    async def netease_candidates(self, keywords):
        return self._netease.get(keywords, [])

    async def netease_lyric_lookup(self, keywords, text_norm=""):
        songs = self._netease.get(keywords, [])
        if not songs:
            return None
        song = songs[0]
        lines = self.lines.get(song.title) or []
        return (song, lines) if lines else None

    async def netease_title_candidates(self, phrase, text_norm=""):
        return [s for q, songs in self._netease.items() if phrase in q for s in songs]

    async def netease_lookup(self, keywords, text_norm=""):
        from qqbot_lyric.library import clean_title

        songs = self._netease.get(keywords, [])
        return clean_title(songs[0].title) if songs else ""

    async def find_by_name(self, title, artist=""):
        return self.songs.get(title)

    async def fetch_by_title(self, title):
        from qqbot_lyric.library import clean_title

        for name, song in self.songs.items():
            if clean_title(name) == clean_title(title):
                return [(song, self.lines.get(song.title) or [])]
        return []

    async def close(self):
        pass


class FakeJudge:
    """按文本返回预置判定。"""

    def __init__(self, mapping: dict[str, str], titles: dict[str, tuple[str, str]] | None = None):
        self.mapping = mapping
        self.titles = titles or {}
        self.calls = []

    async def judge_many(self, texts):
        self.calls.append(list(texts))
        out = []
        for idx, body in enumerate(texts):
            first = body.splitlines()[0] if body.splitlines() else body
            kind = "CHAT"
            for key, value in self.mapping.items():
                if key in body or key in first:
                    kind = value
                    break
            title, artist = self.titles.get(first, ("", ""))
            out.append(judge.Verdict(index=idx, kind=kind, title=title, artist=artist))
        return out


def make_config(**kwargs) -> Config:
    defaults = dict(
        qqbot_lyric_llm_batch_wait=0.01,
        qqbot_lyric_cooldown=0,
        qqbot_lyric_reply_mode="context",
    )
    defaults.update(kwargs)
    return Config(**defaults)


QINGTIAN = [
    "故事的小黄花",
    "从出生那年就飘着",
    "童年的荡秋千",
    "随记忆一直晃到现在",
    "从前从前有个人爱你很久",
    "但故事的最后你好像还是说了拜拜",
    "刮风这天我试过握着你手",
    "但偏偏雨渐渐大到我看你不见",
]


# ------------------------------------------------------------------ 测试
class TestText(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(text.normalize("我 听见 雨滴！"), "我听见雨滴")
        self.assertEqual(text.normalize("ＨＥＬＬＯ"), "hello")

    def test_clean_text_strips_decorations(self):
        self.assertEqual(text.clean_text("「从前从前有个人爱你很久」"), "从前从前有个人爱你很久")
        self.assertEqual(text.clean_text("—— 我听见雨滴 ☁️"), "我听见雨滴")

    def test_is_noise(self):
        self.assertTrue(text.is_noise("哈哈哈哈"))
        self.assertTrue(text.is_noise("/歌词 晴天"))
        self.assertTrue(text.is_noise("233333"))
        self.assertFalse(text.is_noise("我听见雨滴落在青青草地"))

    def test_build_candidates_merges_same_user_burst(self):
        frags = [
            text.Fragment(text="故事的小黄花", norm="故事的小黄花", user_id="1", ts=0),
            text.Fragment(text="从出生那年就飘着", norm="从出生那年就飘着", user_id="1", ts=3),
        ]
        combos = text.build_candidates(frags, max_segments=4, burst_window=12)
        # 只拼「同一个人的连续消息」；别人的消息另起一段，所以候选里没有它
        self.assertEqual([[f.text for f in c] for c in combos],
                         [["从出生那年就飘着"], ["故事的小黄花", "从出生那年就飘着"]])

    def test_build_candidates_stops_across_users(self):
        frags = [
            text.Fragment(text="A句歌词", norm="a句歌词", user_id="1", ts=0),
            text.Fragment(text="B句歌词", norm="b句歌词", user_id="2", ts=1),
        ]
        combos = text.build_candidates(frags, max_segments=4, burst_window=12)
        self.assertEqual(len(combos), 1)
        self.assertEqual([f.text for f in combos[0]], ["B句歌词"])


class TestEnglish(unittest.TestCase):
    """英文歌词：能接对，日常英文不打扰（不调模型 / 不接）。"""

    def shape(self, body: str):
        frag = text.Fragment(text=text.clean_text(body), norm=text.normalize(body), user_id="1", ts=0)
        return gate.analyze([frag])

    def test_english_detection(self):
        self.assertTrue(gate.is_english("if i had my life without you near me"))
        self.assertFalse(gate.is_english("我听见雨滴落在青青草地"))

    def test_english_lyric_like(self):
        for body in ("if i had my life without you near me", "say yes to heaven say yes to me",
                     "I love you", "The rain is falling on my window"):
            self.assertNotEqual(gate.route(self.shape(body)), "skip", body)

    def test_english_chat_skipped(self):
        for body in ("hello how are you today", "what time is the meeting tomorrow",
                     "see you tomorrow", "I will send you the report by tomorrow morning",
                     "lgtm", "thanks a lot", "ok sounds good"):
            self.assertEqual(gate.route(self.shape(body)), "skip", body)

    def test_credit_lines_not_lyrics(self):
        lrc = "[00:00.000] 编曲 : Robbie Buchanan\n[00:22.751]The days would all be empty\n[00:01]作词 : Gerry"
        self.assertEqual(library.parse_lrc(lrc), ["The days would all be empty"])


class TestGate(unittest.TestCase):
    def shape(self, body: str) -> gate.Shape:
        frag = text.Fragment(text=text.clean_text(body), norm=text.normalize(body), user_id="1", ts=0)
        return gate.analyze([frag])

    def test_lyric_like(self):
        for body in ("我听见雨滴落在青青草地", "从前从前有个人爱你很久", "爱你孤身走暗巷"):
            self.assertNotEqual(gate.route(self.shape(body)), "skip", body)

    def test_chat_like(self):
        for body in ("咱们几点开会啊", "这个月工资什么时候发", "你好"):
            self.assertEqual(gate.route(self.shape(body)), "skip", body)

    def test_long_text_skipped(self):
        body = "今天下午三点半在会议室开个会大家记得把材料准备好顺便带点吃的谢谢"
        self.assertEqual(gate.route(self.shape(body)), "skip")

    def test_multi_line_rhyme_bonus(self):
        a = self.shape("刮风这天我试过握着你手")
        b = gate.analyze([
            text.Fragment(text="刮风这天我试过握着你手", norm="刮风这天我试过握着你手", user_id="1", ts=0),
            text.Fragment(text="但偏偏雨渐渐大到我看你不见", norm="但偏偏雨渐渐大到我看你不见", user_id="1", ts=2),
        ])
        self.assertGreater(b.score, a.score - 0.2)
        self.assertIn("分行+", b.reasons)

    def test_explicit_bonus(self):
        plain = self.shape("明天记得带伞")
        explicit = gate.analyze(
            [text.Fragment(text="明天记得带伞", norm="明天记得带伞", user_id="1", ts=0)], explicit=True
        )
        self.assertGreater(explicit.score, plain.score)


class TestRhyme(unittest.TestCase):
    def test_rhyme_key(self):
        self.assertEqual(rhyme.rhyme_key("我听见雨滴落在青青草地"), rhyme.rhyme_key("我听见远方下课钟声响起"))
        self.assertEqual(rhyme.rhyme_key("Hello my love"), rhyme.rhyme_key("I can feel above"))

    def test_english_and_empty(self):
        self.assertTrue(rhyme.rhyme_key("When I am down"))
        self.assertEqual(rhyme.rhyme_key(""), "")


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.lib = FakeLibrary({
            "晴天|周杰伦": QINGTIAN,
            "小幸运|田馥甄": ["我听见雨滴落在青青草地", "我听见远方下课钟声响起", "可是我听见你的声音"],
            "后来|刘若英": ["后来我总算学会了如何去爱", "可惜你早已远去消失在人海"],
        })

    def run_search(self, body, **kwargs):
        frag = text.Fragment(text=text.clean_text(body), norm=text.normalize(body), user_id="1", ts=0)
        ctx = search.SearchContext.build([frag], **kwargs)
        return asyncio.run(search.search(self.lib, ctx, min_score=0.82))

    def test_exact_line_hit(self):
        hit = self.run_search("我听见雨滴落在青青草地")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.song.title, "小幸运")
        self.assertEqual(hit.next_line, "我听见远方下课钟声响起")

    def test_traditional_lyrics_match_simplified_input(self):
        """lrclib 的歌词多是繁体，群友打简体也要能接（繁简互通）。"""
        class TradLibrary(FakeLibrary):
            pass

        lib = TradLibrary({"晴天|周杰伦": [
            "故事的小黃花", "從出生那年就飄著", "童年的盪鞦韆", "隨記憶一直晃到現在",
            "從前從前有個人愛你很久", "但故事的最後你好像還是說了拜拜",
        ]})
        frag = text.Fragment(text="从前从前有个人爱你很久", norm=text.normalize("从前从前有个人爱你很久"), user_id="1", ts=0)
        hit = asyncio.run(search.search(lib, search.SearchContext.build([frag]), min_score=0.82))
        self.assertIsNotNone(hit)
        self.assertIn("拜拜", hit.next_line)

    def test_fuzzy_hit_with_typo(self):
        hit = self.run_search("从前从前有个人爱你很久")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.song.title, "晴天")
        self.assertEqual(hit.next_line, "但故事的最后你好像还是说了拜拜")

    def test_no_hit_for_chat(self):
        self.assertIsNone(self.run_search("今天食堂的菜好咸"))

    def test_candidate_hint_used(self):
        hit = self.run_search("后来总算学会了", hint_title="后来", hint_artist="刘若英")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.song.title, "后来")

    def test_netease_direct_lyrics(self):
        """lrclib 没收录的歌：网易云按内容搜到 + 直接给歌词，也要能接上。"""
        netease_song = library.Song(id="9", title="小幸运", artist="田馥甄", source="netease")
        self.lib = FakeLibrary({"小幸运|田馥甄": ["我听见雨滴落在青青草地", "我听见远方下课钟声响起"]},
                               netease={"我听见雨滴落在青青草地": [netease_song]})
        hit = self.run_search("我听见雨滴落在青青草地")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.next_line, "我听见远方下课钟声响起")

    def test_netease_fallback(self):
        netease_song = library.Song(id="99", title="小幸运", artist="田馥甄", source="netease")
        self.lib = FakeLibrary({"小幸运|田馥甄": ["我听见雨滴落在青青草地", "我听见远方下课钟声响起"]},
                               netease={"雨滴": [netease_song]})
        hit = self.run_search("我听见雨滴落在青青草地")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.song.title, "小幸运")


class TestParse(unittest.TestCase):
    def test_parse_batch_tolerates_code_fence(self):
        payload = '```json\n{"items":[{"i":0,"type":"LYRIC","title":"晴天","artist":"周杰伦"}]}\n```'
        out = judge.parse_batch(payload, 1)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].is_lyric)
        self.assertEqual(out[0].title, "晴天")

    def test_parse_batch_empty(self):
        self.assertEqual(judge.parse_batch("", 3), [])
        self.assertEqual(judge.parse_batch("我不知道", 3), [])

    def test_parse_batch_ignores_unknown_type(self):
        out = judge.parse_batch('{"items":[{"i":0,"type":"POEM"}]}', 1)
        self.assertEqual(out, [])


class TestEngine(unittest.TestCase):
    def build(self, mapping, titles=None, **cfg):
        lib = FakeLibrary({"晴天|周杰伦": QINGTIAN})
        jd = FakeJudge(mapping, titles)
        store = state.RelayStore()
        engine = relay.RelayEngine(make_config(**cfg), lib, jd, store)
        return engine, lib, jd, store

    def test_reply_next_line(self):
        engine, lib, jd, store = self.build({"从前从前": "LYRIC"}, {"从前从前有个人爱你很久": ("晴天", "周杰伦")})
        out = asyncio.run(engine.handle(1, "从前从前有个人爱你很久", "u1", ts=100.0))
        self.assertTrue(out.replied)
        self.assertEqual(out.hit.song.title, "晴天")
        self.assertEqual(out.hit.next_line, "但故事的最后你好像还是说了拜拜")
        rendered = relay.render(out.hit, "context")
        self.assertIn("但故事的最后你好像还是说了拜拜", rendered)
        self.assertIn("晴天", rendered)

    def test_chat_ignored(self):
        engine, *_ = self.build({"今天": "CHAT"})
        out = asyncio.run(engine.handle(1, "今天食堂的菜好咸", "u1", ts=100.0))
        self.assertFalse(out.replied)
        self.assertEqual(out.status, "chat")

    def test_short_message_skipped_without_llm(self):
        engine, _, jd, _ = self.build({"": "LYRIC"})
        out = asyncio.run(engine.handle(1, "你好", "u1", ts=100.0))
        self.assertEqual(out.status, "skip")
        self.assertEqual(jd.calls, [])

    def test_relay_window_continues_same_song(self):
        engine, *_ = self.build({"从前从前": "LYRIC"}, {"从前从前有个人爱你很久": ("晴天", "周杰伦")})
        first = asyncio.run(engine.handle(1, "从前从前有个人爱你很久", "u1", ts=100.0))
        self.assertTrue(first.replied)
        # 窗口期内别人接着唱同首歌的下一句，不需要模型再判定
        engine.judge.calls.clear()
        second = asyncio.run(engine.handle(1, "刮风这天我试过握着你手", "u2", ts=150.0))
        self.assertTrue(second.replied)
        self.assertEqual(second.hit.song.title, "晴天")
        self.assertGreaterEqual(second.hit.start, first.hit.end)
        self.assertEqual(engine.judge.calls, [])

    def test_cooldown_blocks_second_reply(self):
        """连续接窗口**外**：同一首歌刚接过，冷却期内不重复接（不刷屏）。"""
        engine, *_ = self.build({"从前从前": "LYRIC"}, qqbot_lyric_cooldown=60,
                                qqbot_lyric_relay_window=0)
        first = asyncio.run(engine.handle(1, "从前从前有个人爱你很久", "u1", ts=100.0))
        self.assertTrue(first.replied)
        second = asyncio.run(engine.handle(1, "故事的小黄花", "u1", ts=101.0))
        self.assertEqual(second.status, "cooldown")
        # 冷却期过了，同一首歌照样能接
        third = asyncio.run(engine.handle(1, "故事的小黄花", "u1", ts=200.0))
        self.assertTrue(third.replied, third)

    def test_japanese_multiline_lyric(self):
        """日文：两句歌词被网易云拆成两行，拼起来要能接上（2026-11-06 线上问题）。"""
        body = ["夜の空を饰る绮丽な花", "谁も知らない", "二人だけの夜",
                "待ち焦がれていた景色と重なる"]
        lib = FakeLibrary({"あの夢をなぞって|YOASOBI": body},
                          netease={"誰も知らない 二人だけの夜": [
                              library.Song(id="1417236632", title="あの夢をなぞって",
                                           artist="YOASOBI", source="netease")]})
        engine = relay.RelayEngine(make_config(qqbot_lyric_cooldown=0), lib,
                                   FakeJudge({}), state.RelayStore())
        out = asyncio.run(engine.handle(1, "誰も知らない 二人だけの夜", "u1", ts=100.0))
        self.assertTrue(out.replied, out)
        self.assertEqual(out.hit.next_line, "待ち焦がれていた景色と重なる")

    def test_japanese_partial_line_not_answered(self):
        """只对上半句（「誰も知らない」↔ 别首歌里的「知らない」）不算数，不能接错歌。"""
        lib = FakeLibrary({"知らない|安月名莉子": ["褒めてくれ 今も上手に笑えてるなら", "知らない",
                                                "not true not true"]})
        engine = relay.RelayEngine(make_config(qqbot_lyric_cooldown=0), lib,
                                   FakeJudge({}), state.RelayStore())
        out = asyncio.run(engine.handle(1, "誰も知らない", "u1", ts=100.0))
        self.assertFalse(out.replied, out)

    def test_chat_after_relay_not_answered(self):
        """接过龙之后，群里聊别的不能被拼成歌词接上去（「see you tomorrow」真是一句歌词）。"""
        engine, lib, jd, _ = self.build({}, qqbot_lyric_cooldown=0)
        # 走免验证路径（线上就是这样）：按内容搜歌 → 网易云返回这首歌
        lib.netease = object()
        lib._netease["刮风这天我试过握着你手"] = [library.Song(id="晴天", title="晴天", artist="周杰伦",
                                                           source="netease")]
        first = asyncio.run(engine.handle(1, "刮风这天我试过握着你手", "u1", ts=100.0))
        self.assertTrue(first.replied, first)
        # 「see you tomorrow」真是一句歌词（《syt=see you tomorrow》），
        # 但它自己版式不像歌词 → 不能拼上别人的闲聊之后被接。
        # （「这个bug修好了没」版式像歌词、又是本地索引里的整行，接龙窗口内会接，
        #  这是已知的取舍：宁可偶尔接错，也不能让对唱接不上。）
        for body in ("see you tomorrow",):
            out = asyncio.run(engine.handle(1, body, "u2", ts=104.0))
            self.assertFalse(out.replied, f"{body} 被误接：{out.hit.next_line if out.hit else ''}")

    def test_same_line_repeated_not_spammed(self):
        """窗口内把 bot 刚接过的那句又发一遍：不重复接（防刷屏）。

        但群友复读的是**bot 刚接的那句**（「Today's the day I grow some balls」）时，
        那是接着对唱，要往下接 —— 见 test_resung_full_line_continues。
        """
        engine, *_ = self.build({}, qqbot_lyric_cooldown=45)
        first = asyncio.run(engine.handle(1, "从前从前有个人爱你很久", "u1", ts=100.0))
        self.assertTrue(first.replied)
        # bot 接了「但故事的最后…」；再把它发一遍 = 刷屏
        again = asyncio.run(engine.handle(1, "从前从前有个人爱你很久", "u2", ts=103.0))
        self.assertEqual(again.status, "cooldown")

    def test_unmatched_reports_title(self):
        """曲库没有这首歌时只报歌名，不硬编下一句。"""
        engine, lib, *_ = self.build(
            {"少年": "LYRIC"},
            {"少年智则国智少年富则国富": ("少年中国说", "张杰")},
            qqbot_lyric_use_llm=True,
            qqbot_lyric_llm_fallback_score=1.0,  # 强制走模型兜底分支
        )
        # 版式分不高（保证会走模型分支），且曲库里没有这首歌
        out = asyncio.run(engine.handle(1, "少年智则国智少年富则国富", "u1", ts=100.0))
        self.assertEqual(out.status, "unmatched")
        self.assertEqual(out.title_hint, "少年中国说")
        self.assertIn("少年中国说", relay.render_title_only(out.title_hint, out.artist_hint))

    def test_group_not_locked_after_one_song(self):
        """接过一首歌之后，群里换一首歌也要能接（不能整群锁死在一首上）。"""
        lib = FakeLibrary({"晴天|周杰伦": QINGTIAN,
                           "小幸运|田馥甄": ["我听见雨滴落在青青草地", "我听见远方下课钟声响起"]})
        jd = FakeJudge({"从前从前": "LYRIC", "雨滴": "LYRIC"},
                       {"从前从前有个人爱你很久": ("晴天", "周杰伦"),
                        "我听见雨滴落在青青草地": ("小幸运", "田馥甄")})
        engine = relay.RelayEngine(make_config(), lib, jd, state.RelayStore())
        first = asyncio.run(engine.handle(1, "从前从前有个人爱你很久", "u1", ts=100.0))
        self.assertEqual(first.hit.song.title, "晴天")
        second = asyncio.run(engine.handle(1, "我听见雨滴落在青青草地", "u2", ts=110.0))
        self.assertTrue(second.replied)
        self.assertEqual(second.hit.song.title, "小幸运")

    def test_english_relay_without_llm(self):
        """英文整句：版式像歌词 + 曲库按内容搜到 → 不必等模型就能接。"""
        lines = ["If I had to live my life without you near me", "The days would all be empty",
                 "The nights would seem so long"]
        lib = FakeLibrary({})
        lib.netease = object()
        song = library.Song(id="1377130", title="Nothing's Gonna Change My Love for You",
                            artist="George Benson", source="netease", from_library=True)
        lib._netease = {"if i had my life without you near me": [song]}
        lib.lines["Nothing's Gonna Change My Love for You"] = lines
        lib.songs["Nothing's Gonna Change My Love for You"] = song
        jd = FakeJudge({})  # 模型一旦被调用就会返回 CHAT
        engine = relay.RelayEngine(make_config(), lib, jd, state.RelayStore())
        out = asyncio.run(engine.handle(1, "if i had my life without you near me", "u1", ts=100.0))
        self.assertTrue(out.replied, out)
        self.assertEqual(out.hit.next_line, "The days would all be empty")
        self.assertEqual(jd.calls, [])  # 没有调模型

    def test_context_reset_clears_fragments(self):
        """reset 必须连片段窗口一起清，否则同群连唱两首歌会串台。"""
        engine, *_ = self.build({"故事": "LYRIC"}, {"故事的小黄花": ("晴天", "周杰伦")})
        engine.add_fragment(1, "故事的小黄花", "u1", 100.0)
        self.assertTrue(engine._candidates(1))
        engine.reset_group(1)
        self.assertEqual(engine._candidates(1), [])
        self.assertEqual(engine._fragments, {})
        self.assertEqual(engine._meta, {})

    def test_quoted_fragments_are_joined(self):
        engine, *_ = self.build({"故事的小黄花": "LYRIC"}, {"故事的小黄花": ("晴天", "周杰伦")})
        frag = engine.add_fragment(1, "故事的小黄花", "u1", 100.0)
        combo = engine._candidates(1)
        self.assertTrue(combo)
        self.assertEqual([f.text for f in combo[-1]], ["故事的小黄花"])

    def test_render_plain_mode(self):
        lib = FakeLibrary({"晴天|周杰伦": QINGTIAN})
        hit = search.Hit(song=lib.songs["晴天"], score=1.0, lines=QINGTIAN, start=4, end=4)
        self.assertEqual(relay.render(hit, "plain"), "但故事的最后你好像还是说了拜拜")
        self.assertNotIn("晴天", relay.render(hit, "plain"))

    def test_render_default_has_no_song_name(self):
        """默认只接下一句，不带「（—— 歌名 - 歌手）」署名。"""
        lib = FakeLibrary({"晴天|周杰伦": QINGTIAN})
        hit = search.Hit(song=lib.songs["晴天"], score=1.0, lines=QINGTIAN, start=4, end=4)
        self.assertEqual(relay.render(hit), "但故事的最后你好像还是说了拜拜")
        self.assertNotIn("——", relay.render(hit))
        self.assertNotIn("周杰伦", relay.render(hit))
        self.assertIn("—— 晴天 - 周杰伦", relay.render(hit, "context"))

    def test_credit_line_never_replied(self):
        """群友把「—— 歌名 - 歌手」署名行单独发出来，不能被当成歌词接。"""
        engine, lib, jd, _ = self.build({})
        for body in ("（—— Thinking out Loud - Ed Sheeran）", "—— 晴天 - 周杰伦"):
            self.assertFalse(text.is_noise(body))  # 不是噪音，只是「不是歌词」
            out = asyncio.run(engine.handle(1, body, "u1", ts=100.0))
            self.assertFalse(out.replied, body)
        self.assertEqual(jd.calls, [])

    def test_credit_line_stripped_from_message(self):
        """「下一句 + 署名」整段复制回来时，署名行要剔掉、歌词照接。"""
        engine, lib, jd, _ = self.build({})
        body = "刮风这天我试过握着你手\n（—— 晴天 - 周杰伦）"
        out = asyncio.run(engine.handle(1, text.lyric_lines(body)[0], "u1", ts=100.0))
        self.assertTrue(out.replied, out)
        self.assertEqual(out.hit.song.title, "晴天")
        self.assertEqual(out.hit.next_line, "但偏偏雨渐渐大到我看你不见")


# Thinking Out Loud（网易云真实歌词：一句歌词被拆成三行）
TOL = [
    "When your legs don't work like they used to before",
    "And I can't sweep you off of your feet",
    "Will your mouth still remember the taste of my love",
    "Will your eyes still smile from your cheeks",
    "And darlin' I will",
    "Be lovin' you",
    "Till we're seventy",
    "Baby my heart",
    "Could still fall as hard",
    "At twenty three",
]


class TestRelayContinuation(unittest.TestCase):
    """连续对唱：一句一句轮流发，每一步都要接得上（2026-11-06 线上问题回归）。"""

    def build(self, lines=None, **cfg):
        body = lines or TOL
        # 按内容搜歌 → 网易云返回这首歌（线上就是这样：整句搜「Thinking Out Loud」）
        lib = FakeLibrary({"Thinking out Loud|Ed Sheeran": body},
                          netease={q: [library.Song(id="1", title="Thinking out Loud",
                                                    artist="Ed Sheeran", source="netease")]
                                   for q in (
                                       "daring I will, be loving you till we're 70",
                                       "Baby my heart",
                                       "Could still fall as hard",
                                       "And darlin' I will, Be lovin' you, Till we're seventy,",
                                   )})
        jd = FakeJudge({}, {})  # 模型一旦被调用就返回 CHAT：验证全程不需要模型
        store = state.RelayStore()
        cfg.setdefault("qqbot_lyric_cooldown", 0)
        engine = relay.RelayEngine(make_config(**cfg), lib, jd, store)
        return engine, lib, jd

    def test_partial_line_matches_multi_line(self):
        """用户把三行歌词写成一句（逗号分隔），也要能对上（0.82 阈值）。"""
        score, start, end = search.best_window(
            text.normalize("daring I will, be loving you till we're 70"), TOL, 3
        )
        self.assertGreaterEqual(score, Config().qqbot_lyric_min_score)
        self.assertEqual((start, end), (4, 6))

    def test_partial_window_requires_line_order(self):
        """输入各段在歌词里的行号必须递增，否则（拼了别人的话）不算命中。"""
        norm = "\n".join([text.normalize("Till we're seventy"), text.normalize("And darlin' I will")])
        score, start, end = search.best_window(norm, TOL, 3)
        self.assertLess(score, 0.82)

    def test_first_line_answered(self):
        engine, _, jd = self.build()
        out = asyncio.run(engine.handle(1, "daring I will, be loving you till we're 70", "u1", ts=100.0))
        self.assertTrue(out.replied, out)
        self.assertEqual(out.hit.song.title, "Thinking out Loud")
        self.assertEqual(out.hit.next_line, "Baby my heart")
        self.assertEqual(jd.calls, [])

    def test_continuous_singing_across_users(self):
        """A 唱一句 → bot 接 → B 接着唱 → bot 必须马上接（不能被冷却挡住）。"""
        engine, _, jd = self.build(qqbot_lyric_cooldown=45)
        first = asyncio.run(engine.handle(1, "daring I will, be loving you till we're 70", "u1", ts=100.0))
        self.assertTrue(first.replied, first)
        self.assertEqual(first.hit.next_line, "Baby my heart")
        # 5 秒后另一个人接着唱（同一个人自己接自己，也要能接）
        second = asyncio.run(engine.handle(1, "Baby my heart", "u2", ts=105.0))
        self.assertTrue(second.replied, second)
        self.assertEqual(second.hit.next_line, "Could still fall as hard")
        third = asyncio.run(engine.handle(1, "Could still fall as hard", "u1", ts=110.0))
        self.assertTrue(third.replied, third)
        self.assertEqual(third.hit.next_line, "At twenty three")
        self.assertEqual(jd.calls, [])

    def test_second_line_alone_continues(self):
        """第二句单独发（上一句是别人发的、bot 刚接过）也要接得上。"""
        engine, _, _ = self.build(qqbot_lyric_cooldown=45)
        first = asyncio.run(engine.handle(1, "daring I will, be loving you till we're 70", "u1", ts=100.0))
        self.assertTrue(first.replied, first)
        second = asyncio.run(engine.handle(1, "And darlin' I will, Be lovin' you, Till we're seventy,", "u2", ts=106.0))
        self.assertTrue(second.replied, second)
        self.assertEqual(second.hit.next_line, "Baby my heart")

    def test_same_song_repeat_still_cooled(self):
        """同一首歌里把 bot 接过的上一句再发一遍：仍然挡住（不刷屏）。"""
        engine, _, _ = self.build(qqbot_lyric_cooldown=45)
        first = asyncio.run(engine.handle(1, "Baby my heart", "u1", ts=100.0))
        self.assertTrue(first.replied, first)
        # bot 接了「Could still fall as hard」；把上一句「Baby my heart」再发一遍 = 刷屏
        again = asyncio.run(engine.handle(1, "Baby my heart", "u2", ts=101.0))
        self.assertEqual(again.status, "cooldown")

    def test_resung_full_line_continues(self):
        """群友把 bot 刚接的那句原样复读：那是接着对唱，要往下接（2026-11-06 线上）。"""
        engine, _, _ = self.build(qqbot_lyric_cooldown=45)
        first = asyncio.run(engine.handle(1, "Baby my heart", "u1", ts=100.0))
        self.assertTrue(first.replied, first)
        self.assertEqual(first.hit.next_line, "Could still fall as hard")
        again = asyncio.run(engine.handle(1, "Could still fall as hard", "u2", ts=101.0))
        self.assertTrue(again.replied, again)
        self.assertEqual(again.hit.next_line, "At twenty three")

    def test_chat_in_relay_window_not_matched(self):
        """接龙窗口内别人发的闲聊不能被接（2026-11-06 线上：「思劳牧慢速」被接成歌词）。

        窗口内没有模型复核，只靠版式 + 精确匹配；闲聊拼上一条上下文后
        会命中「上一句」，于是被当成歌词又接一遍。要求：当前这条自己不像歌词
        时不许拼上下文，命中区间还必须真的覆盖当前这条。
        """
        engine, _, _ = self.build(qqbot_lyric_cooldown=45)
        first = asyncio.run(engine.handle(1, "Baby my heart", "u1", ts=100.0))
        self.assertTrue(first.replied, first)
        self.assertEqual(first.hit.next_line, "Could still fall as hard")
        chat = asyncio.run(engine.handle(1, "思劳牧慢速", "u9", ts=105.0))
        self.assertFalse(chat.replied, chat)
        # 闲聊没被接之后，真正的下一句照样接得上（窗口没被打断）
        nxt = asyncio.run(engine.handle(1, "Could still fall as hard", "u9", ts=108.0))
        self.assertTrue(nxt.replied, nxt)
        self.assertEqual(nxt.hit.next_line, "At twenty three")

    def test_other_song_not_blocked_by_cooldown(self):
        """冷却只挡同一首歌：换一首歌接着唱不受影响。"""
        lib = FakeLibrary({"Thinking out Loud|Ed Sheeran": TOL,
                           "小幸运|田馥甄": ["我听见雨滴落在青青草地", "我听见远方下课钟声响起"]},
                          netease={"Baby my heart": [library.Song(id="1", title="Thinking out Loud",
                                                                  artist="Ed Sheeran", source="netease")]})
        engine = relay.RelayEngine(make_config(qqbot_lyric_cooldown=45), lib, FakeJudge({}), state.RelayStore())
        first = asyncio.run(engine.handle(1, "Baby my heart", "u1", ts=100.0))
        self.assertTrue(first.replied, first)
        second = asyncio.run(engine.handle(1, "我听见雨滴落在青青草地", "u2", ts=103.0))
        self.assertTrue(second.replied, second)
        self.assertEqual(second.hit.song.title, "小幸运")


if __name__ == "__main__":
    unittest.main(verbosity=2)
