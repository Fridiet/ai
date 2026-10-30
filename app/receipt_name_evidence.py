"""Conservative whole-product evidence, separate from learned model scores."""
import re
from .ingredient_matcher import lookup_key

FRESH_NAMES = {
    "청파프리카",
    "백오이", "청오이", "가시오이", "다다기오이",
    "양파", "대파", "쪽파", "당근", "감자", "고구마", "마늘", "생강",
    "양배추", "배추", "양상추", "상추", "청양고추", "고추", "오이", "애호박",
    "붉은볼락", "단호박", "시금치", "콩나물", "숙주", "팽이버섯", "새송이버섯", "표고버섯",
    "느타리버섯", "브로콜리", "파프리카", "고등어", "파인애플", "바나나",
}
NONFOOD = re.compile(r"샴푸|컨디셔너|치약|세제|화장지|키친타[올월]|물티슈|비누|방향제|주방세정|수세미|(?:옷칠|스텐|실리콘|나무).*?(?:스푼|국자)|(?:스푼|국자)(?:소|대)?$|면봉|불스원샷|휘발유용|경유용|재[사시]용봉투|종량제봉투")
COMPOUND = re.compile(r"라면|짬뽕|과자|스낵|크래커|웨이퍼|소스|드레싱|케이크|케익|쿠키|빵|피자|만두|스프|수프|향|맛|분말|가루|즙|주스|음료|라떼|커피|스틱|칩|요거트|요구르트|우유|절임|장아찌|볶음|튀김|전골|찌개|국물|세트|모듬|모둠|믹스")


def snack_brand_context(text):
    # Context blocks raw-produce guesses; it does not prove a particular snack.
    return bool(re.match(r'^(?:오리온|크라운|해태|농심)', lookup_key(text)))


def fresh_suffix(text):
    if snack_brand_context(text) or NONFOOD.search(lookup_key(text)) or COMPOUND.search(lookup_key(text)):
        return None
    # Strip only explicit packaging suffixes; never arbitrary final words.
    def packaging(match):
        parts = re.split(r"[/,\s]+", match.group()[1:-1].strip())
        return "" if parts and all(re.fullmatch(r"\d*(?:입|개|봉|팩|망|단|묶음|대|중|소|해동|냉장|냉동)", part) for part in parts) else match.group()
    text = re.sub(r"\([^)]*\)|\[[^]]*\]", packaging, text)
    text = re.sub(r"\d+(?:\.\d+)?\s*(?:kg|mg|g|ml|l|개|입|팩|봉|단|망|묶음)\s*$", "", text, flags=re.I)
    key = lookup_key(text)
    if NONFOOD.search(key) or COMPOUND.search(key):
        return None
    candidates = sorted((name for name in FRESH_NAMES if key.endswith(name)), key=len, reverse=True)
    if not candidates:
        return None
    name = candidates[0]
    prefix = key[:-len(name)]
    # Do not guess a single ingredient for a mixed product.
    if any(other in prefix for other in FRESH_NAMES):
        return None
    return name


def clear_nonfood(text):
    return bool(NONFOOD.search(lookup_key(text)))


def compound_product(text):
    key = lookup_key(text)
    return bool(COMPOUND.search(key) or re.search(r'초콜릿|초코|잼|빼빼로|웨하스|크라상|크루아상|크로아상|호떡|드링크', key))


def unresolved_flavor_context(text, matched_name):
    """A flavor substring alone does not identify the surrounding product.

    This is an ambiguity signal, never a fuzzy repair into chips or sweets.
    Whole plain names with explicit size remain unambiguous. Branded products
    can still use independently verified catalog identities or model evidence.
    """
    name = lookup_key(matched_name or '')
    if name not in {'사워크림', '바닐라', '초콜릿', '치즈', '케첩', '마요네즈'}:
        return False
    value = re.sub(r'^\s*\d{1,3}\s+', '', text)
    value = re.sub(r'\d+(?:\.\d+)?\s*(?:kg|g|ml|l|개|입|팩|봉)\s*$', '', value, flags=re.I)
    key = lookup_key(value)
    return name in key and key != name


def egg_product_head(text):
    """Egg identity outranks marketing words such as 안심, not cooked egg dishes."""
    if clear_nonfood(text):
        return None
    key = lookup_key(text)
    match = re.search(r'(왕란|특란|신선란|유정란|무정란|계란|달걀)(?:\d+(?:구|입|개|판))*$', key)
    if not match:
        return None
    prefix = key[:match.start()]
    if COMPOUND.search(prefix) or re.search(r'구운|훈제|반숙|삶은|간장|소금|장조림|소고기|돼지고기|닭고기', prefix):
        return None
    return '달걀'


