import pytest
from app.online_order import package_quantity, is_order_layout, analyze_order_items
from app.inventory_mapper import build_inventory_candidates, parse_inventory_quantity


@pytest.mark.parametrize('text,quantity,unit', [
    ('우유, 1L, 2개', 2000, 'mL'), ('두부, 300g, 2개입', 600, 'g'),
    ('통밀식빵, 420g, 2개입, 1세트', 840, 'g'),
    ('주스, 190ml, 3입', 570, 'mL'), ('그릭요거트, 90g, 8개입, 1개', 720, 'g'),
    ('깻잎, 30g, 3팩', 90, 'g'), ('소고기, 1개, 400g', 400, 'g'),
    ('백오이, 3개입, 1개', 3, '개'), ('양파, 1.5kg, 1개', 1500, 'g'),
    ('밀가루, 500g, 2봉', 1000, 'g'), ('양배추, 1개입, 1개', 1, '개'),
])
def test_package_contents(text, quantity, unit):
    result = package_quantity(text)
    assert (result['quantity'], result['unit']) == (quantity, unit)


@pytest.mark.parametrize('text', ['바나나, 1.2kg 내외, 1개',
                                  '연두부 오리엔탈소스 6p,804g,1개',
                                  '두부, 0g, 1개', '고기, 300g, 500g',
                                  '샐러드,709,1개', '우유,500m,1개'])
def test_do_not_invent_contents(text):
    assert package_quantity(text)['source'] == 'DEFAULT'


def item(text, y, x=130):
    return dict(text=text, confidence=.99, box=[[x,y],[780,y],[780,y+25],[x,y+25]])


def test_cards_wrapped_titles_and_purchase_count():
    items = [item('배송완료', 10, 10),
             item('로켓프레시 샐러드,70g,1', 100), item('개', 130),
             item('1,710원·2개', 165), item('장바구니 담기', 165, 625),
             item('로켓프레시 우유,1L,2개', 220),
             item('5,840원·1개', 265), item('장바구니 담기', 265, 625)]
    assert is_order_layout(items)
    analysis = analyze_order_items(items)
    candidates = build_inventory_candidates(analysis)
    assert [c['inventory_batch_candidate']['remaining_quantity'] for c in candidates] == [140, 2000]
    # GPT identity correction cannot replace the independently verified amount.
    analysis['products'][0]['llm_quantity_snapshot'] = dict(quantity=1,unit='개',source='DEFAULT')
    assert build_inventory_candidates(analysis)[0]['inventory_batch_candidate']['remaining_quantity'] == 140


def test_receipt_not_order():
    assert not is_order_layout([item('양파 500g 2 3980',100),item('장바구니',200)])


def test_normal_receipt_explicit_bundle():
    assert parse_inventory_quantity('밀가루 500g 2봉')['quantity'] == 1000
    assert parse_inventory_quantity('우유 1L,2개')['quantity'] == 2000
    assert parse_inventory_quantity('밀가루 500g 2 3980')['quantity'] == 500


def test_corrected_contents_agree_with_recipe_quantity_balance():
    from app.quantity_balance import ingredient_balances
    quantity = package_quantity('두부,300g,2개입')
    balance = ingredient_balances([dict(canonical_name='두부', quantity=400, unit='g')],
                                 {'두부': [dict(remaining_quantity=quantity['quantity'], unit=quantity['unit'])]})
    assert balance[0]['status'] == 'ENOUGH'
    assert balance[0]['knownAvailableQuantity'] == 600
    unknown = ingredient_balances([dict(canonical_name='양파',quantity=200,unit='g')],
                                 {'양파': [dict(remaining_quantity=2,unit='개')]})
    assert unknown[0]['status'] == 'UNKNOWN'
    assert unknown[0]['shortageQuantity'] is None


def test_recovery_never_makes_misread_g_an_extra_digit(monkeypatch):
    import numpy as np
    from app.online_order import refine_order_titles
    lines=[item('로켓프레시 두부,3009,1개',100),item('3,000원·1개',150),
           item('장바구니 담기',150,625),item('로켓프레시 우유,1L,2개',200),
           item('4,000원·1개',250),item('장바구니 담기',250,625)]
    def recognize(crops):
        return [dict(text='로켓프레시 두부,3009g,1개',confidence=.99),
                dict(text='300g,1개',confidence=.99),dict(text='300g,1개',confidence=.99)]
    monkeypatch.setattr('app.ocr.recognize_numeric_crops',recognize)
    result=refine_order_titles(np.zeros((400,900,3),dtype=np.uint8),lines)
    assert package_quantity(result[0]['text'])['quantity'] == 300
    assert result[0]['order_original_text'] == lines[0]['text']
