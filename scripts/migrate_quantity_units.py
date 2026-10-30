"""Local MySQL quantity migration: preview, backup, apply; never guesses weights."""
import argparse
import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from sqlalchemy import text
from scripts.seed_development_data import _resolve_engine

ROOT = Path(__file__).resolve().parents[1]
UNITS = {}
for line in (ROOT / 'app/data/quantity_units.properties').read_text(encoding='utf-8').splitlines():
    if line and not line.startswith('#'):
        key, value = line.split('=', 1)
        unit, factor = value.split(',')
        UNITS[key] = unit, Decimal(factor)

def plan(row):
    result = {k: row.get(k) for k in ('remaining_quantity', 'unit', 'unit_type', 'custom_quantity_text', 'count_basis', 'per_count_quantity', 'per_count_unit')}
    result['quantity_needs_review'] = True
    spec = UNITS.get(str(row['unit'] or '').strip().lower())
    n = row['remaining_quantity']
    if row['unit_type'] == 'QUANTITY' and spec and n is not None and Decimal(str(n)) >= 0:
        value = (Decimal(str(n)) * spec[1]).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
        if value >= Decimal('1e12'): raise ValueError('Quantity exceeds DECIMAL capacity')
        result.update(remaining_quantity=value, unit=spec[0], quantity_needs_review=False)
    return result

def digest(connection, table):
    rows = [dict(r) for r in connection.execute(text(f'SELECT * FROM `{table}`')).mappings()]
    return hashlib.sha256(json.dumps(rows, default=str, sort_keys=True).encode()).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--restore', type=Path, help='Backup JSON; skips batches modified since migration')
    args = parser.parse_args()
    engine = _resolve_engine(None)
    if engine.url.host not in ('localhost', '127.0.0.1'): raise RuntimeError('Local DB only')
    if args.restore:
        backup = json.loads(args.restore.read_text(encoding='utf-8'))
        restored, skipped = [], []
        with engine.begin() as c:
            for old in backup['rows']:
                if old['status'] not in ('ACTIVE','EXPIRED'): continue
                current = c.execute(text('SELECT * FROM inventory_batch WHERE batch_id=:id FOR UPDATE'), {'id':old['batch_id']}).mappings().first()
                expected = plan(old)
                if not current or str(current['updated_at']) != str(old['updated_at']) or current['unit'] != expected['unit'] or str(current['status']) != old['status'] or (None if current['remaining_quantity'] is None else Decimal(str(current['remaining_quantity']))) != (None if expected['remaining_quantity'] is None else Decimal(str(expected['remaining_quantity']))):
                    skipped.append(old['batch_id']); continue
                values = {k:old.get(k) for k in ('remaining_quantity','unit','unit_type','custom_quantity_text','count_basis','per_count_quantity','per_count_unit')}
                # Added columns remain, so the current server can still read restored legacy quantities safely.
                values['quantity_needs_review'] = old['unit_type'] != 'QUANTITY' or old['unit'] not in ('g','개','mL')
                c.execute(text('UPDATE inventory_batch SET '+','.join(f'{k}=:{k}' for k in values)+' WHERE batch_id=:batch_id'),{**values,'batch_id':old['batch_id']})
                restored.append(old['batch_id'])
        print(json.dumps({'restored':len(restored),'skippedChangedBatches':skipped}))
        return
    output = ROOT / 'output' / ('quantity_migration_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    output.mkdir(parents=True, exist_ok=False)
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(text('SELECT * FROM inventory_batch ORDER BY batch_id')).mappings()]
        ddl = c.execute(text('SHOW CREATE TABLE inventory_batch')).first()[1]
        history = {t: digest(c,t) for t in ('meal_consumption','inventory_activity','calendar')}
    candidates = [(row, plan(row)) for row in rows if row['status'] in ('ACTIVE','EXPIRED')]
    preview = [{'batchId': row['batch_id'], 'name': row['ingredient_name'], 'beforeQuantity': row['remaining_quantity'], 'beforeUnit': row['unit'], **after} for row, after in candidates]
    (output/'preview.json').write_text(json.dumps(preview, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
    (output/'backup.json').write_text(json.dumps({'ddl':ddl,'rows':rows,'historyHashes':history}, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
    print(json.dumps({'directory':str(output), 'currentBatches':len(preview), 'needsReview':sum(x['quantity_needs_review'] for x in preview), 'applied':args.apply}, ensure_ascii=False))
    if not args.apply: return
    # MySQL DDL commits implicitly; columns are additive and restart-safe.
    with engine.begin() as c:
        columns = {r[0] for r in c.execute(text('SHOW COLUMNS FROM inventory_batch'))}
        for name, definition in {'count_basis':'VARCHAR(100) NULL', 'per_count_quantity':'DECIMAL(18,6) NULL', 'per_count_unit':'VARCHAR(16) NULL', 'quantity_needs_review':'BIT(1) NOT NULL DEFAULT 0'}.items():
            if name not in columns: c.execute(text(f'ALTER TABLE inventory_batch ADD COLUMN {name} {definition}'))
        c.execute(text('ALTER TABLE inventory_batch MODIFY remaining_quantity DECIMAL(18,6) NULL'))
    with engine.begin() as c:
        for old, after in candidates:
            current = dict(c.execute(text('SELECT * FROM inventory_batch WHERE batch_id=:id FOR UPDATE'), {'id':old['batch_id']}).mappings().one())
            if current['status'] != old['status'] or current['unit'] != old['unit'] or (None if current['remaining_quantity'] is None else float(current['remaining_quantity'])) != (None if old['remaining_quantity'] is None else round(float(old['remaining_quantity']),6)):
                raise RuntimeError('Concurrent change; data updates rolled back')
            c.execute(text('UPDATE inventory_batch SET ' + ','.join(f'{k}=:{k}' for k in after) + ' WHERE batch_id=:batch_id'), {**after,'batch_id':old['batch_id']})
        assert history == {t:digest(c,t) for t in history}, 'Historical data changed'
    print('Migration verified; historical meal/activity/calendar rows unchanged.')

if __name__ == '__main__': main()
