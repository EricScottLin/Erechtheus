"""按群功能开关（群聊功能管理）。

每个群可以单独开关某功能，例如::

    @机器人 关闭本群复读
    @机器人 开启本群视频解析
    /关闭 复读
    /本群功能            # 查看全部开关

内置功能：

- ``poke``        戳一戳表情（qqbot_meme 读取）
- ``quotes``      金句（qqbot_meme 读取）
- ``meme``        表情包 /表情（qqbot_meme 读取）
- ``bili_video``  视频解析（桥接 nonebot_plugin_bilibili 的群开关）

``repeat``（复读）与 ``teach``（我说你说）由独立插件 ``qqbot_repeat`` 注册。

其他插件可以调用 ``register_feature(key, name, ...)`` 注册自己的功能，
然后在处理消息时用 ``is_enabled(group_id, key)`` 判断。
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from nonebot import get_driver, get_plugin, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg

from .permissions import check_sender_admin, finish_denial

try:
    from nonebot_plugin_localstore import get_plugin_data_dir

    DATA_DIR: Path = get_plugin_data_dir()
except Exception:  # pragma: no cover - localstore 缺失时退回当前目录
    DATA_DIR = Path.cwd() / "data" / "qqbot_group"

GetFn = Callable[[int], bool]
SetFn = Callable[[int, bool], None]


@dataclass
class Feature:
    key: str
    name: str
    default: bool = True
    aliases: tuple[str, ...] = ()
    getter: GetFn | None = None
    setter: SetFn | None = None


_REGISTRY: dict[str, Feature] = {}
_ORDER: list[str] = []
_external_checked = False


# ---------------------------------------------------------------- 存储
def state_file() -> Path:
    return DATA_DIR / "features.json"


def _load_state() -> dict:
    path = state_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_group] 读取功能开关失败：{e}")
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_state(state: dict) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- 注册
def register_feature(
    key: str,
    name: str,
    default: bool = True,
    aliases: tuple[str, ...] = (),
    getter: GetFn | None = None,
    setter: SetFn | None = None,
) -> None:
    """注册一个可按群开关的功能。"""
    if key in _REGISTRY:
        _REGISTRY[key] = Feature(key, name, default, aliases, getter, setter)
        return
    _REGISTRY[key] = Feature(key, name, default, aliases, getter, setter)
    _ORDER.append(key)
    logger.info(f"[qqbot_group] 已注册群功能：{name}（{key}）")


def _register_bilibili() -> bool:
    """桥接 B 站插件的按群开关（插件已加载才生效）。"""
    plugin = get_plugin("nonebot_plugin_bilibili")
    if plugin is None:
        return False
    module = plugin.module
    off_set = getattr(module, "_GROUP_OFF", None)
    save_state = getattr(module, "_save_state", None)
    if off_set is None or not callable(save_state):
        return False

    def _get(group_id: int) -> bool:
        return str(group_id) not in {str(x) for x in off_set}

    def _set(group_id: int, enabled: bool) -> None:
        if enabled:
            off_set.discard(str(group_id))
        else:
            off_set.add(str(group_id))
        save_state()

    register_feature(
        "bili_video",
        "视频解析",
        aliases=("B站解析", "bilibili", "视频"),
        getter=_get,
        setter=_set,
    )
    return True


def sync_external_features() -> None:
    """探测外部插件的功能（只做一次）。"""
    global _external_checked
    if _external_checked:
        return
    _external_checked = True
    try:
        _register_bilibili()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_group] 注册视频解析开关失败：{e}")


# 内置功能（repeat / teach 由 qqbot_repeat 插件注册）
register_feature("kick", "踢人", aliases=("踢", "kick", "移出群聊"))
register_feature("poke", "戳一戳表情", aliases=("戳一戳", "拍一拍", "戳一戳回图"))
register_feature("quotes", "金句", aliases=("金句库",))
register_feature("meme", "表情包", aliases=("表情", "/表情"))
register_feature("box", "盒（查QQ/资产）", default=False, aliases=("开盒", "查QQ", "查资产"))
register_feature(
    "selfmute", "禁言套餐/精致睡眠",
    default=False,
    aliases=("精致睡眠", "禁言套餐", "睡眠", "下午茶", "午睡", "牢饭", "自闭", "极致睡眠", "冬眠"),
)
register_feature(
    "greeting", "早安/晚安",
    default=False,
    aliases=("早安", "晚安", "作息", "睡眠记录"),
)
register_feature(
    "work", "上班打卡",
    default=False,
    aliases=("上班", "下班", "打卡", "工时"),
)


# ---------------------------------------------------------------- 读写
def is_enabled(group_id: int, key: str) -> bool:
    """查询某群某功能是否开启；未注册的功能视为开启。"""
    sync_external_features()
    feature = _REGISTRY.get(key)
    if feature is None:
        return True
    if feature.getter is not None:
        try:
            return bool(feature.getter(group_id))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_group] 读取功能 {key} 开关失败：{e}")
            return feature.default
    state = _load_state()
    return bool(state.get(str(group_id), {}).get(key, feature.default))


def set_enabled(group_id: int, key: str, enabled: bool) -> bool:
    """设置某群某功能开关；功能不存在返回 False。"""
    sync_external_features()
    feature = _REGISTRY.get(key)
    if feature is None:
        return False
    if feature.setter is not None:
        feature.setter(group_id, enabled)
        return True
    state = _load_state()
    state.setdefault(str(group_id), {})[key] = enabled
    _save_state(state)
    return True


def list_features(group_id: int) -> list[tuple[str, str, bool]]:
    """返回 ``[(key, name, enabled)]``。"""
    sync_external_features()
    return [(key, _REGISTRY[key].name, is_enabled(group_id, key)) for key in _ORDER]


def resolve_feature(text: str) -> Feature | None:
    """按 key / 中文名 / 别名 / 包含关系匹配功能。"""
    sync_external_features()
    raw = (text or "").strip().strip("。.!！")
    if raw.startswith("本群"):
        raw = raw[2:].strip()
    if not raw:
        return None
    low = raw.lower()
    for key in _ORDER:
        feature = _REGISTRY[key]
        names = {feature.key.lower(), feature.name.lower()}
        names |= {a.lower() for a in feature.aliases}
        if low in names:
            return feature
    for key in _ORDER:
        feature = _REGISTRY[key]
        if low in feature.name.lower() or feature.name.lower() in low:
            return feature
    return None


# ---------------------------------------------------------------- 命令
feature_cmd = on_command(
    "本群功能", aliases={"群功能", "功能开关"}, priority=5, block=True
)


@feature_cmd.handle()
async def handle_features(bot: Bot, event: GroupMessageEvent) -> None:
    """列出本群所有功能开关（所有人可看，只有管理员能改）。"""
    features = list_features(event.group_id)
    on = [name for _, name, enabled in features if enabled]
    off = [name for _, name, enabled in features if not enabled]

    lines = [f"⚙️ 本群功能开关（{event.group_id}）", "────────────"]
    for _, name, enabled in features:
        lines.append(f"{'✅' if enabled else '❌'} {name}")
    lines.append("────────────")
    lines.append(f"已开 {len(on)} / 共 {len(features)} 项")

    if check_sender_admin(bot, event) is None:
        lines.append("开关：/开启 <功能>、/关闭 <功能>（也可以说「关闭本群视频解析」）")
    else:
        lines.append("你是普通成员，只能查看；开关需要群主/群管操作。")
    await feature_cmd.finish("\n".join(lines))


# 消息里以「/开启 xxx」「/关闭 xxx」开头时才认，避免「开启本群视频解析」这类
# 自然语言误触；参数认不出来时仍然响应，并在 _do_toggle 里提示可用功能，
# 而不是静默无回复（用户会以为机器人坏了）。
_TOGGLE_HEAD_RE = re.compile(r"^/?(?:开启|启用|关闭|禁用)\s*(\S.*)?$")


async def _toggle_rule(event: MessageEvent, arg: Message = CommandArg()) -> bool:
    """是否响应这条开关命令。

    三种情况：

    1. 带命令前缀（``/开启 xxx``）——**一律响应**，参数写错也由 _do_toggle
       给出「没找到功能 / 可用功能列表」的提示，不能静默无回复；
    2. 不带前缀但能匹配到已注册功能（``@机器人 关闭本群复读``）——响应，
       这是文档里写明的自然语言用法；
    3. 不带前缀且匹配不到功能（``关闭本群电脑``）——不响应，避免吃掉日常聊天。

    注意不能只用 ``CommandArg`` 判参数：rule 阶段拿不到参数时它会判 False，
    正是 ``/开启 不存在的功能`` 静默的根源。
    """
    raw = event.get_message().extract_plain_text().strip()
    starts = get_driver().config.command_start or {"/"}
    if any(p and raw.startswith(p) for p in starts):
        return True
    return resolve_feature(arg.extract_plain_text()) is not None


async def _do_toggle(
    matcher, bot: Bot, event: GroupMessageEvent, enabled: bool, arg: Message
) -> None:
    denial = check_sender_admin(bot, event)
    if denial:
        await finish_denial(matcher, bot, event, denial)

    feature = resolve_feature(arg.extract_plain_text())
    if feature is None:
        shown = arg.extract_plain_text().strip()
        if not shown:
            names = "、".join(_REGISTRY[k].name for k in _ORDER)
            await matcher.finish(
                f"要{'开启' if enabled else '关闭'}哪个功能？可用的有：{names}\n"
                "例如：/开启 待办"
            )
        await matcher.finish(f"没找到功能「{shown}」，发 /本群功能 看看有哪些。")

    set_enabled(event.group_id, feature.key, enabled)
    flag = "✅ 已开启" if enabled else "❌ 已关闭"
    await matcher.finish(f"{flag}本群「{feature.name}」")


def _make_toggle(keyword: str, aliases: set[str], enabled: bool):
    matcher = on_command(
        keyword, aliases=aliases, rule=_toggle_rule, priority=5, block=True
    )

    @matcher.handle()
    async def _handler(
        bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
    ) -> None:
        await _do_toggle(matcher, bot, event, enabled, arg)

    return matcher


toggle_on = _make_toggle("开启", {"启用"}, True)
toggle_off = _make_toggle("关闭", {"禁用"}, False)
