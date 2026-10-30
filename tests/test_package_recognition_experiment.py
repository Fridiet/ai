from scripts.experiment_package_recognition import package_candidate, select_product_crops
from scripts.evaluate_package_recognition import agreement_candidate


def item(text, top):
    return dict(text=text, box=[[0,top],[200,top],[200,top+20],[0,top+20]])


def test_damaged_unit_is_candidate_not_inferred_quantity():
    assert package_candidate('우유900m1')
    assert package_candidate('이스트89')
    assert not package_candidate('1,980')
    assert not package_candidate('대파')


def test_contact_and_footer_are_not_reread_as_products():
    items = [item('전화1234',0), item('우유900m1',40), item('환불7일',80)]
    product = dict(item('우유900m1 1,980',40), prices=[1980])
    assert [i for i,_ in select_product_crops(items,[product])] == [1]


def test_unpriced_region_is_not_selected():
    row = item('회원1234',0)
    assert select_product_crops([row],[dict(row,prices=[])]) == []


def rereads(a, b, before='닭정육82kg'):
    return [dict(padding=p,before=before,after=t,score=.95) for p,t in [(0,a),(3,b)]]


def test_agreed_separated_weight_is_offline_candidate():
    assert agreement_candidate(rereads('닭정육8 2kg','닭정육8 2kg')) is not None


def test_whitespace_agreement_cannot_hide_quantity_conflict():
    assert agreement_candidate(rereads('닭정육8 2kg','닭정육82kg')) is None


def test_inconsistent_unit_and_damaged_volume_are_not_promoted():
    assert agreement_candidate(rereads('이스트8g','이스트89','이스트89')) is None
    assert agreement_candidate(rereads('우유900m1','우유900m1','우유900m1')) is None


def test_unchanged_package_and_nonfinite_confidence_are_ignored():
    assert agreement_candidate(rereads('우유900ml','우유900ml','우유900ml')) is None
    values=rereads('닭정육8 2kg','닭정육8 2kg')
    values[0]['score']=float('nan')
    assert agreement_candidate(values) is None
