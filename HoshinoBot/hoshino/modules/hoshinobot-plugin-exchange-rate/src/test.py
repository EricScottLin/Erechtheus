import asyncio
from exchange_rate import handle_message

async def test():
    print(await handle_message('汇率'))
    print(await handle_message('汇率50EUR'))
    print(await handle_message('汇率10.5人民币'))
    print(await handle_message('汇率50 RMB'))
    print(await handle_message('汇率墨西哥比索'))

    print(await handle_message('汇率收藏新台币'))
    print(await handle_message('汇率'))
    print(await handle_message('汇率收藏新台币'))
    print(await handle_message('汇率取消收藏新台币'))

    print(await handle_message('汇率998'))
    print(await handle_message('汇率USD998'))

if __name__=='__main__':
    asyncio.run(test())
