from scripts.audit_receipt_readiness import transaction_compatible
from scripts.evaluate_receipts import greedy_text_matches


def test_duplicate_paid_and_gift_names_align_without_quantity_leakage():
    labels = [dict(transaction_type='purchase'), dict(transaction_type='gift')]
    raw = ['킨더부에노 증정', '킨더부에노 2 3,400']
    matches = greedy_text_matches(['킨더부에노']*2,
        [['킨더부에노', text] for text in raw], threshold=.55,
        compatible=lambda i,j: transaction_compatible(labels[i], raw[j]))
    assert matches[0][0] == 1
    assert matches[1][0] == 0


def test_missing_gift_marker_is_not_invented():
    assert not transaction_compatible(dict(transaction_type='gift'), '킨더부에노 1')
    assert transaction_compatible({}, '킨더부에노 증정')
    assert transaction_compatible(dict(transaction_type='purchase'), '증정행사 초콜릿 1 3,000')
