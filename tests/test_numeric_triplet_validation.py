from scripts.evaluate_calibrated_numeric_crops import verified_triplet


def cells():
    return [dict(field=k,text=t,confidence=.99) for k,t in
            [('unit_price','1,280'),('count','2'),('total','2,560')]]


def test_balanced_three_numeric_cells():
    assert verified_triplet(cells())==dict(unit_price=1280,count=2,total=2560)


def test_malformed_low_confidence_and_unbalanced_cells():
    for field,value in [('text','2?'),('text','3'),('confidence',float('nan')),('confidence',.89)]:
        sample=cells();sample[1][field]=value
        assert verified_triplet(sample) is None
    assert verified_triplet(cells()+[cells()[0]]) is None
    assert verified_triplet(cells()[:2]) is None
