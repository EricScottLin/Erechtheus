"""歌词接龙插件配置（前缀 ``qqbot_lyric_``）。

示例（.env.prod）::

    # 总开关 + 接龙手感
    qqbot_lyric_enabled=true
    qqbot_lyric_auto_reply=true          # false = 只识别不接龙（测试用）
    qqbot_lyric_reply_mode=plain         # plain=只接下一句（默认）；context=再括注歌名
    qqbot_lyric_cooldown=45              # 同一群同一首歌两次接龙最小间隔（秒）
    qqbot_lyric_relay_window=120         # 接龙后的「连续接」窗口（秒）
    qqbot_lyric_same_song_ttl=900        # 记住当前正在接的歌多久（在窗口外也粘）
    qqbot_lyric_cross_user_window=90     # 连续接时，上一条别人的消息也能拼进来（秒）

    # 曲库（lrclib 为主，网易云搜索兜底）
    qqbot_lyric_source=lrclib,netease
    qqbot_lyric_netease_fallback=true
    qqbot_lyric_lrclib_base=https://lrclib.net
    qqbot_lyric_timeout_seconds=6
    qqbot_lyric_max_song_lines=400

    # 判定（默认复用 qqbot_ai 的模型配置）
    qqbot_lyric_use_llm=true             # 关掉则只用本地版式判断
    qqbot_lyric_llm_api_base=            # 留空 = 沿用 qqbot_ai_api_base
    qqbot_lyric_llm_api_key=             # 留空 = 沿用 qqbot_ai_api_key
    qqbot_lyric_llm_model=               # 留空 = 沿用 qqbot_ai_model
    qqbot_lyric_llm_timeout_seconds=15
    qqbot_lyric_llm_max_input_chars=200
    qqbot_lyric_llm_min_chars=5
    qqbot_lyric_llm_max_chars=60

    # 匹配阈值
    qqbot_lyric_min_score=0.82
    qqbot_lyric_hint_bonus=0.12          # 模型给出歌名线索时对该歌的加分
"""

from typing import Any

from nonebot import get_plugin_config
from pydantic import BaseModel, Field, field_validator

SOURCES = ("lrclib", "netease")
REPLY_MODES = ("context", "plain")


