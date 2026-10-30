"""Reload a local checkpoint and reproduce every saved development prediction."""
import argparse
import json
import math
from pathlib import Path
import torch
from app.ml.semantic_ocr_normalizer import SemanticOCRNormalizer
from scripts.improve_ocr_normalizer import ROOT
from scripts.train_ocr_normalizer import metrics


def wilson_interval(correct, total):
    """Descriptive interval only: clustered receipts violate independent-row assumptions."""
    if not total:
        return None
    z = 1.96
    p = correct/total
    denominator = 1+z*z/total
    center = (p+z*z/(2*total))/denominator
    radius = z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denominator
    return [max(0.,center-radius),min(1.,center+radius)]


def verify(folder):
    torch.set_num_threads(2)
    saved = json.loads((folder/'heldout_predictions.json').read_text(encoding='utf-8'))
    model = SemanticOCRNormalizer(folder/'model.pt', ROOT/'output/semantic_ocr_cache')
    results = [model.predict(row['input']) for row in saved]
    mismatches = [i for i, (row, result) in enumerate(zip(saved, results))
                  if row['prediction'] != result['candidate_label']
                  or abs(row['score'] - result['model_score']) > 1e-4]
    accepted = [i for i, result in enumerate(results) if not result['needs_review']]
    food = [i for i,r in enumerate(saved) if r['truth']!='__NONFOOD__']
    correct = sum(row['truth']==result['candidate_label'] for row,result in zip(saved,results))
    report = {**metrics([r['truth'] for r in saved], [r['candidate_label'] for r in results]),
              'reload_mismatch_indices': mismatches, 'accepted_rows': len(accepted),
              'coverage': len(accepted)/len(saved),
              'accepted_accuracy': sum(saved[i]['truth'] == results[i]['candidate_label']
                                       for i in accepted)/len(accepted) if accepted else None,
              'threshold': model.threshold, 'production_deployed': False,
              'independent_test': False, 'calibrated_probability': False,
              'correct_rows':correct, 'food_rows':len(food),
              'food_canonical_accuracy':sum(saved[i]['truth']==results[i]['candidate_label'] for i in food)/len(food) if food else None,
              'descriptive_wilson_95':wilson_interval(correct,len(saved)),
              'interval_caveat':'Not independent rows or an independent test; interval is descriptive only.'}
    (folder/'reload_verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    if mismatches:
        raise RuntimeError('Reloaded inference differs from the training evaluator')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    verify(args.folder.resolve())
