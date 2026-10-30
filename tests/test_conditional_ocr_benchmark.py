from scripts.benchmark_conditional_receipt_ocr import summarize


def row(iteration, base, total, trigger=True, preserved=True):
    return dict(iteration=iteration, base_seconds=base, total_seconds=total,
                secondary_triggered=trigger, primary_identity_preserved=preserved,
                existing_counts_preserved=preserved)


def test_summary_excludes_initial_from_timing_but_not_invariants():
    result = summarize([row(0,100,200,preserved=False), row(1,2,4), row(1,4,6,False)])
    assert result['repeat_count'] == 2
    assert result['repeat_base_p50_seconds'] == 3
    assert result['repeat_total_p50_seconds'] == 5
    assert result['repeat_secondary_count'] == 1
    assert result['primary_identity_preserved'] is False
    assert result['existing_counts_preserved'] is False


def test_initial_only_has_no_repeat_percentile():
    result = summarize([row(0,2,4)])
    assert result['repeat_total_p95_seconds'] is None
    assert result['repeat_count'] == 0
