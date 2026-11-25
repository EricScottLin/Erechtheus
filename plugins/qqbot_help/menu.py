"""帮助菜单的数据源：块 → 功能 → 命令。

只放「用户需要看到」的东西：功能名、一句话作用、主要命令、可选价格。
新增功能/命令时在这里加一条即可，总览图和详情图会同时更新 —— 以后加
跑图、发票、删票、解歌这类收费命令，只要在对应功能的 ``groups`` 里补
``Cmd("/xxx", "...", 价格)`` 就行。

约定：
- ``price`` 单位是月币，0 表示免费；
- ``switch`` 填 ``qqbot_group`` 的功能 key，总览会按本群开关过滤；
- ``gate`` 是额外的可见性判断（目前只有科技，需要先开通才出现）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cmd:
    """一条命令：展示写法 + 一句话说明 + 价格（月币，0 = 免费）。"""

    cmd: str
    desc: str = ""
    price: int = 0


@dataclass(frozen=True)
class Feature:
    """一个功能，也是 /help <功能名> 的一页。"""

    name: str
    block: str
    desc: str
    overview: tuple[str, ...] = ()          # 总览里只显示这几条主要命令
    groups: tuple[tuple[str, tuple[Cmd, ...]], ...] = ()  # 详情页的命令分组
    notes: tuple[str, ...] = ()             # 详情页提示（可为空）
    switch: str = ""                        # qqbot_group 的功能 key，空 = 常开
    gate: str = ""                          # "maimai_dx" = 需要开通才能看到
    aliases: tuple[str, ...] = ()           # /help <别名> 也能打开


PRICE_UNIT = "月币"

BLOCKS: tuple[tuple[str, str], ...] = (
    ("音游", "#a78bfa"),
    ("娱乐", "#f5a524"),
    ("工具与管理", "#38bdf8"),
)

BLOCK_COLORS = dict(BLOCKS)

FEATURES: tuple[Feature, ...] = (
    # ---------------------------------------------------------------- 音游
    Feature(
        name="舞萌",
        block="音游",
        desc="查看 Best 50、AP 50 与单曲成绩",
        overview=("/B50", "/AP50", "/INFO 曲名"),
        groups=(
            ("成绩", (
                Cmd("/B50", "Best 50 成绩图"),
                Cmd("/AP50", "AP 50 成绩图"),
            )),
            ("单曲", (
                Cmd("/INFO 曲名", "单曲成绩与定数"),
                Cmd("/GINFO 曲名", "单曲成绩（另一数据源）"),
            )),
        ),
        notes=("首次使用先用 /lxbind 或 /dfbind 绑定成绩源",),
        aliases=("maimai", "舞萌dx", "b50", "ap50"),
    ),
    Feature(
        name="DJMAX",
        block="音游",
        desc="DJMAX Respect V 查分，命令前缀 dj",
        overview=("/dj bind ID", "/dj b100 4", "/dj song 曲名"),
        groups=(
            ("账号", (
                Cmd("/dj bind 你的ID", "绑定 V-Archive 注册 ID（不是游戏 ID）"),
                Cmd("/dj unbind", "解绑当前 ID"),
            )),
            ("成绩", (
                Cmd("/dj b100 4", "B100 分表，键数 4 / 5 / 6 / 8"),
                Cmd("/dj list 4 15", "指定等级分表，sc15 查 SC 侧"),
                Cmd("/dj pack 4 MX VC10", "按曲包出分表"),
                Cmd("/dj new 4 MX", "新曲分表"),
                Cmd("/dj pp 4", "Perfect Play 分表"),
                Cmd("/dj max 4", "理论值 B100"),
                Cmd("/dj listdlc", "曲包代码列表"),
            )),
            ("单曲", (
                Cmd("/dj song 曲名", "曲目信息：全部键数的定数"),
                Cmd("/dj song 4B 曲名", "只看 4B"),
                Cmd("/dj info 4B 曲名", "你在该曲 4B 的成绩"),
            )),
        ),
        notes=(
            "歌曲可以写曲名、歌手或 songid",
            "未绑定时也可以：/dj <ID> info <曲名>",
            "已绑定后 ID 可省略，如 /dj b100 4",
        ),
        aliases=("dj", "djmax"),
    ),
    Feature(
        name="科技",
        block="音游",
        desc="生成最新成绩图，首次使用需先私聊绑定账号",
        overview=("私聊发二维码图", "/更新成绩", "/导出（仅私聊）"),
        groups=(
            ("首次使用", (
                Cmd("私聊发送二维码图", "完成绑定，自动登录", 100),
                Cmd("/大象信息", "查看个人资料与当前状态"),
                Cmd("/更新成绩", "生成最新成绩图，约 1 分钟", 10),
            )),
            ("其他", (
                Cmd("/导出（仅私聊）", "导出成绩文件，群聊不可用", 10),
                Cmd("/解绑", "解除当前绑定账号"),
                Cmd("/登出", "登出状态"),
            )),
        ),
        notes=("首次使用请先用私聊发送二维码图完成绑定",),
        switch="keji",
        gate="maimai_dx",
        aliases=("舞萌直连",),
    ),
    # ---------------------------------------------------------------- 娱乐
    Feature(
        name="猪猪",
        block="娱乐",
        desc="抽今天属于你的猪，还有卡面、斗猪和图鉴",
        overview=("/今日猪猪", "/小猪卡", "/斗猪"),
        groups=(
            ("每天", (
                Cmd("/今日猪猪", "抽今天属于你的猪"),
                Cmd("/小猪卡", "今天的 DX PASS 卡面"),
                Cmd("/猪猪成就", "图鉴成就与荣耀进度"),
            )),
            ("玩法", (
                Cmd("/斗猪", "带着猪去斗兽场，每日限次"),
                Cmd("/牧场", "看自己的牧场"),
                Cmd("/喂食", "喂猪提升好感"),
            )),
            ("表情", (
                Cmd("/猪猪表情 列表", "猪猪表情包总览"),
                Cmd("/猪猪表情 添加", "引一张图投稿（或先发图再发命令）"),
            )),
        ),
        aliases=("抽猪",),
    ),
    Feature(
        name="吃什么",
        block="娱乐",
        desc="随机推荐吃什么，可按菜系、店家、时段",
        overview=("/吃什么", "/吃什么 川菜"),
        groups=(
            ("用法", (
                Cmd("/吃什么", "全库随机（带图）"),
                Cmd("/吃什么 川菜", "按菜系"),
                Cmd("/吃什么 麦当劳", "按店家（文字菜单）"),
                Cmd("/吃什么 中午", "按时段：早上 / 中午 / 晚上 / 夜宵"),
                Cmd("/吃什么 列表", "看全部选项"),
            )),
        ),
        aliases=("吃啥",),
    ),
    Feature(
        name="喝什么",
        block="娱乐",
        desc="随机推荐喝什么，可按分类、品牌、时段",
        overview=("/喝什么", "/喝什么 奶茶"),
        groups=(
            ("用法", (
                Cmd("/喝什么", "全库随机"),
                Cmd("/喝什么 奶茶", "按分类"),
                Cmd("/喝什么 瑞幸咖啡", "按品牌"),
                Cmd("/喝什么 列表", "看全部分类"),
            )),
        ),
        aliases=("喝啥",),
    ),
    Feature(
        name="捕鱼",
        block="娱乐",
        desc="每日签到与捕鱼，赚月币",
        overview=("/签到", "/捕鱼"),
        groups=(
            ("玩法", (
                Cmd("/签到", "每日签到领月币"),
                Cmd("/捕鱼", "下网捕鱼，赚月币"),
            )),
        ),
        aliases=("签到", "钓鱼", "下网"),
    ),
    Feature(
        name="表情包",
        block="娱乐",
        desc="随机表情、投稿与表情库管理",
        overview=("/表情", "/表情添加"),
        groups=(
            ("命令", (
                Cmd("/表情", "随机来一张表情"),
                Cmd("/表情列表", "列出表情库"),
                Cmd("/表情添加", "引一张图，或发图后 10 分钟内添加"),
                Cmd("/表情删除 编号", "删除表情"),
            )),
        ),
        switch="meme",
        aliases=("表情",),
    ),
    Feature(
        name="金句",
        block="娱乐",
        desc="随机金句、添加与同步",
        overview=("/金句", "/金句添加"),
        groups=(
            ("命令", (
                Cmd("/金句", "随机来一句"),
                Cmd("/金句添加 内容", "添加金句"),
                Cmd("/金句同步", "同步金句库"),
            )),
        ),
        switch="quotes",
    ),
    Feature(
        name="梗图",
        block="娱乐",
        desc="整句说出触发词，就随机发一张梗图",
        overview=("/梗 列表", "/梗 添加 词"),
        groups=(
            ("命令", (
                Cmd("/梗", "看说明"),
                Cmd("/梗 列表", "看触发词"),
                Cmd("/梗 添加 词", "上传梗图（超级用户）"),
            )),
        ),
        switch="meme_reply",
        aliases=("梗",),
    ),
    Feature(
        name="复读",
        block="娱乐",
        desc="群友教过的话，被说出来时会复读",
        overview=("/复读列表", "/复读删除 词"),
        groups=(
            ("命令", (
                Cmd("/复读列表", "看已教学的词条"),
                Cmd("/复读删除 词", "删除词条"),
            )),
        ),
        switch="repeat",
        aliases=("复读机",),
    ),
    Feature(
        name="骂/吐槽",
        block="娱乐",
        desc="安全版嘴臭，对事不对人",
        overview=("/骂 @某人", "/吐槽 @某人"),
        groups=(
            ("命令", (
                Cmd("/骂 @某人", "吐槽一句"),
                Cmd("/吐槽 @某人", "同 /骂"),
            )),
        ),
        switch="roast",
        aliases=("骂", "吐槽", "锐评"),
    ),
    Feature(
        name="电猪",
        block="娱乐",
        desc="电一下某人，发一张电猪表情",
        overview=("/电 @某人",),
        groups=(
            ("命令", (
                Cmd("/电 @某人", "电一下"),
                Cmd("电 @某人", "不带 / 也可以"),
            )),
        ),
        switch="dian",
        aliases=("电", "电他"),
    ),
    Feature(
        name="精致睡眠",
        block="娱乐",
        desc="说一句触发词，自己进禁言睡一会",
        overview=("说「极致睡眠」", "说「午睡」"),
        groups=(
            ("触发词（直接说，不用斜杠）", (
                Cmd("极致睡眠", "睡到晚上"),
                Cmd("午睡", "午睡一会"),
                Cmd("冬眠", "长睡一觉"),
            )),
        ),
        notes=("只有本群开启后才能用；时长按触发词区分",),
        switch="selfmute",
        aliases=("禁言套餐", "极致睡眠", "睡眠"),
    ),
    Feature(
        name="早安/晚安",
        block="娱乐",
        desc="记录作息，早安晚安互相问候",
        overview=("说「早安」", "说「晚安」"),
        groups=(
            ("触发词（直接说，不用斜杠）", (
                Cmd("早安", "记录起床"),
                Cmd("晚安", "记录睡觉"),
            )),
        ),
        switch="greeting",
        aliases=("早安", "晚安", "作息"),
    ),
    Feature(
        name="上班打卡",
        block="娱乐",
        desc="上下班打卡，记录工时",
        overview=("说「上班」", "说「下班」"),
        groups=(
            ("触发词（直接说，不用斜杠）", (
                Cmd("上班", "开始计时"),
                Cmd("下班", "结束计时"),
            )),
        ),
        switch="work",
        aliases=("上班", "下班", "打卡"),
    ),
    Feature(
        name="盒（查QQ/资产）",
        block="娱乐",
        desc="查一个 QQ 号在本群的资产与信息",
        overview=("/开盒 QQ号",),
        groups=(
            ("命令", (
                Cmd("/开盒 QQ号", "查这个号的资产"),
            )),
        ),
        switch="box",
        aliases=("开盒", "查QQ", "查资产"),
    ),
    Feature(
        name="我说你说",
        block="娱乐",
        desc="教一句触发词，以后有人说了就复读",
        overview=("/我说列表", "/我说删除 词"),
        groups=(
            ("命令", (
                Cmd("/我说 触发词 你说", "教一句（引用图片可教图）"),
                Cmd("/我说列表", "看本群已教的词条"),
                Cmd("/我说删除 词", "删除词条"),
            )),
        ),
        switch="teach",
        aliases=("说我说", "教学", "复读教学"),
    ),
    # ---------------------------------------------------------------- 工具与管理
    Feature(
        name="待办",
        block="工具与管理",
        desc="待办清单（仅超级用户）",
        overview=("/待办",),
        groups=(
            ("命令", (
                Cmd("/待办", "看自己的待办"),
            )),
        ),
        switch="todo",
        aliases=("todo",),
    ),
    Feature(
        name="歌词接龙",
        block="工具与管理",
        desc="听到一句歌词，查是什么歌",
        overview=("/什么歌 歌词",),
        groups=(
            ("命令", (
                Cmd("/什么歌 歌词", "查这句歌词是什么歌"),
                Cmd("/什么歌", "看用法说明"),
            )),
        ),
        switch="lyric",
        aliases=("歌词", "什么歌", "接歌词"),
    ),
    Feature(
        name="视频解析",
        block="工具与管理",
        desc="B 站链接自动解析",
        overview=("/B站 链接",),
        groups=(
            ("命令", (
                Cmd("/B站 链接", "解析视频信息"),
                Cmd("直接发链接", "自动解析"),
            )),
        ),
        switch="bili_video",
        aliases=("b站", "bilibili", "bili", "视频"),
    ),
    Feature(
        name="小e",
        block="工具与管理",
        desc="群里的 AI 助手，@ 或直接叫小e",
        overview=("/小e 问题",),
        groups=(
            ("命令", (
                Cmd("/小e 问题", "问一句"),
                Cmd("@小e 问题", "在群里 @ 它也能问"),
            )),
        ),
        switch="ai",
        aliases=("ai", "AI", "问答"),
    ),
    Feature(
        name="踢人",
        block="工具与管理",
        desc="把某人移出群（需管理员）",
        overview=("/踢 @某人",),
        groups=(
            ("命令", (
                Cmd("/踢 @某人", "移出群聊"),
            )),
        ),
        switch="kick",
        aliases=("踢", "kick", "移出群聊"),
    ),
    Feature(
        name="抽烟（随机禁言）",
        block="工具与管理",
        desc="随机时长禁言（需管理员）",
        overview=("/烟 @某人",),
        groups=(
            ("命令", (
                Cmd("/烟 @某人", "随机禁言 1 分钟 ~ 8 小时"),
                Cmd("/烟（回复消息）", "对回复的那个人使用"),
            )),
        ),
        notes=("等价于 /禁言 但不指定时长",),
        switch="smoke",
        aliases=("烟", "抽烟", "吸烟", "来一根"),
    ),
    Feature(
        name="禁言",
        block="工具与管理",
        desc="指定时长禁言与解禁（需管理员）",
        overview=("/禁言 @某人 10m", "/解禁 @某人"),
        groups=(
            ("命令", (
                Cmd("/禁言 @某人 10m", "指定时长禁言"),
                Cmd("/解禁 @某人", "解除禁言"),
            )),
        ),
        aliases=("ban", "解禁"),
    ),
    Feature(
        name="帮助",
        block="工具与管理",
        desc="命令总览与单个功能的全部命令",
        overview=("/help", "/help 功能名"),
        groups=(
            ("命令", (
                Cmd("/help", "命令总览"),
                Cmd("/help 功能名", "看某个功能的全部命令"),
            )),
        ),
        aliases=("help", "菜单", "命令"),
    ),
)

# 功能名 → Feature，以及命令名 → Feature，供 /help 查询
_BY_NAME: dict[str, Feature] = {}
_BY_COMMAND: dict[str, Feature] = {}


def command_key(cmd: str) -> str:
    """命令写法 → 查表用的命令名。

    ``/导出（仅私聊）`` → ``导出``；``/dj bind 你的ID`` → ``dj``；
    ``私聊发送二维码图`` 这种不是命令的说明行原样返回。
    """
    text = str(cmd or "").strip()
    if text.startswith("/") or text.startswith("／"):
        text = text[1:].strip()
    head = text.split()[0] if text.split() else ""
    for sep in ("（", "("):
        head = head.split(sep)[0]
    return head.strip().lower()


def _index() -> None:
    if _BY_NAME:
        return
    for feature in FEATURES:
        _BY_NAME[feature.name.lower()] = feature
        for alias in feature.aliases:
            _BY_NAME.setdefault(str(alias).lower(), feature)
        for _, cmds in feature.groups:
            for cmd in cmds:
                key = command_key(cmd.cmd)
                if key:
                    _BY_COMMAND.setdefault(key, feature)


def find_feature(word: str) -> Feature | None:
    """按功能名 / 别名找功能。"""
    _index()
    return _BY_NAME.get((word or "").strip().lstrip("/").lower())


def find_by_command(word: str) -> Feature | None:
    """按命令写法找所属功能（``/help 导出`` → 科技）。"""
    _index()
    key = (word or "").strip().lstrip("/／").lower()
    if not key:
        return None
    hit = _BY_COMMAND.get(key) or _BY_COMMAND.get(command_key(word))
    if hit is not None:
        return hit
    # 「/吃什么 川菜」这类带参数的命令，退回首个词再找一次
    head = key.split()[0]
    return _BY_COMMAND.get(head)


def features_of(block: str) -> list[Feature]:
    return [f for f in FEATURES if f.block == block]
