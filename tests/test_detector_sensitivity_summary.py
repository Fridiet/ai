from scripts.experiment_receipt_detector_sensitivity import summarize_runs


def test_variant_counts_and_times_remain_separate():
    runs = [dict(variant='default', seconds=2., audit={'counts': {'food_rows': 3, 'correct_purchase_counts': 1}}),
            dict(variant='default', seconds=4., audit={'counts': {'food_rows': 5, 'correct_purchase_counts': 2}}),
            dict(variant='secondary', seconds=1., audit={'counts': {'food_rows': 3, 'correct_purchase_counts': 3}})]
    summary = summarize_runs(runs, 'seconds')
    assert summary['default']['receipts'] == 2
    assert summary['default']['counts'] == {'food_rows': 8, 'correct_purchase_counts': 3}
    assert summary['secondary']['receipts'] == 1
    assert summary['secondary']['sample_p50_seconds'] == 1.
    assert summarize_runs([], 'seconds') == {}
