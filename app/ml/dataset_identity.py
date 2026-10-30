import hashlib
import json


def recipe_dataset_hash(recipes):
    return hashlib.sha256(json.dumps(recipes,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
