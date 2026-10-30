"""Seal and independently reload a local choice-reranking pipeline; never call remote inference."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import torch
from app.ml.choice_ocr_normalizer import ChoiceOCRNormalizer,INFERENCE_FILES,ROOT
from scripts.train_ocr_normalizer import metrics,target,split_groups
from scripts.verify_semantic_ocr import wilson_interval


def seal(folder):
    path = folder/'model.json'
    config = json.loads(path.read_text(encoding='utf-8'))
    report = json.loads((folder/'training_report.json').read_text(encoding='utf-8'))
    for key in ('weight','temperature'):
        if config[key]!=report['selected'][key]:
            raise ValueError('Manifest differs from validation-selected parameters')
    base = (folder/config['base_checkpoint']).resolve()
    audit = json.loads((base.parent/'training_separation_audit.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(base.read_bytes()).hexdigest()
    if not audit['passes_direct_training_separation'] or digest!=audit['base_checkpoint_sha256']:
        raise ValueError('Base checkpoint lacks a matching separation audit')
    config['base_checkpoint_sha256'] = digest
    config['inference_code_sha256'] = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in INFERENCE_FILES}
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')


def verify(folder,do_seal=False):
    torch.set_num_threads(2)
    if do_seal:
        seal(folder)
    rows = json.loads((folder/'heldout_predictions.json').read_text(encoding='utf-8'))
    source_rows = json.loads((ROOT/'output/local_receipt_review/pairs/candidates.json').read_text(encoding='utf-8'))
    split,_ = split_groups(source_rows,seed=42)
    expected = [(source_rows[i]['filename'],source_rows[i]['alignment']['ocr_text'],target(source_rows[i])) for i in split['test']]
    actual = [(row['filename'],row['input'],row['truth']) for row in rows]
    if actual!=expected:
        raise ValueError('Evaluation rows or truth differ from the original frozen split')
    model = ChoiceOCRNormalizer(folder/'model.json')
    results = []; elapsed = []
    for i,row in enumerate(rows):
        start = time.perf_counter()
        results.append(model.predict(row['input']))
        elapsed.append(time.perf_counter()-start)
        if (i+1)%5==0:
            print(f'reloaded verification {i+1}/{len(rows)}',flush=True)
    mismatches = [i for i,(row,result) in enumerate(zip(rows,results)) if row['prediction']!=result['candidate_label']
                  or abs(row['score']-result['model_score'])>1e-4]
    correct = sum(r['truth']==p['candidate_label'] for r,p in zip(rows,results))
    food = [i for i,r in enumerate(rows) if r['truth']!='__NONFOOD__']
    accepted = [i for i,p in enumerate(results) if not p['needs_review']]
    report = {**metrics([r['truth'] for r in rows],[p['candidate_label'] for p in results]),
        'correct_rows':correct,'reload_mismatch_indices':mismatches,'food_rows':len(food),
        'food_canonical_accuracy':sum(rows[i]['truth']==results[i]['candidate_label'] for i in food)/len(food),
        'accepted_rows':len(accepted),'coverage':len(accepted)/len(rows),
        'accepted_accuracy':sum(rows[i]['truth']==results[i]['candidate_label'] for i in accepted)/len(accepted) if accepted else None,
        'mean_seconds_per_item':sum(elapsed)/len(elapsed),'max_seconds_per_item':max(elapsed),
        'descriptive_wilson_95':wilson_interval(correct,len(rows)),
        'independent_test':False,'human_verified_labels':False,'production_deployed':False,
        'fresh_inference_no_score_cache':True,'remote_inference_used':False,
        'original_evaluation_rows_and_truth_match':True,
        'interval_caveat':'Development set with clustered receipt rows and AI labels; interval is descriptive only.'}
    (folder/'reload_verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (folder/'reloaded_predictions.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if mismatches:
        raise RuntimeError('Reloaded inference differs from experiment predictions')
    return report


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--seal',action='store_true')
    args = parser.parse_args()
    verify(args.folder.resolve(),args.seal)
