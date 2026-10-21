"""抓取菜品库（下厨房搜索页）——/吃什么 的数据来源。

用法（在容器里或宿主机 venv 中执行）：
    python plugins/qqbot_pigverse/tools/fetch_dishes.py

产物写入 localstore 数据目录：
    data/qqbot_pigverse/dishes.json   索引
    data/qqbot_pigverse/dishes/*.jpg  720x540 菜品图

注意：图片来自下厨房（i2.chuimg.com），仅作群内娱乐展示，不随仓库分发。
"""

import json, re, time, urllib.parse
from pathlib import Path
import httpx

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

DISHES = [
    # 川菜
    ("麻婆豆腐","川菜"),("回锅肉","川菜"),("宫保鸡丁","川菜"),("水煮鱼","川菜"),
    ("水煮肉片","川菜"),("鱼香肉丝","川菜"),("辣子鸡","川菜"),("酸菜鱼","川菜"),
    ("毛血旺","川菜"),("口水鸡","川菜"),("蒜泥白肉","川菜"),("干煸四季豆","川菜"),
    ("蚂蚁上树","川菜"),("夫妻肺片","川菜"),("冒菜","川菜"),("麻辣香锅","川菜"),
    # 粤菜
    ("白切鸡","粤菜"),("烧鹅","粤菜"),("叉烧","粤菜"),("虾饺","粤菜"),
    ("肠粉","粤菜"),("煲仔饭","粤菜"),("豉汁蒸排骨","粤菜"),("清蒸鲈鱼","粤菜"),
    ("干炒牛河","粤菜"),("菠萝咕咾肉","粤菜"),("盐焗鸡","粤菜"),("云吞面","粤菜"),
    # 湘菜
    ("剁椒鱼头","湘菜"),("辣椒炒肉","湘菜"),("小炒黄牛肉","湘菜"),("口味虾","湘菜"),
    ("腊味合蒸","湘菜"),("毛氏红烧肉","湘菜"),
    # 鲁菜
    ("糖醋鲤鱼","鲁菜"),("葱烧海参","鲁菜"),("九转大肠","鲁菜"),("四喜丸子","鲁菜"),("把子肉","鲁菜"),
    # 苏菜
    ("松鼠鳜鱼","苏菜"),("狮子头","苏菜"),("盐水鸭","苏菜"),("大煮干丝","苏菜"),("蟹粉小笼","苏菜"),
    # 浙菜
    ("西湖醋鱼","浙菜"),("东坡肉","浙菜"),("龙井虾仁","浙菜"),("叫花鸡","浙菜"),("梅干菜扣肉","浙菜"),
    # 闽菜
    ("佛跳墙","闽菜"),("海蛎煎","闽菜"),("沙茶面","闽菜"),
    # 徽菜
    ("臭鳜鱼","徽菜"),("毛豆腐","徽菜"),
    # 东北菜
    ("锅包肉","东北菜"),("地三鲜","东北菜"),("猪肉炖粉条","东北菜"),("小鸡炖蘑菇","东北菜"),
    ("酸菜白肉","东北菜"),("东北乱炖","东北菜"),("溜肉段","东北菜"),
    # 西北菜
    ("羊肉泡馍","西北菜"),("肉夹馍","西北菜"),("凉皮","西北菜"),("兰州牛肉面","西北菜"),
    ("大盘鸡","西北菜"),("手抓饭","西北菜"),("烤羊肉串","西北菜"),("油泼面","西北菜"),
    # 云贵菜
    ("过桥米线","云贵菜"),("汽锅鸡","云贵菜"),("酸汤鱼","云贵菜"),("折耳根炒腊肉","云贵菜"),("宣威火腿","云贵菜"),
    # 家常菜
    ("番茄炒蛋","家常菜"),("青椒肉丝","家常菜"),("酸辣土豆丝","家常菜"),("红烧肉","家常菜"),
    ("可乐鸡翅","家常菜"),("糖醋排骨","家常菜"),("蛋炒饭","家常菜"),("红烧茄子","家常菜"),
    ("蒜蓉西兰花","家常菜"),("紫菜蛋花汤","家常菜"),("冬瓜排骨汤","家常菜"),("番茄牛腩","家常菜"),
    # 主食小吃
    ("饺子","小吃"),("小笼包","小吃"),("生煎包","小吃"),("馄饨","小吃"),
    ("煎饼果子","小吃"),("油条","小吃"),("豆浆","小吃"),("豆腐脑","小吃"),
    ("胡辣汤","小吃"),("热干面","小吃"),("螺蛳粉","小吃"),("麻辣烫","小吃"),
    ("火锅","小吃"),("烧烤","小吃"),("炸鸡","小吃"),("汉堡","小吃"),
    ("披萨","小吃"),("寿司","小吃"),
    # 快餐
    ("拉面","快餐"),("咖喱饭","快餐"),("黄焖鸡米饭","快餐"),("沙县小吃","快餐"),
    ("臭豆腐","快餐"),("烤冷面","快餐"),
]

OUT = Path("/srv/qqbot/data/qqbot_pigverse/dishes")
OUT.mkdir(parents=True, exist_ok=True)
INDEX = OUT.parent / "dishes.json"
items = []
miss = []

with httpx.Client(headers=UA, timeout=20, follow_redirects=True) as c:
    for i, (name, cuisine) in enumerate(DISHES):
        try:
            r = c.get("https://www.xiachufang.com/search/?keyword=" + urllib.parse.quote(name))
            pairs = re.findall(r'data-src="(https://i2\.chuimg\.com/[^"]+)"[^>]*alt="([^"]*)"', r.text)
            pick = None
            for src, title in pairs:
                if name in title:
                    pick = (src, title); break
            if pick is None and pairs:
                pick = pairs[0]
            if pick is None:
                miss.append(name); print("MISS", name, flush=True); continue
            src = re.sub(r'\?.*$', '', pick[0]) + "?imageView2/1/w/720/h/540/interlace/1/q/80"
            img = c.get(src)
            if img.status_code != 200 or len(img.content) < 5000:
                miss.append(name); print("BAD", name, img.status_code, flush=True); continue
            fname = f"{len(items):03d}.jpg"
            (OUT / fname).write_bytes(img.content)
            items.append({"name": name, "cuisine": cuisine, "file": fname, "src_title": pick[1][:40]})
            print(f"OK {name} {len(img.content)}", flush=True)
        except Exception as e:
            miss.append(name); print("ERR", name, e, flush=True)
        time.sleep(0.4)

INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"DONE total={len(items)} miss={len(miss)}: {miss}", flush=True)
