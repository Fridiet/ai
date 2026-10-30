from types import SimpleNamespace
import numpy as np
import pytest
from app.receipt_tiles import tile_ranges, predict_tiles


def test_ranges_cover_every_row_once_with_context_overlap():
    ranges = list(tile_ranges(2000))
    assert ranges == [(0,900,0,1000),(900,1800,800,1900),(1800,2000,1700,2000)]
    assert sum(end-start for start,end,_,_ in ranges) == 2000


def test_same_text_on_distinct_rows_is_not_removed():
    class Engine:
        def __init__(self): self.calls = 0
        def predict(self, crop, **kwargs):
            # Row at global 890 seen by both first and second overlapping crops;
            # another row at 920 must survive as a distinct purchase.
            offset = [0,800][self.calls]
            self.calls += 1
            return [SimpleNamespace(json={'rec_texts':['고등어','고등어'],
                'rec_scores':[.99,.99], 'rec_polys':[
                    [[0,y-offset-5],[40,y-offset-5],[40,y-offset+5],[0,y-offset+5]] for y in [890,920]]})]
    result = predict_tiles(Engine(), np.zeros((1000,100,3), dtype=np.uint8))
    assert len(result) == 2
    assert [r['box'][0][1] for r in result] == [885,915]


def test_bad_dimensions_rejected():
    with pytest.raises(ValueError): list(tile_ranges(0))
