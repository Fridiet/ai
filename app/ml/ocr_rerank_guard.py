"""Conservative protection against unsupported nonfood-to-food reclassification."""
from app.ml.ocr_normalizer import NONFOOD,normalize,clean_product_text
from app.ml.ocr_lexical_prior import lexical_prior


def guard_nonfood_switches(original,reranked,texts,labels,all_food_switches=False,product_aliases=()):
    result = reranked.clone()
    nonfood = labels.index(NONFOOD)
    before = original.argmax(1)
    after = reranked.argmax(1)
    evidence = lexical_prior(texts,labels)
    aliases = set(tuple(pair) for pair in product_aliases)
    blocked = []
    for i in range(len(texts)):
        changed = before[i].item()!=after[i].item()
        supported = bool(evidence[i,after[i]].item()) or (normalize(clean_product_text(texts[i])),labels[after[i]]) in aliases
        protected = before[i].item()==nonfood or all_food_switches
        if changed and protected and after[i].item()!=nonfood and not supported:
            result[i] = original[i]
            blocked.append(i)
    return result,blocked
