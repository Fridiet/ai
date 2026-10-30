"""Local-only, strict 1:1 history linkage. Dry-run by default; full SQL backup before apply.

Stop Spring before --apply. Existing amounts, dates, recipe keys and history rows
are never rewritten. Unmatched/ambiguous legacy history blocks the migration.
"""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

from sqlalchemy import inspect, text
from scripts.seed_development_data import _resolve_engine

ADDITIONS = {
    'calendar': {'deleted_at': 'DATETIME(6) NULL', 'activity_linked': 'BIT(1) NULL'},
    'inventory_activity': {'meal_event_id': 'BIGINT NULL', 'cancelled_at': 'DATETIME(6) NULL'},
    'recipe_recommendation_feedback': {'meal_event_id': 'BIGINT NULL'},
}
TABLES = ('calendar', 'meal_consumption', 'inventory_activity', 'recipe_recommendation_feedback', 'inventory_batch')


def unique_links(expected, candidates, label):
    """Every source must have one candidate, and no target may serve two sources."""
    grouped = defaultdict(list)
    for source, target in candidates:
        if target is not None:
            grouped[source].append(target)
    missing = sum(len(grouped[source]) == 0 for source in expected)
    ambiguous = sum(len(grouped[source]) > 1 for source in expected)
    chosen = {source: grouped[source][0] for source in expected if len(grouped[source]) == 1}
    reverse_collisions = len(chosen) - len(set(chosen.values()))
    if missing or ambiguous or reverse_collisions:
        raise ValueError(f'{label}: missing={missing}, ambiguous={ambiguous}, reverse_collisions={reverse_collisions}')
    return chosen


def columns(connection, table):
    return [c['name'] for c in inspect(connection).get_columns(table)]


def snapshot(connection):
    result = {}
    for table in TABLES:
        names = sorted(set(columns(connection, table)) - set(ADDITIONS.get(table, {})))
        pk = inspect(connection).get_pk_constraint(table)['constrained_columns']
        rows = connection.execute(text(f"SELECT {','.join('`'+n+'`' for n in names)} FROM `{table}` ORDER BY {','.join('`'+n+'`' for n in pk)}")).all()
        result[table] = {'count': len(rows), 'sha256': hashlib.sha256(
            json.dumps([list(row) for row in rows], default=str, ensure_ascii=False).encode()).hexdigest()}
    return result


def build_plan(connection):
    names = columns(connection, 'calendar')
    scope = "COALESCE(c.activity_linked,0)=0" if 'activity_linked' in names else '1=1'
    if 'deleted_at' in names:
        scope += ' AND c.deleted_at IS NULL'
    meals = list(connection.execute(text(f'SELECT c.meal_event_id FROM calendar c WHERE {scope}')).scalars())
    consumption_meals = dict(connection.execute(text(f'''SELECT m.consumption_id, m.meal_event_id
        FROM meal_consumption m JOIN calendar c ON c.meal_event_id=m.meal_event_id WHERE {scope}''')).all())
    activity_names = columns(connection, 'inventory_activity')
    activity_filter = ' AND a.cancelled_at IS NULL' if 'cancelled_at' in activity_names else ''
    if 'meal_event_id' in activity_names:
        activity_filter += ' AND (a.meal_event_id IS NULL OR a.meal_event_id=c.meal_event_id)'
    candidates = connection.execute(text(f'''SELECT m.consumption_id, a.id
        FROM meal_consumption m JOIN calendar c ON c.meal_event_id=m.meal_event_id
        LEFT JOIN inventory_activity a ON a.user_id=c.owner_user_id AND a.fridge_id=c.fridge_id
          AND a.batch_id=m.batch_id AND a.event_type='CONSUMED' AND a.occurred_at=c.eaten_at
          AND a.quantity=m.quantity_used AND a.unit=m.unit
          AND (a.unit_type=m.unit_type OR (m.unit_type='CUSTOM' AND a.unit_type='QUANTITY'
            AND m.quantity_used>0 AND m.custom_quantity_text IS NULL AND m.pre_custom_quantity_text IS NULL))
          {activity_filter}
        WHERE {scope}''')).all()
    activities = unique_links(consumption_meals, candidates, 'consumption/activity')
    feedback_filter = ''
    if 'meal_event_id' in columns(connection, 'recipe_recommendation_feedback'):
        feedback_filter = ' AND (f.meal_event_id IS NULL OR f.meal_event_id=c.meal_event_id)'
    feedback_candidates = connection.execute(text(f'''SELECT c.meal_event_id, f.feedback_id
        FROM calendar c LEFT JOIN recipe_recommendation_feedback f ON f.user_id=c.owner_user_id
          AND f.fridge_id=c.fridge_id AND f.recipe_name=c.title AND f.created_at=c.eaten_at
          AND f.event_type='COOKED' {feedback_filter}
        WHERE {scope}''')).all()
    feedback = unique_links(meals, feedback_candidates, 'meal/feedback')
    return {'meals': meals,
            'activities': [{'id': target, 'meal': consumption_meals[source]} for source, target in activities.items()],
            'feedback': [{'id': target, 'meal': meal} for meal, target in feedback.items()]}


