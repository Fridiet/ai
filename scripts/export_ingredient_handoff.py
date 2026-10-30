"""Synchronize local reference tables only and export ID-independent JSON/MySQL SQL."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from sqlalchemy import MetaData, Table, select
from scripts.seed_development_data import _resolve_engine
from scripts.sync_specificity_database import alias_targets
from app.ingredient_matcher import _dictionary_path
from app.inventory_mapper import infer_ingredient_type, infer_default_unit
from app.receipt_mapper import infer_receipt_category

OUT = Path(__file__).resolve().parents[1]/'output/ingredient_handoff_20260923'

def sqlstr(value):
    return 'NULL' if value is None else "CONVERT(0x"+str(value).encode('utf-8').hex()+" USING utf8mb4)"

def main(apply=False):
    engine=_resolve_engine(None)
    assert engine.url.host in {'localhost','127.0.0.1','::1'}
    meta=MetaData()
    master=Table('ingredient_master',meta,autoload_with=engine)
    alias=Table('ingredient_alias',meta,autoload_with=engine)
    entries=json.loads(_dictionary_path().read_text(encoding='utf-8'))
    targets=alias_targets(entries)
    with engine.begin() as db:
        before_m=[dict(r) for r in db.execute(select(master)).mappings()]
        before_a=[dict(r) for r in db.execute(select(alias)).mappings()]
        ids={r['canonical_name']:r['ingredient_id'] for r in before_m}
        grouped={}
        for r in before_a: grouped.setdefault(r['alias_text'],[]).append(r)
        if any(len(v)>1 for v in grouped.values()): raise ValueError('Duplicate alias labels require review')
        changes={'new_masters':[e['name'] for e in entries if e['name'] not in ids],
            'new_aliases':[a for a in targets if a not in grouped],
            'remapped_aliases':[a for a,n in targets.items() if a in grouped and grouped[a][0]['ingredient_id']!=ids.get(n)]}
        print(json.dumps(changes,ensure_ascii=False,indent=2))
        if not apply: return
        OUT.mkdir(parents=True,exist_ok=True)
        backup=OUT.parent/('ingredient_reference_backup_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.json')
        backup.write_text(json.dumps({'master':before_m,'alias':before_a},ensure_ascii=False,default=str,indent=2),encoding='utf-8')
        for name in changes['new_masters']:
            kind=infer_ingredient_type(name,None)
            values=dict(canonical_name=name,ingredient_type=kind,default_unit=infer_default_unit(name,kind))
            values['category']=infer_receipt_category({'ingredient_master_candidate':values,'classification':'fresh_food'})
            ids[name]=db.execute(master.insert().values(**values)).inserted_primary_key[0]
        for label,name in targets.items():
            if label not in grouped:
                db.execute(alias.insert().values(alias_text=label,ingredient_id=ids[name],confidence=1.0))
            elif label in changes['remapped_aliases']:
                db.execute(alias.update().where(alias.c.alias_id==grouped[label][0]['alias_id']).values(ingredient_id=ids[name],confidence=1.0))
        masters=[dict(r) for r in db.execute(select(master).order_by(master.c.canonical_name)).mappings()]
        names={r['ingredient_id']:r['canonical_name'] for r in masters}
        aliases=[dict(r) for r in db.execute(select(alias).order_by(alias.c.alias_text)).mappings()]
        assert all(names[r['ingredient_id']]==targets[r['alias_text']] for r in aliases if r['alias_text'] in targets)
        assert all(next(x for x in masters if x['canonical_name']==r['canonical_name'])==r for r in before_m)
        portable_m=[{k:r[k] for k in ['canonical_name','ingredient_type','default_unit','category']} for r in masters]
        portable_a=[dict(alias_text=r['alias_text'],canonical_name=names[r['ingredient_id']],confidence=None if r['confidence'] is None else float(r['confidence'])) for r in aliases]
    for filename,data in [('ingredient_master.json',portable_m),('ingredient_alias.json',portable_a),('sync_report.json',changes)]:
        (OUT/filename).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['-- MySQL 8, existing ingredient_master / ingredient_alias tables required.',
           '-- BACK UP reference tables first. Review existing alias conflicts before import.',
           '-- No local IDs are imported. Existing master attributes are preserved.',
           'SET NAMES utf8mb4;', 'START TRANSACTION;']
    for r in portable_m:
        cols=list(r)
        lines.append('INSERT INTO ingredient_master ('+','.join(cols)+') SELECT '+','.join(sqlstr(r[k]) for k in cols)+' WHERE NOT EXISTS (SELECT 1 FROM ingredient_master WHERE canonical_name='+sqlstr(r['canonical_name'])+');')
    for r in portable_a:
        # Existing aliases are deliberately not overwritten in a teammate database.
        lines.append('INSERT INTO ingredient_alias (alias_text,ingredient_id,confidence) SELECT '+sqlstr(r['alias_text'])+',m.ingredient_id,'+('NULL' if r['confidence'] is None else str(float(r['confidence'])))+' FROM ingredient_master m WHERE m.canonical_name='+sqlstr(r['canonical_name'])+' AND NOT EXISTS (SELECT 1 FROM ingredient_alias WHERE alias_text='+sqlstr(r['alias_text'])+');')
    lines.append('COMMIT;')
    (OUT/'import_mysql.sql').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    conflict_sql = "SELECT incoming.alias_text, existing.canonical_name AS existing_target, incoming.canonical_name AS incoming_target FROM JSON_TABLE("+sqlstr(json.dumps(portable_a,ensure_ascii=False))+", '$[*]' COLUMNS(alias_text VARCHAR(255) PATH '$.alias_text', canonical_name VARCHAR(255) PATH '$.canonical_name')) incoming JOIN ingredient_alias a ON a.alias_text=incoming.alias_text JOIN ingredient_master existing ON existing.ingredient_id=a.ingredient_id WHERE existing.canonical_name<>incoming.canonical_name;\n"
    (OUT/'check_alias_conflicts.sql').write_text(conflict_sql,encoding='utf-8')
    print(json.dumps(dict(masters=len(masters),aliases=len(aliases),output=str(OUT),backup=str(backup)),ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');main(p.parse_args().apply)
