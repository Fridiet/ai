from scripts.audit_receipt_readiness import diagnostic_rates


def test_rates_keep_abstentions_in_recall_not_conditional_precision():
    rates = diagnostic_rates(dict(name_labeled_food_rows=10, name_labeled_accepted=5,
        exact_names=4, quantity_labeled_food_rows=8, correct_purchase_counts=6,
        joint_labeled_food_rows=8, name_and_count_correct=3))
    assert rates['exact_name_recall_on_labeled_food'] == .4
    assert rates['exact_name_precision_on_aligned_labeled_food_only'] == .8
    assert rates['purchase_count_accuracy_on_labeled_food'] == .75
    assert rates['name_or_count_correction_needed_on_joint_labels'] == .625


def test_no_labels_is_unavailable_not_perfect():
    rates = diagnostic_rates({})
    assert all(value is None for key, value in rates.items() if key != 'scope_note')
