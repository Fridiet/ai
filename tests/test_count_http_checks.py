from scripts.verify_count_corroboration_http import check_items


def sample():
    return [dict(name='밀가루',quantity=2.5,unit='kg',reviewNotes=['가격으로 복원']),
            dict(name='우유',quantity=1,unit='개',reviewNotes=['포장당 용량 확인']),
            dict(name='청오이',category='vegetable')]


def test_required_http_values_and_review_provenance():
    assert all(c['passed'] for c in check_items('IMG00059.png',sample()))
    changed=sample()
    changed[0]['reviewNotes']=[]
    assert not all(c['passed'] for c in check_items('IMG00059.png',changed))


def test_duplicate_identity_does_not_pass():
    items=sample()
    items.append(items[0])
    assert not all(c['passed'] for c in check_items('IMG00059.png',items))


def test_damaged_volume_cannot_be_silently_asserted():
    changed=sample()
    changed[1].update(quantity=1800,unit='ml')
    assert not all(c['passed'] for c in check_items('IMG00059.png',changed))
