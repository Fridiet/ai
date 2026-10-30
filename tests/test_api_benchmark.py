from scripts.benchmark_receipt_api import percentile, summarize


def test_interpolated_percentiles_and_missing_samples():
    assert percentile([], .95) is None
    assert percentile([3], .95)==3
    assert percentile([1,2,3],.5)==2
    assert percentile([1,2,3],.95)==2.9


def test_failures_not_hidden_and_initial_request_excluded():
    records=[dict(filename='a',phase='initial',seconds=50,status=200,response_sha256='x'),
             dict(filename='a',phase='repeat',seconds=2,status=200,response_sha256='x'),
             dict(filename='a',phase='repeat',seconds=120,status=None)]
    result=summarize(records)
    assert result['repeat_p50_seconds']==2
    assert result['failed_requests']==1
    assert result['response_stable_by_image']=={'a':True}
    assert result['first_request_is_proven_cold'] is False