def explicit_food_head(text):
    """Preserve explicit product identity; plain 등심 does not identify animal species."""
    if clear_nonfood(text):
        return None
    # POS often appends a delimiter plus a truncated numeric size/SKU. This
    # proves neither weight nor count; discard it only for identity matching.
    text = re.sub(r"_\d{1,6}\s*$", "", text)
    # Identity-only recovery: m1 may be a damaged ml glyph, but it does not
    # establish volume. Restrict the prefix to plain-milk brand/marketing text.
    milk_key = lookup_key(text)
    milk = re.fullmatch(r'((?:서울|남양|매일|파스퇴르|빙그레)?(?:[1-3]등급|아침에|신선한|유기농|맛있는|흰)*)우유(?:\d+(?:ml|m1|l)(?:\d+(?:입|개))?)?', milk_key)
    if milk and re.search(r'\d+m1', milk_key):
        return '우유', '유제품'
    # Identity only: package multipliers are not purchased bundle counts.
    text = re.sub(r"\d+(?:\.\d+)?\s*(?:kg|mg|g|ml|l)\s*[*x×]\s*\d+\s*$", "", text, flags=re.I)
    text = re.sub(r"\d+(?:\.\d+)?\s*(?:kg|g|개입|개|입)\s*$", "", text, flags=re.I)
    text = re.sub(r"\(\d+(?:g|kg)\)$", "", text, flags=re.I)
    key = lookup_key(text)
    # Preserve drying as part of food identity, rather than matching the fresh
    # fruit substring. A following sauce/drink/snack head is not dried fruit.
    # Complete product heads, not flavor words inside another product. Numeric
    # suffixes are accepted for identity only; they do not establish contents.
    if re.fullmatch(r'wheattortillas?', key):
        return '또띠아', '가공식품'
    if re.fullmatch(r'(?:뉴)?디너롤(?:[1-9]\d{0,2}ct)?', key):
        return '디너롤', '빵류'
    if re.fullmatch(r'(?:ks)?(?:마일드)?체다(?:치즈)?', key):
        return '체다치즈', '유제품'
    # Verified brand + named flavor can identify a product even when POS omits
    # the chip head. Do not generalize the brand alone or repair its spelling.
    # Sources and exclusions: docs/ocr-verified-brand-products.md.
    if re.fullmatch(r'(?:\d{1,3})?(?:lays|레이즈)(?:(?:케찹|케첩|ketchup)(?:맛)?(?:감자(?:칩)?)?|(?:솔트(?:앤드|앤)?비네거|saltvinegar)(?:맛)?(?:감자칩)?)(?:\d+(?:g)?)?', key):
        return '감자칩', '과자'
    if re.fullmatch(r'(?:\d{1,3})?(?:노브랜드)?(?:카라멜|캐러멜)초콜릿', key):
        return '초콜릿', '과자'
    for pattern, name, food_type in [
        (r'레몬녹차(?:\d+(?:\.\d+)?(?:ml|l)?)?$', '레몬녹차', '음료'),
        (r'(?:또르?띠아|토르띠아|토르티야|또르티야)$', '또띠아', '가공식품'),
        (r'멸균우유(?:\d+(?:\.\d+)?(?:ml|l)?)?$', '멸균우유', '유제품'),
        (r'(?:마시멜로우?|머쉬멜로우|마쉬멜로우|마시맬로)(?:\d+)?$', '마시멜로', '과자'),
        (r'샤인머스[캣켓](?:\d+)?$', '샤인머스캣', '과실류'),
    ]:
        head = re.search(pattern, key)
        if head and not compound_product(key[:head.start()]):
            return name, food_type
    dried = re.search(r'(건무화과|건자두|푸룬|건포도|건망고|건살구|건크랜베리)$', key)
    if dried and not COMPOUND.search(key[:dried.start()]):
        name = '푸룬' if dried.group() == '건자두' else dried.group()
        return name, '건과일'
    # Zero is a printed product variant, not a nutrition guarantee. POS P/CAN
    # suffixes and unitless sizes are identity suffixes only, never volume proof.
    zero = re.search(r'(?:(사이다|콜라)제로|제로(사이다|콜라))(?:p|pet|can|캔|페트)?(?:\d+(?:\.\d+)?(?:ml|l)?)?$', key)
    if zero and not COMPOUND.search(key[:zero.start()]):
        return '제로' + (zero.group(1) or zero.group(2)), '음료'
    # A truncated numeric size does not establish volume, but an explicit
    # fruit + beverage head still establishes identity (not the raw fruit).
    beverage = re.search(r'(망고|사과|포도|오렌지|파인애플|자두|복숭아|석류)(음료|주스)(?:\d+(?:\.\d+)?(?:ml|l)?)?$', key)
    if (beverage and not COMPOUND.search(key[:beverage.start()])
            and not re.search(r'망고|사과|포도|오렌지|파인애플|자두|복숭아|석류|건$|말린$', key[:beverage.start()])):
        return beverage.group(1) + beverage.group(2), '음료'
    if re.search(r'(?:먹는|맑은)샘물$', key):
        return '물', '음료'
    # Orthographic variants of the nut, not arbitrary fuzzy OCR corrections.
    # Preserve an explicitly printed variety/product, not its broad parent.
    if re.search(r'미고[랭렝]누들$', key):
        return '미고랭누들', '면류'
    if re.search(r'(?:해시|해쉬)브라운(?:감자)?$', key):
        return '해시브라운', '가공식품'
    if re.search(r'단백질바(?:초코|초콜릿|땅콩|아몬드)?$', key):
        return '단백질바', '가공식품'
    if re.search(r'치즈볼$', key):
        return '치즈볼', '가공식품'
    if key.endswith('피자'):
        return '피자', '가공식품'
    if re.search(r'웨이퍼(?:롤)?(?:바닐라|초코|초콜릿|딸기)?$', key):
        return '웨이퍼', '과자'
    olive = re.search(r'(그린|블랙)올리브$', key)
    if olive and not COMPOUND.search(key[:olive.start()]):
        return olive.group(), '과실류'
    for pattern, name, food_type in [
        (r'(?:크라상|크루아상|크로아상)(?:\d+(?:입|개|봉))?$', '크루아상', '빵류'),
        (r'빼빼로$', '빼빼로', '과자'),
        (r'웨하스(?:바닐라|초코|딸기)?$', '웨하스', '과자'),
        (r'호떡(?:요요|\d{1,2})?$', '호떡', '빵류'),
    ]:
        if re.search(pattern, key):
            return name, food_type
    # A complete nut head remains legible despite a damaged marketing prefix.
    # A POS size without a unit proves identity only, never package weight.
    nut = re.search(r'(호두|아몬드|캐슈너트|캐슈넛|피스타치오|마카다미아)(?:\d{1,4})?$', key)
    if nut and not compound_product(key[:nut.start()]) and not snack_brand_context(text):
        prefix = key[:nut.start()]
        if not re.search(r'호두|아몬드|캐슈|피스타치오|마카다미아|땅콩|초콜릿|초코|꿀|허니|설탕|카라멜|캐러멜', prefix):
            return ('캐슈넛' if nut.group(1) == '캐슈너트' else nut.group(1)), '견과류'
    # Processed compounds such as 캐슈너트음료 must keep their own identity.
    if key.endswith(('캐슈너트', '캐슈넛')) and not COMPOUND.search(key):
        return '캐슈넛', '견과류'
    # Explicit chips plus flavor: sour cream is a modifier, not the product.
    # Recover a single OCR glyph only when both the potato product stem and
    # an explicit flavor remain. Bare 감자침 or damaged stems are not proof.
    if re.search(r'감자침(?:사워크림|어니언|오리지널|솔트|소금|치즈|바베큐)+$', key):
        return '감자칩', '과자'
    if re.search(r'감자칩(?:사워크림|어니언|오리지널|솔트|소금|치즈|바베큐)*$', key):
        return '감자칩', '과자'
    jam = re.search(r'(딸기|사과|포도|블루베리|살구|무화과|오렌지)잼$', key)
    if jam:
        return jam.group(), '잼류'
    # Keep a sauce as sauce rather than its tomato component. Other named
    # sauce bases (cream/rose) are not silently changed to tomato sauce.
    if re.search(r'토마토(?:스파게티|파스타)?소스$', key) and not re.search(r'로제|크림', key):
        return '토마토소스', '소스'
    # Unambiguous prepared-food heads remain their prepared identity even with
    # ingredient/flavor modifiers: 고구마식빵 is bread, not a raw sweet potato.
    for suffix, name, food_type in [('식빵', '식빵', '빵류'),
                                    ('모닝롤', '모닝빵', '빵류'),
                                    ('케이크', '케이크', '빵류'),
                                    ('케익', '케이크', '빵류'),
                                    ('카스테라', '카스테라', '빵류'),
                                    ('쿠키', '쿠키', '과자'),
                                    ('웨이퍼', '웨이퍼', '과자'),
                                    ('드레싱', '드레싱', '소스'),
                                    ('샐러드', '샐러드', '즉석섭취식품'),
                                    ('미네랄워터', '물', '음료')]:
        if key.endswith(suffix):
            return name, food_type
    for suffix, name, food_type in [("모둠쌈", "쌈채소", "채소"), ("모듬쌈", "쌈채소", "채소"),
                                     ("유정란", "달걀", "알류"), ("무정란", "달걀", "알류"),
                                     ("등심", "등심", "육류")]:
        if key.endswith(suffix) and not COMPOUND.search(key[:-len(suffix)]):
            return name, food_type
    return None