def _to_list(value: Any) -> list[str]:
    """把 ``"a,b"`` / ``'["a"]'`` 统一成 ``list[str]``。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            import json

            try:
                value = json.loads(text)
            except ValueError:
                value = text
        else:
            value = text
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).replace("，", ",").split(",") if p.strip()]


class Config(BaseModel):
    """歌词接龙配置。"""

    # ---- 总开关 ----
    qqbot_lyric_enabled: bool = Field(
        default=True, description="插件总开关（按群开关之外的总闸）"
    )
    qqbot_lyric_auto_reply: bool = Field(
        default=True, description="识别到歌词后是否自动接下一句"
    )
    qqbot_lyric_reply_mode: str = Field(
        default="plain",
        description="回复样式：plain=只接下一句（默认，像群友对唱）；context=再接一行「—— 歌名 - 歌手」",
    )
    qqbot_lyric_cooldown: int = Field(
        default=45, ge=0, le=3600,
        description=(
            "同一首歌两次接龙的最小间隔（秒）。只挡「同一首」：换歌/对唱接着唱不受影响"
        ),
    )
    qqbot_lyric_relay_window: int = Field(
        default=120, ge=0, le=3600,
        description="bot 接过之后的「连续接」窗口（秒）；窗口内别人接着唱会继续接",
    )
    qqbot_lyric_same_song_ttl: int = Field(
        default=900, ge=0, le=24 * 3600,
        description="记住「当前正在接的歌」的时长（秒），避免轻易换歌",
    )
    qqbot_lyric_attachment_window: int = Field(
        default=20, ge=0, le=600,
        description="最近几分钟的零散消息也可以作为歌词片段候选（整段接不上时兜底用）",
    )
    qqbot_lyric_burst_window: int = Field(
        default=12, ge=1, le=120,
        description="同一个人连续发、间隔不超过该秒数的消息视为同一段歌词",
    )
    qqbot_lyric_max_segments: int = Field(
        default=4, ge=1, le=8,
        description="一段歌词最多由几条消息拼成",
    )
    qqbot_lyric_cross_user_window: int = Field(
        default=90, ge=0, le=600,
        description=(
            "连续接龙时，上一条**别人**发的消息也能拼进候选的秒数（0=不拼）。"
            "群友一句一句轮流对唱时，拼上上一条才定位得到「下一句」"
        ),
    )

    # ---- 曲库 ----
    qqbot_lyric_source: list[str] = Field(
        default_factory=lambda: list(SOURCES),
        description="曲库来源顺序，如 lrclib,netease",
    )
    qqbot_lyric_netease_fallback: bool = Field(
        default=True, description="lrclib 没收录时用网易云搜索补歌名歌手"
    )
    qqbot_lyric_lrclib_base: str = Field(
        default="https://lrclib.net", description="lrclib 基地址（可换镜像）"
    )
    qqbot_lyric_timeout_seconds: float = Field(
        default=6.0, ge=2.0, le=30.0, description="单次曲库请求超时（秒）"
    )
    qqbot_lyric_max_song_lines: int = Field(
        default=400, ge=10, le=2000, description="单首歌最多取多少行歌词"
    )
    qqbot_lyric_cache_songs: int = Field(
        default=500, ge=0, le=5000, description="进程内缓存多少首歌的歌词"
    )
    qqbot_lyric_cache_line_index: int = Field(
        default=200000, ge=1000, le=2000000,
        description="本地歌词行索引最多保存多少行（总量，超了淘汰最久未用的）",
    )

    # ---- 判定（LLM，默认不用）----
    qqbot_lyric_use_llm: bool = Field(
        default=False,
        description=(
            "是否用模型判断「像不像歌词」。默认关闭：判定靠本地版式 + 曲库证据，"
            "实测已足够（常见歌词 12/12 接对、48 句闲聊 0 误接）；打开后只在版式分极低时兜底"
        ),
    )
    qqbot_lyric_llm_fallback_score: float = Field(
        default=0.3, ge=0.0, le=1.0,
        description="本地版式分低于该值且免验证路径也没找到证据时是否问模型；0 = 永不问",
    )
    qqbot_lyric_llm_api_base: str = Field(default="", description="模型接口；留空沿用 qqbot_ai")
    qqbot_lyric_llm_api_key: str = Field(default="", description="模型 Key；留空沿用 qqbot_ai")
    qqbot_lyric_llm_model: str = Field(default="", description="模型名；留空沿用 qqbot_ai")
    qqbot_lyric_llm_timeout_seconds: float = Field(
        default=15.0, ge=3.0, le=120.0, description="模型请求超时（秒）"
    )
    qqbot_lyric_llm_max_tokens: int = Field(
        default=800, ge=64, le=4096, description="模型输出 token 上限"
    )
    qqbot_lyric_llm_min_chars: int = Field(
        default=5, ge=2, le=50, description="短于该字数的消息不调模型"
    )
    qqbot_lyric_llm_max_chars: int = Field(
        default=60, ge=10, le=200, description="长于该字数的消息不调模型（不像歌词，且会拖慢）"
    )
    qqbot_lyric_llm_batch_size: int = Field(
        default=6, ge=1, le=20, description="一次最多判定几条消息"
    )
    qqbot_lyric_llm_batch_wait: float = Field(
        default=1.5, ge=0.0, le=10.0, description="攒批等待时间（秒，0=不等待），群聊连发时合并成一次请求"
    )

    # ---- 免验证（明显像歌词时先用「按内容搜歌」当证据，省一次模型调用）----
    qqbot_lyric_verify_without_llm: bool = Field(
        default=True,
        description=(
            "版式明显像歌词时，先用「按内容搜歌」找证据：搜不到就当聊天（不调模型），"
            "搜到并经曲库逐行比对才接。中英文都适用"
        ),
    )
    qqbot_lyric_quick_verify_timeout: float = Field(
        default=4.0, ge=1.0, le=20.0,
        description="免验证路径的超时（秒）；超时则交给模型判定，不当作「不是歌词」",
    )

    # ---- 匹配 ----
    qqbot_lyric_min_score: float = Field(
        default=0.78, ge=0.5, le=1.0,
        description=(
            "片段与歌词行的最低相似度。0.78 是实测校准值：群友把歌词库里的三行"
            "写成一句（「daring I will, be loving you till we're 70」）时得分约 0.79，"
            "0.82 会漏接；日常闲聊的得分远低于此"
        ),
    )
    qqbot_lyric_hint_bonus: float = Field(
        default=0.12, ge=0.0, le=0.4, description="模型给出歌名/歌手线索时对该歌的加分"
    )

    @field_validator("qqbot_lyric_source", mode="before")
    @classmethod
    def _parse_source(cls, value: Any) -> Any:
        items = [s.lower() for s in _to_list(value)]
        items = [s for s in items if s in SOURCES]
        return items or list(SOURCES)

    @field_validator("qqbot_lyric_reply_mode")
    @classmethod
    def _check_reply_mode(cls, value: str) -> str:
        mode = (value or "plain").strip().lower()
        if mode not in REPLY_MODES:
            raise ValueError(f"qqbot_lyric_reply_mode 只能是 {sorted(REPLY_MODES)}")
        return mode


def _load_config() -> Config:
    """从 NoneBot 全局配置读取；未初始化（单测）时用默认值。"""
    try:
        return get_plugin_config(Config)
    except Exception:  # noqa: BLE001 —— NoneBot 未初始化
        return Config()


plugin_config: Config = _load_config()
