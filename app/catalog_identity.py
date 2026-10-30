"""Inventory identities supported by BOTH an exact product and its legal type.

No fuzzy names or arbitrary legal types are promoted to ingredient identities.
This is a mapping policy, not a learned confidence/probability.
"""
from .ingredient_matcher import lookup_key


def verified_identity(product_name: str, food_type: str) -> str | None:
    key = lookup_key(product_name)
    kind = lookup_key(food_type)
    # Specific processed heads precede broad legal classes. Never map sauces to
    # the animal/vegetable used in their flavoring (참치액 != 참치).
    if kind in {'소스', '복합조미식품', '액젓', '기타수산물가공품'}:
        for head in ('참치액', '굴소스', '멸치액젓', '까나리액젓', '드레싱'):
            if key.endswith(head):
                return head
    if kind in {'발효유', '농후발효유'}:
        return '그릭요거트' if '그릭' in key else '요구르트'
    if kind == '커피':
        return '커피'
    if kind in {'과자', '캔디류', '초콜릿', '초콜릿가공품', '맥주', '두부'}:
        return {'캔디류': '사탕', '초콜릿가공품': '초콜릿'}.get(kind, kind)
    if kind in {'유탕면', '건면'} and any(head in key for head in ('라면', '짬뽕', '짜파게티')):
        return '라면'
    if kind == '즉석조리식품' and key.endswith(('즉석밥', '맛있는밥', '햇반')):
        return '즉석밥'
    return None
