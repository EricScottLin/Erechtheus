"""汇率换算工具实现（自然语言「多少多少 A 是多少 B」）。

数据源优先级（任一可用即可，全部失败才报错）：

1. **中国货币网中间价**（``chinamoney.com.cn``）—— 央行授权发布的人民币中间价，
   权威；但只覆盖「X/人民币」，交叉汇率用两组中间价推算。
2. **open.er-api.com** —— 免费的实时汇率，USD 基准，覆盖全部币种（每日更新）。
3. **frankfurter.app** —— 欧洲央行参考汇率，兜底。

设计要点：

- 币种识别用「中文名 / 代码 / 符号」三套词表，长词优先匹配（``人民币`` 不能被
  ``元`` 抢走、``美元`` 不能被 ``美`` 抢走）。
- 金额解析支持阿拉伯数字与中文数字（``两千五``、``一千二``），也支持 ``3.5万``。
- 网络失败不抛异常，返回给模型看的文字说明，由模型决定怎么兜底。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from nonebot.log import logger

# ---------------------------------------------------------------- 币种表
# 代码 -> (中文名列表, 符号列表)
_CURRENCIES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "CNY": (("人民币", "元", "RMB", "¥"), ("￥", "¥")),
    "JPY": (("日元", "日币", "円"), ("JP¥", "JPY")),
    "USD": (("美元", "美金", "刀"), ("US$", "$")),
    "EUR": (("欧元", "欧"), ("€",)),
    "GBP": (("英镑", "英磅", "镑"), ("£",)),
    "HKD": (("港元", "港币", "香港元"), ("HK$",)),
    "MOP": (("澳门元", "澳门币", "澳元(澳门)"), ()),
    "TWD": (("新台币", "台币", "新台幣"), ("NT$",)),
    "KRW": (("韩元", "韩币", "韩国元"), ("₩",)),
    "SGD": (("新加坡元", "新币", "新加坡币"), ("S$",)),
    "AUD": (("澳大利亚元", "澳洲元", "澳元", "澳币"), ("A$",)),
    "CAD": (("加拿大元", "加元", "加币"), ("C$",)),
    "CHF": (("瑞士法郎", "瑞郎"), ()),
    "RUB": (("卢布", "俄罗斯卢布"), ("₽",)),
    "THB": (("泰铢",), ("฿",)),
    "MYR": (("马来西亚林吉特", "马币", "林吉特"), ("RM",)),
    "INR": (("印度卢比", "卢比"), ("₹",)),
    "NZD": (("新西兰元", "纽币"), ("NZ$",)),
    "SEK": (("瑞典克朗",), ()),
    "NOK": (("挪威克朗",), ()),
    "DKK": (("丹麦克朗",), ()),
    "AED": (("阿联酋迪拉姆", "迪拉姆"), ()),
    "SAR": (("沙特里亚尔", "里亚尔"), ()),
    "TRY": (("土耳其里拉", "里拉"), ()),
    "MXN": (("墨西哥比索", "比索"), ()),
    "ZAR": (("南非兰特", "兰特"), ()),
    "PLN": (("波兰兹罗提", "兹罗提"), ()),
    "HUF": (("匈牙利福林", "福林"), ()),
    "VND": (("越南盾",), ("₫",)),
    "PHP": (("菲律宾比索",), ("₱",)),
    "IDR": (("印尼盾", "印度尼西亚盾"), ()),
    "BTC": (("比特币",), ("₿",)),
}

# 中文大写/常用写法
_ALIAS = {
    "RMB": "CNY",
    "人民币元": "CNY",
    "美元": "USD",
    "欧元": "EUR",
}

# 长词优先：建一张「写法 -> 代码」的表，按 key 长度倒序做正则交替
_NAME_TO_CODE: dict[str, str] = {}
for _code, (_names, _symbols) in _CURRENCIES.items():
    for _n in (*_names, *_symbols, _code):
        _NAME_TO_CODE.setdefault(_n.lower(), _code)
for _alias, _code in _ALIAS.items():
    _NAME_TO_CODE[_alias.lower()] = _code
# 「元」单独出现时指人民币，但「美元/欧元/日元」已被长词匹配，正则里用长词优先
_SORTED_NAMES = sorted(_NAME_TO_CODE, key=len, reverse=True)
_NAME_PATTERN = re.compile("|".join(re.escape(n) for n in _SORTED_NAMES), re.IGNORECASE)

# 中文数字
_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "两": 2, "貳": 2, "贰": 2, "三": 3, "叁": 3,
    "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陆": 6, "七": 7, "柒": 7, "八": 8, "捌": 8,
    "九": 9, "玖": 9,
}
_CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_BIG = {"万": 10_000, "亿": 100_000_000}

# 金额：阿拉伯数字或中文数字，允许「3.5万」「两千五」这类写法
_AMOUNT_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)*|[零〇一壹二两貳贰三叁四肆五伍六陆七柒八捌九玖十拾百佰千仟万亿]+)"
    r"\s*(?P<mult>万|亿)?"
)


# ---------------------------------------------------------------- 解析
def _cn_to_number(text: str) -> Optional[float]:
    """把中文数字（含 ``两千五`` 这种口语省略）转成数值；失败返回 None。"""
    if not text:
        return None
    total = 0.0
    section = 0.0
    digit: Optional[int] = None
    saw_unit = False
    for ch in text:
        if ch in _CN_DIGITS:
            digit = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            saw_unit = True
            unit = _CN_UNITS[ch]
            section += (1 if digit is None else digit) * unit
            digit = None
        elif ch in _CN_BIG:
            saw_unit = True
            big = _CN_BIG[ch]
            total += (section + (digit or 0)) * big
            section = 0.0
            digit = None
        else:
            return None
    if digit is not None:
        if saw_unit and digit < 10 and text[-1] in _CN_DIGITS:
            # 口语省略：「两千五」= 2500，「三百五」= 350
            prev_unit = 1
            for ch in reversed(text[:-1]):
                if ch in _CN_UNITS:
                    prev_unit = _CN_UNITS[ch]
                    break
                if ch in _CN_BIG:
                    prev_unit = _CN_BIG[ch]
                    break
            section += digit * (prev_unit // 10) if prev_unit >= 100 else digit
        else:
            section += digit
    return total + section


def _to_amount(num_text: str, mult: Optional[str]) -> Optional[float]:
    if not num_text:
        return None
    if re.fullmatch(r"\d+(?:[.,]\d+)*", num_text):
        value = float(num_text.replace(",", ""))
    else:
        value = _cn_to_number(num_text)
        if value is None:
            return None
    if mult == "万":
        value *= 10_000
    elif mult == "亿":
        value *= 100_000_000
    return value


@dataclass
class Query:
    amount: float
    src: str
    dst: str


def _match_currency(token: str) -> Optional[str]:
    """把一个「币种写法」归一成 ISO 代码；认不出返回 None。"""
    token = (token or "").strip()
    if not token:
        return None
    lower = token.lower()
    exact = _NAME_TO_CODE.get(lower)
    if exact:
        return exact
    # 长词优先：token 里包含的最长已识别写法（如「美刀」-> 美元，「刀」）
    best: Optional[tuple[int, str]] = None
    for name in _SORTED_NAMES:
        if not name:
            continue
        pos = lower.find(name)
        if pos < 0:
            continue
        if best is None or len(name) > best[0]:
            best = (len(name), _NAME_TO_CODE[name])
    return best[1] if best else None


def parse_query(text: str) -> Optional[Query]:
    """从自然语言里解析「金额 + 源币种 + 目标币种」。

    兼容写法：``2500日元是多少人民币``、``100美元换成人民币``、
    ``把 300 欧元 换算成 JPY``、``$50 多少港币``、``两千五百日元等于多少人民币``。
    """
    if not text:
        return None
    text = text.strip()

    # 找出文本里出现的币种（正则已按长词优先排序，不会把「美元」切成「元」）
    seen: list[tuple[str, int]] = []
    for m in _NAME_PATTERN.finditer(text):
        code = _NAME_TO_CODE[m.group(0).lower()]
        if code not in [c for c, _ in seen]:
            seen.append((code, m.start()))
    if not seen:
        return None

    src, src_pos = seen[0]
    if len(seen) >= 2:
        dst = seen[1][0]
    else:
        # 只提了一个币种：默认换成人民币（本来就是人民币就换美元）
        dst = "CNY" if src != "CNY" else "USD"

    # 金额：优先取源币种前面最近的数字；没有就取整句里第一个数字
    amount = None
    for m in reversed(list(_AMOUNT_RE.finditer(text[:src_pos]))):
        candidate = _to_amount(m.group("num"), m.group("mult"))
        if candidate is not None:
            amount = candidate
            break
    if amount is None:
        m = _AMOUNT_RE.search(text)
        if m is not None:
            amount = _to_amount(m.group("num"), m.group("mult"))
        else:
            # 「一块钱」→ 一
            amount = 1.0 if ("一" in text) else None
    if amount is None:
        return None
    return Query(amount=amount, src=src, dst=dst)


# ---------------------------------------------------------------- 汇率源
async def _fetch_json(url: str, timeout: float) -> Optional[dict[str, Any]]:
    import httpx

    headers = {"User-Agent": "Mozilla/5.0 (qqbot currency tool)"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        return resp.json()


async def _rates_frankfurter(base: str, timeout: float) -> Optional[tuple[dict[str, float], str]]:
    data = await _fetch_json(f"https://api.frankfurter.app/latest?from={base}", timeout)
    if not data or "rates" not in data:
        return None
    rates = {k: float(v) for k, v in data["rates"].items()}
    rates[base] = 1.0
    return rates, f"欧洲央行参考汇率 {data.get('date', '')}"


async def _rates_er_api(base: str, timeout: float) -> Optional[tuple[dict[str, float], str]]:
    data = await _fetch_json(f"https://open.er-api.com/v6/latest/{base}", timeout)
    if not data or data.get("result") != "success":
        return None
    rates = {k: float(v) for k, v in (data.get("rates") or {}).items()}
    if not rates:
        return None
    stamp = str(data.get("time_last_update_utc") or "").strip()
    return rates, f"exchangerate-api 实时汇率 {stamp}".strip()


async def _rates_chinamoney(timeout: float) -> Optional[dict[str, float]]:
    """人民币中间价，返回 {代码: 每 1 单位外币兑人民币}（CNY 自身为 1）。"""
    data = await _fetch_json(
        "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/ccpr.json", timeout
    )
    if not data:
        return None
    # 结构：{"head": {...}, "data": {...}, "records": [...]} —— records 与 data 同级
    records = data.get("records") or (data.get("data") or {}).get("records") or []
    rates: dict[str, float] = {"CNY": 1.0}
    for row in records:
        name = str(row.get("vrtEName") or "")  # 如 "100JPY/CNY"
        price = row.get("price")
        if "/" not in name or price in (None, "", "---"):
            continue
        left, right = name.split("/", 1)
        code = re.sub(r"^\d+", "", left).upper()
        if right.upper() != "CNY" or code in ("CNY", ""):
            continue
        # 「100日元/人民币」的 price 是 100 单位日元 -> 人民币，必须除以数量
        scale_m = re.match(r"^(\d+)", left)
        scale = int(scale_m.group(1)) if scale_m else 1
        try:
            rates[code] = float(price) / scale
        except (TypeError, ValueError):
            continue
    return rates if len(rates) > 1 else None


async def get_rate(src: str, dst: str, timeout: float = 8.0) -> tuple[float, str]:
    """返回 (1 单位 src 兑多少 dst, 数据来源说明)。失败抛 ``RuntimeError``。"""
    src, dst = src.upper(), dst.upper()
    if src == dst:
        return 1.0, "同一币种"

    # 1) 人民币中间价：两个都覆盖时精度最高（人民银行的官方价）
    try:
        cm = await asyncio.wait_for(_rates_chinamoney(timeout), timeout=timeout + 2)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[qqbot_ai] 中间价获取失败：{type(e).__name__}: {e}")
        cm = None
    if cm and src in cm and dst in cm:
        return cm[src] / cm[dst], "中国货币网人民币汇率中间价"

    # 2) 实时汇率（覆盖面最广）
    for fetcher in (_rates_frankfurter, _rates_er_api):
        try:
            result = await asyncio.wait_for(fetcher(src, timeout), timeout=timeout + 2)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[qqbot_ai] {fetcher.__name__} 失败：{type(e).__name__}: {e}")
            continue
        if not result:
            continue
        table, note = result
        if dst in table and table[dst] > 0:
            # 从 USD 基准算 (1 USD = x src / y dst) 也行
            return table[dst], note
        if "USD" in table and table["USD"] > 0:
            # 交叉：1 src = table[USD] USD；再查 USD -> dst
            try:
                usd_res = await asyncio.wait_for(fetcher("USD", timeout), timeout=timeout + 2)
            except Exception:  # noqa: BLE001
                usd_res = None
            if usd_res and dst in usd_res[0]:
                return table["USD"] * usd_res[0][dst], note
    raise RuntimeError("汇率服务暂时不可用")


def format_result(amount: float, src: str, dst: str, rate: float, source: str) -> str:
    from datetime import datetime, timedelta, timezone

    value = amount * rate
    cst = datetime.now(timezone(timedelta(hours=8)))
    return (
        f"{_fmt_amount(amount)} {_label(src)} ≈ {_fmt_amount(value)} {_label(dst)}"
        f"（1 {src} = {rate:.6g} {dst}，{source}，查询时间 {cst:%Y-%m-%d %H:%M}）"
    )


def _label(code: str) -> str:
    names = _CURRENCIES.get(code, ((), ()))[0]
    return f"{names[0]}({code})" if names else code


def _fmt_amount(value: float) -> str:
    if abs(value) >= 100:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return f"{value:.6g}"


# ---------------------------------------------------------------- 工具入口
async def run_currency_convert(args: dict[str, Any]) -> str:
    """工具 handler：既支持结构化参数，也支持直接传自然语言 ``query``。"""
    raw_query = str(args.get("query") or "").strip()
    parsed = parse_query(raw_query) if raw_query else None

    src_arg = str(args.get("from") or args.get("from_currency") or "").strip()
    dst_arg = str(args.get("to") or args.get("to_currency") or "").strip()
    amount_raw = args.get("amount")

    src = _match_currency(src_arg) or (parsed.src if parsed else None)
    dst = _match_currency(dst_arg) or (parsed.dst if parsed else None)
    if amount_raw in (None, "", 0, "0") and parsed:
        amount_raw = parsed.amount

    if amount_raw in (None, ""):
        return (
            "没有识别到金额，请给出要换算的金额和目标币种，"
            "例如「2500日元是多少人民币」或 amount=2500, from=JPY, to=CNY。"
        )

    try:
        amount = float(str(amount_raw).replace(",", ""))
    except (TypeError, ValueError):
        return f"金额「{amount_raw}」不是有效数字。"

    src = src or "CNY"
    dst = dst or ("USD" if src == "CNY" else "CNY")

    try:
        rate, source = await get_rate(src, dst)
    except RuntimeError:
        return "汇率服务暂时不可用，没查到实时汇率。可以稍后再问，或让我联网搜索。"

    logger.info(
        f"[qqbot_ai] 汇率 {amount:g} {src} -> {dst} = {amount * rate:.6g}（{source}）"
    )
    return format_result(amount, src, dst, rate, source)
