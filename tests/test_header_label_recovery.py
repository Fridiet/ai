from app.receipt_header_recovery import supplement_header_labels


def cell(text,x,y=0,confidence=.99):
    return dict(text=text,confidence=confidence,box=[[x,y],[x+60,y],[x+60,y+20],[x,y+20]])


def test_add_only_missing_label_without_replacing_evidence():
    raw=[cell('상품명',0),cell('단가',300),cell('수량',420)]
    reread=[*raw,cell('금액',540,confidence=.867)]
    result=supplement_header_labels(raw,reread)
    assert len(result)==4
    assert result[:3]==raw
    assert result[3]['text']=='금액'
    assert result[3]['header_reread']
    assert len(raw)==3


def test_reject_unverified_or_displaced_headers():
    raw=[cell('상품명',0),cell('단가',300),cell('수량',420)]
    for reread in [[*raw,cell('금액',540,confidence=.7)],
                   [*raw,cell('금액',540,y=80)],
                   [cell('상품명',0),cell('단가',100),cell('수량',420),cell('금액',540)]]:
        assert supplement_header_labels(raw,reread) is raw
    assert supplement_header_labels([],raw+[cell('금액',540)])==[]


def test_complete_header_is_not_replaced():
    raw=[cell('상품명',0),cell('단가',300),cell('수량',420),cell('금액',540)]
    assert supplement_header_labels(raw,raw) is raw


def test_existing_labels_must_be_unique_and_confident():
    clean=[cell('상품명',0),cell('단가',300),cell('수량',420)]
    reread=clean+[cell('금액',540)]
    duplicate=clean+[cell('단가',100)]
    assert supplement_header_labels(duplicate,reread) is duplicate
    weak=[clean[0],cell('단가',300,confidence=.5),clean[2]]
    assert supplement_header_labels(weak,reread) is weak
