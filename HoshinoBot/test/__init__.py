import re

pattern = r'^(.+)?\s?(增|＋＋|\+\+|减少|减|-|－|－－|--)$'
text = "机厅--"

match = re.match(pattern, text)
if match:
    print("匹配成功！")
else:
    print("没有匹配。")