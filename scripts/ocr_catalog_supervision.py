"""Deterministic, capped weak supervision from the existing local K-FIND DB."""
from collections import defaultdict
from difflib import SequenceMatcher
import json
import random
import sqlite3
from app.ml.ocr_normalizer import normalize, clean_product_text


# Explicit task taxonomy; unlike ingredient aliases, these are category roll-ups.
BREAD_TYPES = {'기타 빵', '식빵', '바게트/치아바타', '크림빵', '곡물빵',
               '소금빵', '즉석 빵', '호빵/찐빵', '단팥빵', '소보로빵',
               '모닝빵', '앙금빵', '토스트/샌드위치'}


def task_label(label):
    if label in BREAD_TYPES:
        return '빵'
    return {'즉석 피자':'피자', '우유(멸균)':'우유', '농후발효유':'요거트',
            '발효유':'요거트', '과·채음료':'음료', '액상음료':'음료',
            '기타 음료':'음료'}.get(label, label)


def catalog_examples(root, rows, split, per_class=12):
    reserved = [rows[i] for part in ('validation','test') for i in split[part]]
    groups = {r['duplicate_group'] for r in reserved}
    files = {r['filename'] for r in reserved}
    names = {normalize(clean_product_text(r['alignment']['ocr_text'])) for r in reserved}
    annotations = json.loads((root/'output/local_receipt_review/assistant_annotations.json').read_text(encoding='utf-8'))
    for receipt in annotations['records']:
        if receipt['filename'] in files or receipt['duplicate_group'] in groups:
            names.update(normalize(clean_product_text(r['product_name'])) for r in receipt['expected_items'])
    names.discard('')
    db = root/'app/data/products.sqlite3'
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as connection:
        records = connection.execute('SELECT id,name,standard_food_name,source FROM products ORDER BY id').fetchall()
    random.Random(42).shuffle(records)
    selected = defaultdict(list)
    seen = set()
    rejected = 0
    for identifier,text,label,source in records:
        label = task_label(label)
        if len(selected[label]) >= per_class:
            continue
        cleaned = normalize(clean_product_text(text))
        if len(cleaned)<2 or cleaned in seen:
            continue
        if cleaned in names or any(len(n)>=4 and abs(len(n)-len(cleaned))<=max(len(n),len(cleaned))*.2
                                  and SequenceMatcher(None,n,cleaned).ratio()>=.85 for n in names):
            rejected += 1
            continue
        seen.add(cleaned)
        selected[label].append({'text':text,'label':label,'source':'existing_kfind_catalog',
                                'product_id':identifier,'catalog_source':source})
    result = [row for label in sorted(selected) for row in selected[label]]
    return result, {'per_class_cap':per_class,'rows':len(result),'classes':len(selected),
                    'reserved_exact_or_near_excluded':rejected,'weak_supervision':True}
