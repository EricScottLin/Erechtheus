"""节日日历 —— 节假日清单与荣耀倍率档位（本地记录，代码即数据）。

分类（CATS）：
  传统    中国农历/节气节日（春节、除夕、元宵、清明、端午、七夕、中元、中秋、
          重阳、腊八、小年）—— ×10，除夕 ×20
  法定    国务院放假安排里的每一天（元旦、春节、清明、劳动节、端午、中秋、国庆）
          —— ×10，国庆首日 ×20
  西方    情人节、愚人节、万圣夜、感恩节、黑色星期五、平安夜、圣诞节、复活节……
          —— ×5（平安夜按传统档 ×10）
  公益    纪念日/公益日（地球日、世界读书日、世界环境日、联合国日……）—— ×5
  互联网  新兴/造节（1024 程序员日、π Day、520、618、世界表情包日、双11、双12、
          88 会员节、女生节、系统管理员日）—— ×5

档位（由高到低）：
  ×20  红日   国庆首日（10-01）、除夕
  ×10  大节   传统节日日，以及法定假期里的每一天
  ×5   小节   西方节日 / 公益纪念日 / 互联网造节
  ×2   周末   周六周日（倍率见 multipliers 规则里的 weekend）

撞节规则：
  同一天有多个节日 → **倍率相加**（2026-04-05 清明 ×10 + 复活节 ×5 = ×15）；
  同一个节日被登记两次（法定假期表 + 农历表）→ 只算一次，取高；
  红日替掉同一个假期的普通档（除夕 ×20 替代春节 ×10，不是 ×200）。

查找优先级（multipliers.for_date）：
  自定义日期  >  自定义区间  >  本节日日历  >  周末

维护说明：
  * 公历固定节日放 SOLAR_FIXED（带分类）；
  * "某月第 N 个周几"放 NTH_WEEKDAY，"某月最后一个周几"放 LAST_WEEKDAY；
  * 复活节按 Computus 现算，黑色星期五 = 感恩节次日；
  * 农历节日没法用公式推，按年抄进 YEARLY（数据来源：lunardate）；
  * 法定假期按年抄进 STATUTORY（来源：国务院办公厅年度放假通知）。
    2027 及以后的通知一般在前一年 11 月发布，届时补一段即可。
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

# ---------------------------------------------------------------- 分类与档位

CAT_TRAD = "传统"
CAT_STAT = "法定"
CAT_WEST = "西方"
CAT_PUBLIC = "公益"
CAT_NET = "互联网"

CATS = [CAT_TRAD, CAT_STAT, CAT_WEST, CAT_PUBLIC, CAT_NET]

TIER_20 = 20.0
TIER_10 = 10.0
TIER_5 = 5.0

TIER_NAMES = {20.0: "红日", 10.0: "大节", 5.0: "小节", 2.0: "周末"}

MAX_STACK = 60.0  # 撞节累加上限，防手滑


def tier_name(mult: float) -> str:
    mult = float(mult)
    if mult == 2.0:
        return "周末"
    if mult >= 20:
        return "红日"
    if mult >= 10:
        return "大节"
    if mult >= 5:
        return "小节"
    if mult > 1:
        return "活动"
    return ""


# ---------------------------------------------------------------- 公历固定节日
# (月, 日, 名称, 倍率, 分类)
SOLAR_FIXED: list[tuple[int, int, str, float, str]] = [
    (1, 1, "元旦", TIER_10, CAT_STAT),
    # ---- 西方 ----
    (2, 14, "情人节", TIER_5, CAT_WEST),
    (3, 14, "白色情人节", TIER_5, CAT_WEST),
    (4, 1, "愚人节", TIER_5, CAT_WEST),
    (6, 28, "骄傲日", TIER_5, CAT_WEST),
    (10, 31, "万圣夜", TIER_5, CAT_WEST),
    (12, 24, "平安夜", TIER_10, CAT_WEST),
    (12, 25, "圣诞节", TIER_5, CAT_WEST),
    (12, 31, "跨年夜", TIER_5, CAT_WEST),
    # ---- 公益 / 纪念日 ----
    (3, 8, "妇女节", TIER_5, CAT_PUBLIC),
    (3, 12, "植树节", TIER_5, CAT_PUBLIC),
    (3, 15, "消费者权益日", TIER_5, CAT_PUBLIC),
    (3, 22, "世界水日", TIER_5, CAT_PUBLIC),
    (4, 7, "世界卫生日", TIER_5, CAT_PUBLIC),
    (4, 22, "地球日", TIER_5, CAT_PUBLIC),
    (4, 23, "世界读书日", TIER_5, CAT_PUBLIC),
    (4, 26, "世界知识产权日", TIER_5, CAT_PUBLIC),
    (5, 4, "青年节", TIER_5, CAT_PUBLIC),
    (5, 12, "护士节", TIER_5, CAT_PUBLIC),
    (5, 31, "世界无烟日", TIER_5, CAT_PUBLIC),
    (6, 1, "儿童节", TIER_5, CAT_PUBLIC),
    (6, 5, "世界环境日", TIER_5, CAT_PUBLIC),
    (6, 8, "世界海洋日", TIER_5, CAT_PUBLIC),
    (6, 14, "世界献血日", TIER_5, CAT_PUBLIC),
    (6, 26, "国际禁毒日", TIER_5, CAT_PUBLIC),
    (7, 1, "建党节", TIER_5, CAT_PUBLIC),
    (8, 1, "建军节", TIER_5, CAT_PUBLIC),
    (9, 10, "教师节", TIER_5, CAT_PUBLIC),
    (9, 21, "国际和平日", TIER_5, CAT_PUBLIC),
    (10, 4, "世界动物日", TIER_5, CAT_PUBLIC),
    (10, 16, "世界粮食日", TIER_5, CAT_PUBLIC),
    (10, 24, "联合国日", TIER_5, CAT_PUBLIC),
    (11, 8, "记者节", TIER_5, CAT_PUBLIC),
    (12, 1, "世界艾滋病日", TIER_5, CAT_PUBLIC),
    (12, 3, "国际残疾人日", TIER_5, CAT_PUBLIC),
    (12, 4, "国家宪法日", TIER_5, CAT_PUBLIC),
    (12, 5, "国际志愿者日", TIER_5, CAT_PUBLIC),
    # ---- 互联网 / 造节 ----
    (3, 7, "女生节", TIER_5, CAT_NET),
    (3, 14, "π Day", TIER_5, CAT_NET),
    (5, 20, "520", TIER_5, CAT_NET),
    (6, 18, "618", TIER_5, CAT_NET),
    (7, 17, "世界表情包日", TIER_5, CAT_NET),
    (8, 8, "88会员节", TIER_5, CAT_NET),
    (10, 24, "程序员日", TIER_5, CAT_NET),  # 1024 = 2^10，中国程序员节
    (11, 11, "双11", TIER_5, CAT_NET),
    (12, 12, "双12", TIER_5, CAT_NET),
    # ---- 法定（国庆当天给基础档，首日由 RED_DAYS 抬到 20）----
    (5, 1, "劳动节", TIER_10, CAT_STAT),
    (10, 1, "国庆节", TIER_10, CAT_STAT),
]

# (月, 周几 0=周一, 第几个, 名称, 倍率, 分类)
NTH_WEEKDAY: list[tuple[int, int, int, str, float, str]] = [
    (5, 6, 2, "母亲节", TIER_5, CAT_WEST),
    (6, 6, 3, "父亲节", TIER_5, CAT_WEST),
    (11, 3, 4, "感恩节", TIER_5, CAT_WEST),
]

# (月, 周几 0=周一, 名称, 倍率, 分类) —— 某月最后一个周几
LAST_WEEKDAY: list[tuple[int, int, str, float, str]] = [
    (7, 4, "系统管理员日", TIER_5, CAT_NET),
]

# ---------------------------------------------------------------- 农历 / 节气（按年抄）
# (日期, 名称, 倍率)
YEARLY: dict[int, list[tuple[str, str, float]]] = {
    2026: [
        ("2026-02-16", "除夕", TIER_20),
        ("2026-02-17", "春节", TIER_10),
        ("2026-03-03", "元宵", TIER_10),
        ("2026-04-05", "清明", TIER_10),
        ("2026-06-19", "端午", TIER_10),
        ("2026-08-19", "七夕", TIER_10),
        ("2026-08-27", "中元", TIER_10),
        ("2026-09-25", "中秋", TIER_10),
        ("2026-10-18", "重阳", TIER_10),
        ("2027-01-15", "腊八", TIER_10),
        ("2027-01-30", "小年", TIER_10),
    ],
    2027: [
        ("2027-02-05", "除夕", TIER_20),
        ("2027-02-06", "春节", TIER_10),
        ("2027-02-20", "元宵", TIER_10),
        ("2027-04-05", "清明", TIER_10),
        ("2027-06-09", "端午", TIER_10),
        ("2027-08-08", "七夕", TIER_10),
        ("2027-08-16", "中元", TIER_10),
        ("2027-09-15", "中秋", TIER_10),
        ("2027-10-08", "重阳", TIER_10),
        ("2028-01-04", "腊八", TIER_10),
        ("2028-01-19", "小年", TIER_10),
    ],
    2028: [
        ("2028-01-25", "除夕", TIER_20),
        ("2028-01-26", "春节", TIER_10),
        ("2028-02-09", "元宵", TIER_10),
        ("2028-04-04", "清明", TIER_10),
        ("2028-05-28", "端午", TIER_10),
        ("2028-08-26", "七夕", TIER_10),
        ("2028-09-03", "中元", TIER_10),
        ("2028-10-03", "中秋", TIER_10),
        ("2028-10-26", "重阳", TIER_10),
        ("2029-01-22", "腊八", TIER_10),
        ("2029-02-06", "小年", TIER_10),
    ],
}

# ---------------------------------------------------------------- 法定假期（国务院通知）
# (起, 止, 名称, 倍率)  —— 假期内每一天都吃这个倍率，首日另有红日覆盖
STATUTORY: dict[int, list[tuple[str, str, str, float]]] = {
    2026: [
        ("2026-01-01", "2026-01-03", "元旦", TIER_10),
        ("2026-02-15", "2026-02-23", "春节", TIER_10),
        ("2026-04-04", "2026-04-06", "清明", TIER_10),
        ("2026-05-01", "2026-05-05", "劳动节", TIER_10),
        ("2026-06-19", "2026-06-21", "端午", TIER_10),
        ("2026-09-25", "2026-09-27", "中秋", TIER_10),
        ("2026-10-01", "2026-10-07", "国庆节", TIER_10),
    ],
    # 2027 起国务院通知一般在头一年 11 月发布，发布后补：
    # 2027: [("2027-01-01", "2027-01-03", "元旦", TIER_10), ...],
}

# 红日（20 倍）：法定假期首日里最重的那一个
# {日期: (显示名, 被替代的节日名, 分类)}  —— 替代而不是叠加，否则除夕会变成 春节+除夕 = ×30
RED_DAYS: dict[str, tuple[str, str, str]] = {
    "2026-02-16": ("除夕", "春节", CAT_TRAD),
    "2026-10-01": ("国庆首日", "国庆节", CAT_STAT),
    "2027-02-05": ("除夕", "春节", CAT_TRAD),
    "2027-10-01": ("国庆首日", "国庆节", CAT_STAT),
    "2028-01-25": ("除夕", "春节", CAT_TRAD),
    "2028-10-01": ("国庆首日", "国庆节", CAT_STAT),
}


# ---------------------------------------------------------------- 计算

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """某月第 n 个周几（weekday: 0=周一）。"""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """某月最后一个周几。"""
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def easter(year: int) -> date:
    """复活节（Anonymous Gregorian algorithm）。"""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=8)
def calendar(year: int) -> dict[str, dict]:
    """某一年的全部节日：{ISO 日期: {mult, name, tier, tags, cats}}。

    同一天撞多个节日 → 倍率相加（清明 ×10 撞复活节 ×5 = ×15）；
    同一个节日登记了两次（如法定假期表和农历表都有"端午"）→ 只算一次。
    """
    cells: dict[str, list[tuple[str, float, str]]] = {}

    def put(iso: str, name: str, mult: float, cat: str) -> None:
        parts = cells.setdefault(iso, [])
        for i, (n, m, _c) in enumerate(parts):
            if n == name:  # 同一节日重复登记：取高，不叠
                if m < mult:
                    parts[i] = (name, mult, cat)
                return
        parts.append((name, mult, cat))

    for month, day, name, mult, cat in SOLAR_FIXED:
        put(date(year, month, day).isoformat(), name, mult, cat)

    for month, weekday, nth, name, mult, cat in NTH_WEEKDAY:
        put(_nth_weekday(year, month, weekday, nth).isoformat(), name, mult, cat)

    for month, weekday, name, mult, cat in LAST_WEEKDAY:
        put(_last_weekday(year, month, weekday).isoformat(), name, mult, cat)

    # 黑色星期五 = 感恩节次日
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    put((thanksgiving + timedelta(days=1)).isoformat(), "黑色星期五", TIER_5, CAT_WEST)

    put(easter(year).isoformat(), "复活节", TIER_5, CAT_WEST)

    # 农历/节气：按年维护的表，跨年的（腊八/小年）顺带收进本年
    for y in (year - 1, year):
        for iso, name, mult in YEARLY.get(y, []):
            if iso.startswith(f"{year:04d}-"):
                put(iso, name, mult, CAT_TRAD)

    # 法定假期：区间内每一天
    for start, end, name, mult in STATUTORY.get(year, []):
        d = date.fromisoformat(start)
        stop = date.fromisoformat(end)
        while d <= stop:
            put(d.isoformat(), name, mult, CAT_STAT)
            d += timedelta(days=1)

    # 红日：替掉该假期的普通档，再按 20 倍记
    for iso, (name, replaced, cat) in RED_DAYS.items():
        if iso.startswith(f"{year:04d}-"):
            cells[iso] = [(n, m, c) for n, m, c in cells.get(iso, []) if n != replaced]
            put(iso, name, TIER_20, cat)

    table: dict[str, dict] = {}
    for iso, parts in cells.items():
        mult = min(sum(m for _, m, _ in parts), MAX_STACK)
        table[iso] = {
            "mult": mult,
            "name": "+".join(n for n, _, _ in parts),
            "tier": tier_name(mult),
            "tags": [n for n, _, _ in parts],
            "cats": list(dict.fromkeys(c for _, _, c in parts)),
        }
    return table


def lookup(d: date) -> dict | None:
    """查某天的节日倍率，没有则 None。"""
    item = calendar(d.year).get(d.isoformat())
    return dict(item) if item else None


def year_entries(year: int) -> list[tuple[str, dict]]:
    return sorted(calendar(year).items())


def listing(start: date | None = None, days: int = 365) -> list[tuple[str, dict]]:
    """从 start 起若干天内所有节日日（不含周末）。"""
    start = start or date.today()
    out: list[tuple[str, dict]] = []
    d = start
    for _ in range(days):
        item = lookup(d)
        if item:
            out.append((d.isoformat(), item))
        d += timedelta(days=1)
    return out


def upcoming(start: date | None = None, days: int = 30, limit: int = 10) -> list[tuple[str, dict]]:
    return listing(start, days)[:limit]
