import json
from os import path
import re
import time
import aiohttp

help_message = '''
汇率
[汇率] 查看汇率
[汇率+货币] 查看汇率
[汇率+数额+货币] 查看汇率
[汇率收藏+货币] 收藏货币
[汇率取消收藏+货币] 取消收藏货币
'''.strip()

def file_path(file_name : str) -> str:
    return path.join(path.dirname(__file__), file_name)

api_url = 'https://api.exchange-rate.yuudi.dev/exchange-rate.json'
alias : dict[str, str] = {}
readable : dict[str, str] = {}
with open(file_path('alias.json'), 'r') as f:
    alias = json.load(f)
with open(file_path('currencies.csv'), 'r') as f:
    currencies = f.read().split('\n')
    for currency in currencies:
        fields = currency.split(',')
        alias[fields[3]] = fields[0]
        readable[fields[0]] = fields[3]

rate_updated = 0
rates : dict[str, float | int] = {}
saved = [
    'USD',
    'CNY',
    'JPY',
]

if path.exists(file_path('data/rate.json')):
    with open(file_path('data/rate.json'), 'r') as f:
        rate = json.load(f)
        rate_updated = rate['updated']
        rates = rate['rates']
if path.exists(file_path('data/saved.json')):
    with open(file_path('data/saved.json'), 'r') as f:
        saved = json.load(f)

def save_rate():
    with open(file_path('data/rate.json'), 'w') as f:
        rate = {
            'updated': rate_updated,
            'rates': rates,
        }
        json.dump(rate, f)
def save_saved():
    with open(file_path('data/saved.json'), 'w') as f:
        json.dump(saved, f)

def get_currency_code(currency_name : str):
    if rates.get(currency_name) is not None:
        return currency_name
    if (code := alias.get(currency_name)) is not None:
        return code
    return None

async def handle_message(query):
    if query.startswith('汇率收藏'):
        currency_name = query[4:].strip()
        if not currency_name:
            return '汇率收藏+货币'
        currency_code = get_currency_code(currency_name)
        if currency_code is None:
            return '不支持的货币'
        if currency_code not in rates:
            return '无数据：' + readable[currency_code]
        if currency_code not in saved:
            saved.append(currency_code)
            save_saved()
            return '收藏成功：' + readable[currency_code]
        return f'{currency_code} 已在收藏内'
    if query.startswith('汇率取消收藏'):
        currency_name = query[6:].strip()
        if not currency_name:
            return '汇率取消收藏+货币'
        currency_code = get_currency_code(currency_name)
        if currency_code is None:
            return '不支持的货币' + currency_name
        if currency_code not in saved:
            return '未收藏：' + readable[currency_code]
        saved.remove(currency_code)
        save_saved()
        return f'{currency_code} 已取消收藏'
    if query == '汇率帮助':
        return help_message
    if query.startswith('汇率'):
        query = query[2:].strip()
        if not query:
            return await generate_message('USD', 100)
        value = 100
        query_value = re.match(r'^(\d+(?:\.\d{1,2})?)', query)
        if query_value:
            value = float(query_value.group(1))
            query = query[query_value.end():].strip()
        currency_name = query
        if not currency_name:
            return '汇率\n汇率+货币\n汇率+数额+货币'
        currency_code = get_currency_code(currency_name)
        if currency_code is None:
            return '不支持的货币' + currency_name
        if currency_code not in rates:
            return '无数据：' + readable[currency_code]
        return await generate_message(currency_code, value)

async def update_rate():
    global rate_updated, rates
    now_ts = int(time.time())
    updated = rate_updated
    if now_ts - updated < 24 * 60 * 60:
        return
    async with aiohttp.ClientSession() as session:
        async with session.get(api_url) as resp:
            if resp.status != 200:
                return
            data = json.loads(await resp.text())
            rate_updated = data['updated']
            rates = data['rates']
            save_rate()

async def generate_message(currency : str, value : int | float):
    await update_rate()
    core_value = value / rates[currency]
    message = f'{value} {readable[currency]} 等于'
    for c in saved:
        v = core_value * rates[c]
        message += f'\n{v:.4f} {readable[c]}'
    return message
