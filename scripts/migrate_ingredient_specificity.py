"""Stage specificity migration and audit; --apply writes AI JSON files only, never DB."""
import argparse
import json
from pathlib import Path
from collections import Counter
from app.ingredient_matcher import IngredientCatalog, IngredientEntry, lookup_key
from scripts.reparse_recipe_ingredients import reparse_recipe_ingredients

ROOT = Path(__file__).resolve().parents[1]


def build_dictionary(entries, plan):
    children = {name: aliases for group in plan.values() for name, aliases in group.items()}
    owners = {}
    for name, aliases in children.items():
        for alias in [name, *aliases]:
            key = lookup_key(alias)
            if key in owners and owners[key] != name:
                raise ValueError(f"Ambiguous alias {alias}")
            owners[key] = name
    result = []
    existing = {entry['name']:entry for entry in entries}
    for entry in entries:
        updated = dict(entry)
        updated['aliases'] = [a for a in entry.get('aliases',[]) if lookup_key(a) not in owners or owners[lookup_key(a)] == entry['name']]
        if entry['name'] in children:
            updated['aliases'] = list(dict.fromkeys(updated['aliases'] + children[entry['name']]))
        result.append(updated)
    result.extend({'name':name,'aliases':aliases} for name,aliases in children.items() if name not in existing)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--recipes-input',type=Path)
    args = parser.parse_args()
    data = ROOT/'app/data'
    entries = json.loads((data/'ingredients_ko.json').read_text(encoding='utf-8'))
    recipes = json.loads((args.recipes_input or data/'recipes_ko.json').read_text(encoding='utf-8'))
    plan = json.loads((data/'ingredient_specificity.json').read_text(encoding='utf-8'))
    updated = build_dictionary(entries,plan)
    catalog = IngredientCatalog([IngredientEntry(e['name'],tuple(e.get('aliases',[]))) for e in updated])
    accepted,quarantined,stats = reparse_recipe_ingredients(recipes,catalog=catalog)
    old = {r['source_recipe_id']:r for r in recipes}
    changed = [{'id':r['source_recipe_id'],'name':r['recipe_name'],
                'before':old[r['source_recipe_id']]['ingredients'],'after':r['ingredients']}
               for r in accepted if old[r['source_recipe_id']]['ingredients'] != r['ingredients']]
    report = {'statistics':stats,'groups':len(plan),'specific_types':sum(map(len,plan.values())),
              'changed_recipes':len(changed),'changes':changed,'quarantined':quarantined,
              'no_source_recipe_ids':[r['source_recipe_id'] for r in recipes if not r.get('source_ingredients_raw')],
              'alias_groups_for_review':[e for e in entries if e.get('aliases')],
              'remaining_generic_usage':dict(Counter(i['canonical_name'] for r in accepted for i in r['ingredients'] if i['canonical_name'] in plan))}
    output = ROOT/'output/ingredient_specificity'
    output.mkdir(parents=True,exist_ok=True)
    for name,value in [('ingredients.candidate.json',updated),('recipes.candidate.json',accepted),('audit.json',report)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.apply:
        for name,value in [('ingredients_ko.json',updated),('recipes_ko.json',accepted)]:
            backup = output/(name+'.before.json')
            if not backup.exists():
                backup.write_bytes((data/name).read_bytes())
            (data/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('changes','alias_groups_for_review')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
