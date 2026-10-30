import pytest
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Float, UniqueConstraint, select
from scripts.sync_specificity_database import synchronize, alias_targets, ingredient_key


def fixture_db():
    engine = create_engine('sqlite://')
    m = MetaData()
    Table('ingredient_master', m, Column('ingredient_id', Integer, primary_key=True),
          Column('canonical_name', String, unique=True), Column('ingredient_type', String),
          Column('default_unit', String), Column('category', String))
    Table('ingredient_alias', m, Column('alias_id', Integer, primary_key=True),
          Column('alias_text', String), Column('ingredient_id', Integer), Column('confidence', Float))
    Table('recipe_catalog', m, Column('recipe_catalog_id', Integer, primary_key=True),
          Column('source', String), Column('source_recipe_id', String), Column('recipe_name', String))
    Table('recipe_catalog_ingredients', m, Column('recipe_catalog_ingredient_id', Integer, primary_key=True),
          Column('recipe_catalog_id', Integer), Column('ingredient_id', Integer),
          Column('canonical_name', String), Column('quantity', Float), Column('unit', String),
          Column('optional', Integer), UniqueConstraint('recipe_catalog_id', 'ingredient_id'))
    m.create_all(engine)
    with engine.begin() as c:
        c.execute(m.tables['ingredient_master'].insert(), dict(ingredient_id=5, canonical_name='버섯'))
        c.execute(m.tables['ingredient_alias'].insert(), dict(alias_text='팽이', ingredient_id=5))
        c.execute(m.tables['recipe_catalog'].insert(), dict(recipe_catalog_id=8,
            source='FRIDIET_CURATED_MVP', source_recipe_id='test', recipe_name='국'))
        c.execute(m.tables['recipe_catalog_ingredients'].insert(), dict(recipe_catalog_ingredient_id=10,
            recipe_catalog_id=8, ingredient_id=5, canonical_name='버섯', quantity=30, unit='g', optional=0))
    return engine, m.tables


def recipe(name='팽이버섯'):
    return [dict(source_recipe_id='test', recipe_name='국', ingredients=[dict(canonical_name=name,
                 quantity=50, unit='g', optional=False)])]


def test_sync_specific_alias_and_idempotence():
    engine, tables = fixture_db()
    entries = [dict(name='버섯', aliases=[]), dict(name='팽이버섯', aliases=['팽이'])]
    with engine.begin() as c:
        dry = synchronize(c, tables, entries, recipe())
        assert dry['masters_inserted'] == 1
        assert len(c.execute(select(tables['ingredient_master'])).all()) == 1
        synchronize(c, tables, entries, recipe(), True)
        assert not any(synchronize(c, tables, entries, recipe()).values())
        master = dict(c.execute(select(tables['ingredient_master'].c.canonical_name,
                                      tables['ingredient_master'].c.ingredient_id)).all())
        assert master['버섯'] == 5
        assert master['팽이버섯'] != 5
        link = c.execute(select(tables['recipe_catalog_ingredients'])).mappings().one()
        assert link['recipe_catalog_id'] == 8
        assert link['canonical_name'] == '팽이버섯'
        alias = c.execute(select(tables['ingredient_alias']).where(
            tables['ingredient_alias'].c.alias_text == '팽이')).mappings().one()
        assert alias['ingredient_id'] == master['팽이버섯']


def test_quantity_change_preserves_link_id():
    engine, tables = fixture_db()
    with engine.begin() as c:
        result = synchronize(c, tables, [dict(name='버섯', aliases=[])], recipe('버섯'), True)
        assert result['recipe_links_updated'] == 1
        row = c.execute(select(tables['recipe_catalog_ingredients'])).mappings().one()
        assert row['recipe_catalog_ingredient_id'] == 10
        assert row['quantity'] == 50


def test_ambiguous_alias_rejected():
    with pytest.raises(ValueError, match='Ambiguous alias'):
        alias_targets([dict(name='버섯', aliases=['팽이']), dict(name='팽이버섯', aliases=['팽이'])])


def test_mysql_quantity_precision():
    assert ingredient_key(dict(canonical_name='소금', quantity=0.3333)) == ingredient_key(
        dict(canonical_name='소금', quantity=0.33))


def test_failure_rolls_back_inserted_master():
    engine, tables = fixture_db()
    with pytest.raises(ValueError, match='Catalog identity'):
        with engine.begin() as c:
            synchronize(c, tables, [dict(name='팽이버섯', aliases=[])],
                        [dict(source_recipe_id='missing', recipe_name='국')], True)
    with engine.connect() as c:
        assert len(c.execute(select(tables['ingredient_master'])).all()) == 1
