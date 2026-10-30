from scripts.audit_receipt_readiness import catalog_alias_agreement


def test_yogurt_registered_alias_is_separate_from_exact():
    assert catalog_alias_agreement('요거트', '요구르트')
    assert not catalog_alias_agreement('요구르트', '요구르트')


def test_specific_product_and_partial_name_not_equated():
    assert not catalog_alias_agreement('그릭요거트', '요구르트')
    assert not catalog_alias_agreement('팽이버섯', '버섯')
    assert not catalog_alias_agreement(None, '요구르트')
