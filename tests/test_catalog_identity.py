from pathlib import Path
from app.catalog_identity import verified_identity
from app.product_catalog import ProductCatalog, ProductEntry


def test_legal_type_and_product_head_both_required():
    assert verified_identity('사조참치액','소스') == '참치액'
    assert verified_identity('참치액','과채주스') is None
    assert verified_identity('참치액맛과자','과자') == '과자'
    assert verified_identity('아무소스','소스') is None


def test_granular_yogurt():
    assert verified_identity('무가당그릭요거트','농후발효유') == '그릭요거트'
    assert verified_identity('요거트맛과자','과자') == '과자'


def catalog(monkeypatch, entries):
    c=ProductCatalog([],Path('missing.sqlite3'))
    monkeypatch.setattr(c,'_search_local',lambda _:entries)
    monkeypatch.setattr(c,'_search_mfds',lambda _:[])
    return c


def entry(name='사조참치액',kind='소스'):
    return ProductEntry(name,'제조사',kind,'기타 소스류','K-FIND:test')


def test_exact_catalog_maps_inventory_identity(monkeypatch):
    r=catalog(monkeypatch,[entry()]).match('사조 참치액')
    assert r['inventory_identity_verified']
    assert r['standard_food_name']=='참치액'


def test_fuzzy_catalog_not_promoted(monkeypatch):
    assert not catalog(monkeypatch,[entry()]).match('사조 참지액')['inventory_identity_verified']


def test_homonymous_conflicting_types_abstain(monkeypatch):
    r=catalog(monkeypatch,[entry(),entry(kind='과자')]).match('사조참치액')
    assert not r['inventory_identity_verified']
