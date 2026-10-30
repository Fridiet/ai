"""Grouping is for display/storage only, never proof of recipe availability."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def parents():
    data = json.loads((Path(__file__).with_name('data')/'ingredient_specificity.json').read_text(encoding='utf-8'))
    return {child:parent for parent,children in data.items() for child in children}


def family(name):
    return parents().get(name,name)


PROCESSED_NAMES = {'감자칩','마늘칩','마늘튀김','당근퓨레','절임무','깻잎장아찌','훈제오리','훈제연어',
    '참치캔','땅콩버터','푸룬','달걀지단','삶은달걀','수란','발사믹크림','할라피뇨피클'}


def receipt_category_override(name):
    if name in PROCESSED_NAMES:
        return 'processed_food'
    if name in {'양파즙','생강즙','레몬즙'}:
        return 'beverage'
    if name in {'고추기름','마늘기름','양파가루','생강가루','멸치액젓','까나리액젓','매실청'}:
        return 'seasoning'
    return None
