"""鱼种数据与捕鱼算法。

数值量级对齐 docs/CURRENCY_SPEC.md §3：
  Drachma 单次产量 R 10~150 · SR 50~400 · SSR 450~999
主题取希腊航海神话，贴合 Erectheus（雅典国王、海神传说）背景。
"""

import random
from dataclasses import dataclass

# ---------------------------------------------------------------- 稀有度
RARITY_R = "R"
RARITY_SR = "SR"
RARITY_SSR = "SSR"

RARITY_LABEL = {
    RARITY_R: "【R】",
    RARITY_SR: "【SR】",
    RARITY_SSR: "【SSR】",
}
RARITY_COLOR = {
    RARITY_R: "⚪",
    RARITY_SR: "🔵",
    RARITY_SSR: "🟣",
}


@dataclass(frozen=True)
class Fish:
    name: str
    detail: str
    rarity: str
    drachma: int  # 基础渔获（Drachma）
    exp: int


# ---------------------------------------------------------------- 鱼种表
# 稀有度越高，drachma/exp 越高，基础权重越低。
FISHES: tuple[Fish, ...] = (
    # ---- R：常见，10~150 Drachma ----
    Fish("比雷埃夫斯沙鳀", "港城外海最常见的银白小鱼，成群游动时像撒了一把碎币。", RARITY_R, 10, 5),
    Fish("卫城岩虾虎", "吸附在古港防波堤石缝里，受惊时会弹出半尺远。", RARITY_R, 25, 8),
    Fish("藻海跳鱼", "能在退潮的滩涂上弹跳移动，胸鳍演化成了两条小腿。", RARITY_R, 45, 12),
    Fish("月光比目", "侧卧于沙底，背侧会随月相变换斑纹，渔民据此判断潮时。", RARITY_R, 80, 18),
    Fish("星火水母", "通体透明，受刺激时边缘迸出蓝色火花， children 称之为海里的萤火。", RARITY_R, 150, 30),
    # ---- SR：少见，50~400 Drachma ----
    Fish("三叉戟棘鲷", "背鳍棘刺形如三叉戟，相传是海神遗落人间的兵器碎片所化。", RARITY_SR, 50, 40),
    Fish("塞壬歌鲭", "洄游时会发出低频鸣响，老水手说那是女妖在唱诱航的歌。", RARITY_SR, 120, 60),
    Fish("金角海螺", "螺旋外壳镀着一层金，实则是一种共生藻的分泌物。", RARITY_SR, 220, 90),
    Fish("青铜锚蟹", "螯足坚硬如青铜，能夹断麻绳，港工既恨又敬。", RARITY_SR, 320, 130),
    Fish("阿波罗剑鱼", "吻部如长剑，破水时银光一闪，正午海面最难捕捉。", RARITY_SR, 400, 160),
    # ---- SSR：稀有，450~999 Drachma ----
    Fish("波塞冬三叉鲟", "体长及人，尾鳍三裂如神戟。捕获者需向海神献祭方敢返航。", RARITY_SSR, 450, 250),
    Fish("深渊美杜莎鳗", "通体覆满蛇形触手，直视其头部者会做三日噩梦。", RARITY_SSR, 620, 340),
    Fish("极光克拉托", "深海发光巨兽，躯体展开时整片海域如同升起极光。", RARITY_SSR, 800, 450),
    Fish("奥林匹斯圣鲑", "银鳞上天然带着山形纹路，渔民视其为神山倒影。", RARITY_SSR, 999, 600),
)

FISH_BY_RARITY: dict[str, list[Fish]] = {}
for _f in FISHES:
    FISH_BY_RARITY.setdefault(_f.rarity, []).append(_f)

# ---------------------------------------------------------------- 权重算法
# 基础权重（对齐规范 §3 的产能上限设计）
_BASE_WEIGHTS = {RARITY_R: 100.0, RARITY_SR: 30.0, RARITY_SSR: 10.0}

# 鱼竿等级对稀有度的加成系数
ROD_RARITY_BOOST_PER_LEVEL = 0.25
# 鱼饵对稀有度的加成系数
BAIT_RARITY_BOOST = 0.5


def rarity_weights(rod_level: int, use_bait: bool = False) -> dict[str, float]:
    """按鱼竿等级与是否用饵，计算各稀有度权重。

    鱼竿只提升稀有度权重，不直接加钱 —— 否则高等级会让 R 也变金矿，
    破坏规范的产能模型（R9：调产出必须同步评估回收）。
    """
    w = dict(_BASE_WEIGHTS)
    rod_boost = 1 + ROD_RARITY_BOOST_PER_LEVEL * max(0, rod_level - 1)
    for r in (RARITY_SR, RARITY_SSR):
        w[r] *= rod_boost
    if use_bait:
        for r in (RARITY_SR, RARITY_SSR):
            w[r] *= 1 + BAIT_RARITY_BOOST
    return w


def pick_fish(rod_level: int, use_bait: bool = False) -> Fish:
    """按权重抽一个稀有度，再在该稀有度内随机取一种鱼。"""
    w = rarity_weights(rod_level, use_bait)
    rarities = list(w.keys())
    rarity = random.choices(rarities, weights=[w[r] for r in rarities], k=1)[0]
    return random.choice(FISH_BY_RARITY[rarity])


def rarity_probs(rod_level: int, use_bait: bool = False) -> dict[str, float]:
    """各稀有度出现概率，用于面板展示。"""
    w = rarity_weights(rod_level, use_bait)
    total = sum(w.values())
    return {r: w[r] / total for r in w}
