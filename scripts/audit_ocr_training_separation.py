"""Audit effective receipt training examples against the frozen development partitions."""
import hashlib
import json
from app.ml.ocr_normalizer import normalize
from scripts.improve_ocr_normalizer import ROOT,clean,load_split
from scripts.train_ocr_normalizer import split_groups


def audit():
    source = ROOT/'output/local_receipt_review/pairs/candidates.json'
    rows = json.loads(source.read_text(encoding='utf-8'))
    split = load_split(rows)
    recomputed,_ = split_groups(rows,seed=42)
    split_matches = split==recomputed
    base = ROOT/'output/ocr_normalizer_experiment_v7'
    report = json.loads((base/'training_report.json').read_text(encoding='utf-8'))
    examples = json.loads((base/'training_examples.json').read_text(encoding='utf-8'))
    ids = [i for part in split.values() for i in part]
    grouping = {}
    for field in ('filename','duplicate_group'):
        sets = {name:{rows[i][field] for i in part} for name,part in split.items()}
        grouping[field] = {f'{a}/{b}':sorted(sets[a]&sets[b]) for a,b in
                           [('train','validation'),('train','test'),('validation','test')]}
    reserved = [rows[i] for name in ('validation','test') for i in split[name]]
    files = {r['filename'] for r in reserved}; groups = {r['duplicate_group'] for r in reserved}
    names = {normalize(clean(r['alignment']['ocr_text'])) for r in reserved}
    names.update(normalize(clean(r['target']['product_name'])) for r in reserved)
    annotations = json.loads((ROOT/'output/local_receipt_review/assistant_annotations.json').read_text(encoding='utf-8'))
    for receipt in annotations['records']:
        if receipt['filename'] in files or receipt['duplicate_group'] in groups:
            names.update(normalize(clean(r['product_name'])) for r in receipt['expected_items'])
    overlaps = [r for r in examples if normalize(clean(r['text'])) in names]
    direct = [r for r in overlaps if not (r['source'].startswith('existing_') or r['source']=='train_class_name')]
    kfind = [r for r in overlaps if r['source']=='existing_kfind_catalog']
    extra = [r for r in report['extra_visual_sources'] if r['filename'] in files or r['duplicate_group'] in groups]
    source_matches = hashlib.sha256(source.read_bytes()).hexdigest()==report['source_sha256']
    valid = (source_matches and split_matches and len(ids)==len(rows) and len(set(ids))==len(rows)
             and not any(values for group in grouping.values() for values in group.values())
             and not direct and not kfind and not extra)
    result = {'passes_direct_training_separation':valid,'original_pair_hash_matches':source_matches,
        'original_seed42_split_reproduced':split_matches,
        'partition_rows':{name:len(part) for name,part in split.items()},'group_overlaps':grouping,
        'effective_receipt_or_synthetic_name_overlaps':direct,'sampled_kfind_exact_name_overlaps':kfind,
        'extra_visual_reserved_receipt_overlaps':extra,'existing_vocabulary_overlaps':overlaps,
        'base_training_sha256':hashlib.sha256((base/'training_examples.json').read_bytes()).hexdigest(),
        'base_checkpoint_sha256':hashlib.sha256((base/'model.pt').read_bytes()).hexdigest(),
        'independent_test':False,'human_verified_labels':False,
        'caveat':'Existing food vocabulary is external knowledge. Development holdout was repeatedly inspected; this audit does not establish independent generalization.'}
    (base/'training_separation_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))
    if not valid:
        raise RuntimeError('Training separation audit failed')
    return result


if __name__=='__main__':
    audit()
