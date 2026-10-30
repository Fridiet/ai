"""Structural receipt metadata, not guesses from product substrings."""
import re


def is_receipt_metadata(text: str) -> bool:
    compact = re.sub(r'[\s_]', '', text)
    if re.match(r'^대표(?:자)?[:：]', compact):
        return True
    if re.match(r'^\[P[O0]S\d+\]', compact, re.I):
        return True
    if re.match(r'^(?:상품및기타문의|멤버십.*?관련문의)[:：]?\d', compact):
        return True
    # Register / cashier lines need the complete POS grammar. A product merely
    # containing one of these English words is not a footer.
    if re.fullmatch(r'Reg#\d{1,6}Trans#\d{1,8}', compact, re.I):
        return True
    if re.fullmatch(r'CASHIER[:：][A-Za-z가-힣.\-]{1,80}REG#\d{1,6}', compact, re.I):
        return True
    if re.fullmatch(r'REG#\d{1,6}', compact, re.I):
        return True
    # Unlabelled store address followed by an explicit telephone marker.
    # Require region + road + street number + telephone, not a road/name word.
    if re.fullmatch(r'(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)[가-힣0-9]+(?:로|길)\d+(?:-\d+)?(?:TEL|T)[:：)]?\d{2,4}-\d{3,4}(?:-\d{4})?',compact,re.I):
        return True
    # Card-provider/account footer without a balanced product transaction.
    if re.fullmatch(r'(?:신한은행|국민은행|우리은행|농협은행|하나은행|토스뱅크|통스뱅크|삼성아멕스)[\d.*xX-]+', compact):
        return True
    return False
