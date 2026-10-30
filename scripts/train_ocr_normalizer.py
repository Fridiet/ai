"""Train an explicitly opt-in experiment on assistant-labeled OCR pairs."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import random
import torch
from app.ml.ocr_normalizer import CharacterClassifier, NONFOOD, features, normalize


def target(row):
    return row['target']['canonical_name'] if row['target']['is_food'] else NONFOOD


def split_groups(rows, seed=42):
    # Transitive components keep BOTH receipts and repeated products together.
    parent = list(range(len(rows)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    seen = {}
    for i, row in enumerate(rows):
        keys = [('receipt', row['duplicate_group']),
                ('product', normalize(row['target']['product_name'])),
                ('ocr', normalize(row['alignment']['ocr_text']))]
        for key in keys:
            if key in seen:
                parent[find(i)] = find(seen[key])
            seen[key] = i
    groups = defaultdict(list)
    for i in range(len(rows)):
        groups[find(i)].append(i)
    if len(groups) < 3:
        raise ValueError('Need at least three independent components; no split is fabricated.')
    parts = list(groups.values())
    random.Random(seed).shuffle(parts)
    parts.sort(key=len, reverse=True)
    split = {'train': [], 'validation': [], 'test': []}
    proportions = {'train': .7, 'validation': .15, 'test': .15}
    for group in parts:
        name = max(split, key=lambda k: proportions[k]*len(rows)-len(split[k]))
        split[name].extend(group)
    if not all(split.values()):
        raise ValueError('Grouping leaves an empty partition; more data required.')
    return split, len(parts)


def metrics(truth, predicted):
    tp = sum(a != NONFOOD and b != NONFOOD for a,b in zip(truth,predicted))
    fp = sum(a == NONFOOD and b != NONFOOD for a,b in zip(truth,predicted))
    fn = sum(a != NONFOOD and b == NONFOOD for a,b in zip(truth,predicted))
    return {'rows': len(truth), 'canonical_accuracy': sum(a==b for a,b in zip(truth,predicted))/len(truth),
        'food_precision': tp/(tp+fp) if tp+fp else None,
        'food_recall': tp/(tp+fn) if tp+fn else None}


def threshold_for_validation(truth, predicted, scores):
    for threshold in [i/100 for i in range(50, 100)]:
        accepted = [i for i,s in enumerate(scores) if s >= threshold]
        if len(accepted) >= 5 and sum(truth[i]==predicted[i] for i in accepted)/len(accepted) >= .9:
            return threshold
    return 1.01  # No adequate validation evidence: abstain on every request.


def train(source, output, allow_assistant_labels, epochs=250, seed=42):
    if not allow_assistant_labels:
        raise ValueError('Assistant annotations require explicit --allow-assistant-labels experiment opt-in.')
    rows = json.loads(source.read_text(encoding='utf-8'))
    for r in rows:
        if r['target']['label_status'] != 'assistant_labeled' or r['alignment']['status'] != 'aligned':
            raise ValueError('Unresolved labels/alignment cannot enter this experiment')
    split, components = split_groups(rows, seed)
    torch.set_num_threads(2)
    torch.manual_seed(seed)
    labels = sorted({target(rows[i]) for i in split['train']})
    x = features([r['alignment']['ocr_text'] for r in rows])
    truth = [target(r) for r in rows]
    y = torch.tensor([labels.index(t) if t in labels else -1 for t in truth])
    train_ids = split['train']
    val_ids = split['validation']
    known_val = [i for i in val_ids if y[i] >= 0]
    if not known_val:
        raise ValueError('Validation has no training classes; cannot select a model responsibly')
    model = CharacterClassifier(len(labels))
    optimizer = torch.optim.AdamW(model.parameters(), lr=.025, weight_decay=.01)
    best_loss, best_state, best_epoch, patience = float('inf'), None, 0, 0
    history = []
    for epoch in range(1, epochs+1):
        model.train()
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(model(x[train_ids]), y[train_ids])
        loss.backward()
        optimizer.step()
        model.eval()
        with torch.no_grad():
            val_loss = torch.nn.functional.cross_entropy(model(x[known_val]), y[known_val]).item()
        history.append({'epoch': epoch, 'train_loss': loss.item(), 'validation_known_class_loss': val_loss})
        if val_loss < best_loss - .0001:
            best_loss, best_state, best_epoch, patience = val_loss, deepcopy(model.state_dict()), epoch, 0
        else:
            patience += 1
        if patience >= 25:
            break
    model.load_state_dict(best_state)
    with torch.no_grad():
        probabilities = model(x).softmax(1)
        scores, indices = probabilities.max(1)
    predicted = [labels[i] for i in indices.tolist()]
    threshold = threshold_for_validation([truth[i] for i in val_ids], [predicted[i] for i in val_ids], [scores[i].item() for i in val_ids])
    evaluations = {}
    majority = Counter(truth[i] for i in train_ids).most_common(1)[0][0]
    for name, ids in split.items():
        actual, guesses = [truth[i] for i in ids], [predicted[i] for i in ids]
        nearest = [max(train_ids, key=lambda j: SequenceMatcher(None, normalize(rows[i]['alignment']['ocr_text']),
                     normalize(rows[j]['alignment']['ocr_text']), autojunk=False).ratio()) for i in ids]
        accepted = [i for i in ids if scores[i].item() >= threshold]
        evaluations[name] = {**metrics(actual, guesses), 'unseen_label_rows': sum(y[i]<0 for i in ids).item(),
            'food_rows': sum(t != NONFOOD for t in actual),
            'majority_baseline': metrics(actual, [majority]*len(ids)),
            'nearest_text_baseline': metrics(actual, [truth[j] for j in nearest]),
            'accepted_rows': len(accepted), 'coverage': len(accepted)/len(ids),
            'accepted_accuracy': sum(truth[i]==predicted[i] for i in accepted)/len(accepted) if accepted else None}
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = {'format': 'receipt_character_classifier_v1', 'state_dict': best_state,
        'labels': labels, 'dimension': x.shape[1], 'threshold': threshold}
    torch.save(checkpoint, output / 'model.pt')
    report = {'algorithm': 'PyTorch character 1/2/3-gram hashed multinomial logistic regression',
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'torch_version': str(torch.__version__), 'seed': seed, 'rows': len(rows), 'connected_groups': components,
        'labels': labels, 'best_epoch': best_epoch, 'epochs_run': len(history),
        'threshold': threshold, 'evaluation': evaluations, 'history': history,
        'training_performed': True, 'production_deployed': False,
        'assistant_label_experiment_authorized': True, 'human_verified': False,
        'independent_test': False,
        'limitations': ['AI labels and automatic alignment may contain errors.',
            'All receipts were previously inspected: held-out development diagnostics only.',
            'Unknown categories require abstention; softmax is not calibrated probability.',
            'Only 22 aligned examples differ after punctuation normalization; spelling correction evidence is limited.',
            'Canonical labels include food categories, not verified DB ingredient IDs.']}
    for name, content in [('training_report.json', report), ('split_manifest.json',
        {name: [{'filename': rows[i]['filename'], 'annotation_row': rows[i]['annotation_row'],
            'duplicate_group': rows[i]['duplicate_group']} for i in ids] for name,ids in split.items()}),
        ('heldout_predictions.json', [{'filename':rows[i]['filename'], 'input':rows[i]['alignment']['ocr_text'],
            'truth':truth[i], 'prediction':predicted[i], 'score':scores[i].item(),
            'accepted':scores[i].item()>=threshold} for i in split['test']])]:
        (output/name).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
    return {k:v for k,v in report.items() if k not in ('history','labels')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('output/local_receipt_review/pairs/candidates.json'))
    parser.add_argument('--output', type=Path, default=Path('output/ocr_normalizer_experiment_v1'))
    parser.add_argument('--allow-assistant-labels', action='store_true')
    parser.add_argument('--epochs', type=int, default=250)
    args = parser.parse_args()
    print(json.dumps(train(args.source, args.output, args.allow_assistant_labels, args.epochs), ensure_ascii=True, indent=2))