def full_backup(engine, destination):
    executable = shutil.which('mysqldump')
    if executable is None:
        candidate = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'MySQL/MySQL Server 8.0/bin/mysqldump.exe'
        if candidate.is_file():
            executable = str(candidate)
    if executable is None:
        raise RuntimeError('mysqldump is required; no schema/data changes made.')
    env = os.environ.copy()
    env['MYSQL_PWD'] = engine.url.password or ''
    command = [executable, '--single-transaction', '--routines', '--triggers', '--events', '--hex-blob',
               '--set-gtid-purged=OFF', '--no-tablespaces', '--host=' + engine.url.host,
               '--port=' + str(engine.url.port or 3306), '--user=' + (engine.url.username or ''),
               '--result-file=' + str(destination), engine.url.database]
    result = subprocess.run(command, env=env, capture_output=True, timeout=120)
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        raise RuntimeError('Full DB backup failed; no schema/data changes made. Credentials suppressed.')


def migrate(apply=False, backup_root=None):
    engine = _resolve_engine(None)
    if engine.dialect.name != 'mysql' or engine.url.host not in {'localhost', '127.0.0.1', '::1'}:
        raise RuntimeError('Only local MySQL is supported.')
    if apply:
        if backup_root is None:
            raise ValueError('--backup-root is required for apply; choose a private directory outside the repository.')
        root = Path(backup_root).resolve()
        repo = Path(__file__).resolve().parents[2]
        if root == repo or repo in root.parents:
            raise ValueError('Backup must be outside the repository.')
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1', 8080)) == 0:
                raise RuntimeError('Stop the local Spring server before applying.')
    with engine.connect() as connection:
        if connection.execute(text("SELECT GET_LOCK('fridiet-calendar-links', 0)")).scalar() != 1:
            raise RuntimeError('Another migration is running.')
        try:
            plan = build_plan(connection)
            before = snapshot(connection)
            result = {'applied': False, 'meals': len(plan['meals']), 'activity_links': len(plan['activities']),
                      'feedback_links': len(plan['feedback'])}
            if not apply:
                return result
            folder = root / ('calendar-links-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            folder.mkdir(parents=True, exist_ok=False)
            connection.commit()
            full_backup(engine, folder / 'database.sql')
            (folder / 'manifest.json').write_text(json.dumps({'plan': plan, 'before': before}, indent=2), encoding='utf-8')
            if snapshot(connection) != before or build_plan(connection) != plan:
                raise RuntimeError('Database changed during backup. Stop all writers and retry.')
            connection.commit()
            for table, definitions in ADDITIONS.items():
                existing = columns(connection, table)
                for name, definition in definitions.items():
                    if name not in existing:
                        connection.execute(text(f'ALTER TABLE `{table}` ADD COLUMN `{name}` {definition}'))
            unique = inspect(connection).get_unique_constraints('recipe_recommendation_feedback')
            if not any(row['column_names'] == ['meal_event_id'] for row in unique):
                connection.execute(text('ALTER TABLE recipe_recommendation_feedback ADD CONSTRAINT uk_recipe_feedback_meal_event UNIQUE (meal_event_id)'))
            connection.commit()
            with connection.begin():
                for table in TABLES:
                    pk = inspect(connection).get_pk_constraint(table)['constrained_columns']
                    key = ','.join('`'+name+'`' for name in pk)
                    connection.execute(text(f'SELECT {key} FROM `{table}` ORDER BY {key} FOR UPDATE')).all()
                if snapshot(connection) != before or build_plan(connection) != plan:
                    raise RuntimeError('History changed before linkage. No linkage updates applied; keep the schema backup.')
                if plan['activities']:
                    connection.execute(text('UPDATE inventory_activity SET meal_event_id=:meal WHERE id=:id'), plan['activities'])
                if plan['feedback']:
                    connection.execute(text('UPDATE recipe_recommendation_feedback SET meal_event_id=:meal WHERE feedback_id=:id'), plan['feedback'])
                if plan['meals']:
                    connection.execute(text('UPDATE calendar SET activity_linked=1 WHERE meal_event_id=:id'), [{'id': i} for i in plan['meals']])
                if snapshot(connection) != before:
                    raise RuntimeError('Historical contents changed; linkage transaction rolled back.')
                if build_plan(connection)['meals']:
                    raise RuntimeError('Unlinked history remains; linkage transaction rolled back.')
            result.update(applied=True, backup_directory=str(folder), historical_contents_unchanged=True)
            (folder / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            return result
        finally:
            connection.execute(text("SELECT RELEASE_LOCK('fridiet-calendar-links')"))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-root', type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.apply, args.backup_root), ensure_ascii=False, indent=2))
