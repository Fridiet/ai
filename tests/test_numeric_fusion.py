from app.receipt_numeric_fusion import supplement_numbers


def box(text,x,y,confidence=.99):
    return dict(text=text,confidence=confidence,box=[[x,y],[x+20,y],[x+20,y+12],[x,y+12]])


def test_adds_only_unoccupied_numeric_box_and_keeps_names():
    header=[box('단가',100,0),box('수량',160,0),box('금액',220,0),box('상품명',0,0)]
    row=[box('양파',0,40),box('1000',100,40),box('2000',220,40)]
    primary=header+row
    merged=supplement_numbers(primary,[box('2',160,40),box('대파',0,40)], [header,row])
    assert merged[:-1]==primary
    assert merged[-1]['text']=='2'
    assert len(primary)==7


def test_never_overwrites_low_confidence_primary_digit():
    header=[box('단가',100,0),box('수량',160,0),box('금액',220,0),box('상품명',0,0)]
    row=[box('양파',0,40),box('1000',100,40),box('2000',220,40),box('7',160,40,.1)]
    assert supplement_numbers(header+row,[box('2',160,40)],[header,row])==header+row


def test_no_header_no_fusion():
    primary=[box('양파',0,40)]
    assert supplement_numbers(primary,[box('2',160,40)],[primary])==primary
