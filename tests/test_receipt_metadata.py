import pytest
from app.receipt_metadata import is_receipt_metadata
from app.product_pipeline import analyze_ocr_items


@pytest.mark.parametrize('text',[
    '대표:김기호 213-81-52063', '[P0S 1068437] 2023.10.25 20:16:21',
    '상품_및_기타 문의 :1522-4400','멤버십 및 삽다이소 관련 문의:1599-2211',
    '신한은행 110.*****6342', '통스뱅크 532750***X***X*', '삼성아멕스 379183',
    'Reg # 104 Trans # 258', 'REG#22 TRANS#1985',
    'CASHIER: J. H. PARK REG#104', 'Cashier: 김민수 REG#123', 'REG#104',
    '세종 종합운동장1로14T)1899-9900',
    '서울 강남구 테헤란로123 TEL:02-1234-5678',
])
def test_footer_not_a_review_product(text):
    assert is_receipt_metadata(text)
    assert not analyze_ocr_items([dict(text=text,confidence=.99,
        box=[[0,0],[500,0],[500,30],[0,30]])])['products']


@pytest.mark.parametrize('text',['대표사과 1000 1 1000','은행 500g 3000 1 3000',
    '국민간식 1000 1 1000','상품권초콜릿 1000 1 1000','문의감자 1000 1 1000',
    'Cashier 초콜릿 1000 1 1000', 'Reg 커피 1000 1 1000',
    'CASHIER: candy 1000 1 1000 REG#2', 'Reg # 104 Trans # 258 1000 1 1000',
    '세종당근 1000 1 1000','서울우유 1000ml 1000 1 1000',
    '세종 종합운동장1로14 1000 1 1000','고추로1899-9900'])
def test_product_words_are_not_metadata(text):
    assert not is_receipt_metadata(text)
