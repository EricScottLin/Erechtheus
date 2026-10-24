"""舞萌直连的价格表与扣费。

价格用**月币**结算：荣耀是等级分（扣了等于扣级），不能当钱花。
按荣耀等级的折扣沿用 ``qqbot_core.pricing.price()``，和商店/抽奖一致。

计费规则（刻意这样设计，避免"扣了钱没办成事"的投诉）：

1. 执行前先查余额，不够直接拒绝、不发起任何网络请求；
2. 操作成功后扣费；失败、忙、被权限拦下都不收费；
3. 单次价格在这里集中登记，改价只改 ``PRICES`` 一处（或 .env.prod 的
   ``MAIMAI_DX_PRICES`` 覆盖），不要在命令处理里写死数字。
"""

from nonebot import get_plugin_config, require

from .config import Config

core = require("qqbot_core")

CURRENCY = "drachma"
_OVERRIDES = dict(get_plugin_config(Config).maimai_dx_prices)

# 指令名 → 月币基础价。0 表示免费。
# 定价依据：普通用户签到 10–60 月币/天（中位约 35），详见 README 的定价说明。
PRICES: dict[str, int] = {
    # 导航类，不收费
    "说明": 0,
    # 只读且不需要写服务器 / 已完成绑定
    "大象信息": 0,
    "登出": 0,  # 安全阀：退出核验不该收费，否则用户会硬扛着不登出
    # 基础拉数据，最便宜
    "更新": 10,
    "导出": 10,
    "绑定": 100,
    # 管理员协助，不收费
    "解绑": 0,
}


def price_of(name: str) -> int:
    """基础价；.env.prod 的 MAIMAI_DX_PRICES 优先。未登记按 0 处理（宁可漏收费，也不要在运行时炸掉）。"""
    if name in _OVERRIDES:
        return max(0, int(_OVERRIDES[name]))
    return int(PRICES.get(name, 0))


async def quote(user_id: str, name: str) -> tuple[int, int]:
    """返回 (折后价, 当前余额)。免费指令不查折扣，直接返回 (0, 余额)。"""
    base = price_of(name)
    balance = int(await core.wallet.balance(user_id, CURRENCY))
    if base <= 0:
        return 0, balance
    cost = int(await core.pricing.price(user_id, base))
    return cost, balance


async def charge(user_id: str, name: str) -> tuple[bool, int, int]:
    """扣费，返回 (是否扣成功, 实扣金额, 扣后余额)。

    调用方必须只在操作成功后调用；余额在查询与扣费之间被别的操作花掉时，
    ``spend`` 会返回 False，这里如实返回，不重试、不补扣。
    """
    cost, balance = await quote(user_id, name)
    if cost <= 0:
        return True, 0, balance
    ok = bool(await core.wallet.spend(user_id, CURRENCY, cost, source="maimai_dx"))
    after = int(await core.wallet.balance(user_id, CURRENCY))
    return ok, cost, after


def cost_line(cost: int, balance: int) -> str:
    """统一的花费提示行；免费指令返回空串。"""
    if cost <= 0:
        return ""
    return f"💸 {cost} 月币 · 余额 {balance}"
