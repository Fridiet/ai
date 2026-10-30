from datetime import date
import pytest
from sqlalchemy import create_engine,text
from app.database import (create_ocr_tables,save_inventory_candidates,confirm_inventory_candidates,
    OcrContextValidationError,OcrScanAlreadyReviewedError,InventoryReviewValidationError)
from tests.test_database import sample_result
from app.team_ocr_database import require_schema


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv('AI_DB_SCHEMA','team')
    e=create_engine('sqlite+pysqlite:///:memory:')
    create_ocr_tables(e)
    with e.begin() as c:
        c.execute(text('CREATE TABLE `user` (user_id INTEGER PRIMARY KEY,is_deleted INTEGER NOT NULL)'))
        c.execute(text('INSERT INTO `user` VALUES (1,0),(2,0),(3,0),(4,1)'))
        c.execute(text('CREATE TABLE fridge (fridge_id INTEGER PRIMARY KEY,owner_user_id INTEGER NOT NULL)'))
        c.execute(text('INSERT INTO fridge VALUES (7,1)'))
        c.execute(text('CREATE TABLE fridge_member (fridge_id INTEGER,user_id INTEGER,status TEXT)'))
        c.execute(text("INSERT INTO fridge_member VALUES (7,2,'ACCEPTED'),(7,3,'PENDING')"))
        c.execute(text('CREATE TABLE ingredient_master (ingredient_id INTEGER PRIMARY KEY,canonical_name TEXT NOT NULL)'))
        c.execute(text("INSERT INTO ingredient_master VALUES (4,'우유')"))
        c.execute(text('CREATE TABLE inventory_batch (batch_id INTEGER PRIMARY KEY AUTOINCREMENT,fridge_id INTEGER NOT NULL,ingredient_id INTEGER,ingredient_name TEXT NOT NULL,consume_by_date DATE NOT NULL,remaining_quantity NUMERIC(18,6),unit TEXT,unit_type TEXT NOT NULL,location_category TEXT,status TEXT NOT NULL,created_by INTEGER NOT NULL,created_at DATETIME,updated_at DATETIME,quantity_needs_review INTEGER NOT NULL)'))
    return e


def saved(e,user=1):
    return save_inventory_candidates(sample_result(),fridge_id=7,created_by=user,engine=e)


def review(result,**changes):
    return dict(ocr_scan_item_id=result['items'][0]['ocr_scan_item_id'],decision='CONFIRM',
                ingredient_id=4,remaining_quantity=1.25,unit='L',consume_by_date=date(2026,10,20),
                storage_type='REFRIGERATED',**changes)


def count(e,table):
    with e.connect() as c:return c.scalar(text('SELECT count(*) FROM '+table))


@pytest.mark.parametrize('user',[1,2])
def test_save_and_confirm_current_schema(db,user):
    require_schema(db)
    result=saved(db,user)
    assert count(db,'inventory_batch')==0
    response=confirm_inventory_candidates(result['ocr_scan_id'],created_by=user,review_items=[review(result)],engine=db)
    assert response['batches'][0]['remaining_quantity']==1250
    assert response['batches'][0]['unit']=='mL'
    with db.connect() as c:
        row=c.execute(text('SELECT * FROM inventory_batch')).mappings().one()
        assert row['ingredient_name']=='우유'
        assert row['unit_type']=='QUANTITY'
        assert row['quantity_needs_review']==0
    with pytest.raises(OcrScanAlreadyReviewedError):
        confirm_inventory_candidates(result['ocr_scan_id'],created_by=user,review_items=[review(result)],engine=db)
    assert count(db,'inventory_batch')==1


@pytest.mark.parametrize('user',[3,4,99])
def test_access_fails_closed(db,user):
    with pytest.raises(OcrContextValidationError): saved(db,user)
    assert count(db,'ocr_scan')==0


def test_membership_rechecked_at_confirmation(db):
    result=saved(db,2)
    with db.begin() as c:c.execute(text("UPDATE fridge_member SET status='LEFT' WHERE user_id=2"))
    with pytest.raises(OcrContextValidationError):
        confirm_inventory_candidates(result['ocr_scan_id'],created_by=2,review_items=[review(result)],engine=db)
    assert count(db,'inventory_batch')==0


@pytest.mark.parametrize('change',[{'consume_by_date':None},{'unit':'%'},{'remaining_quantity':float('inf')},
    {'ingredient_id':999},{'storage_type':'UNKNOWN'}])
def test_validation_rollback(db,change):
    result=saved(db)
    item=review(result);item.update(change)
    with pytest.raises(InventoryReviewValidationError):
        confirm_inventory_candidates(result['ocr_scan_id'],created_by=1,review_items=[item],engine=db)
    assert count(db,'inventory_batch')==0
    with db.connect() as c:assert c.scalar(text('SELECT status FROM ocr_scan'))=='PENDING_REVIEW'


def test_wrong_reviewer_cannot_write(db):
    result=saved(db)
    with pytest.raises(OcrContextValidationError):
        confirm_inventory_candidates(result['ocr_scan_id'],created_by=2,review_items=[review(result)],engine=db)


def test_multi_item_error_rolls_back_first_insert(db):
    result=saved(db)
    with db.begin() as c:
        c.execute(text('INSERT INTO ocr_scan_item (ocr_scan_id,row_index,raw_text,recognized_product_name,normalized_product_name,canonical_name,match_confidence,remaining_quantity,unit,storage_type,review_status,candidate_json,created_at) SELECT ocr_scan_id,1,raw_text,recognized_product_name,normalized_product_name,canonical_name,match_confidence,remaining_quantity,unit,storage_type,review_status,candidate_json,created_at FROM ocr_scan_item'))
        second=c.scalar(text('SELECT max(ocr_scan_item_id) FROM ocr_scan_item'))
    invalid=review(result);invalid.update(ocr_scan_item_id=second,ingredient_id=999)
    with pytest.raises(InventoryReviewValidationError):
        confirm_inventory_candidates(result['ocr_scan_id'],created_by=1,review_items=[review(result),invalid],engine=db)
    assert count(db,'inventory_batch')==0
    with db.connect() as c:assert c.scalar(text("SELECT count(*) FROM ocr_scan_item WHERE review_status='CONFIRMED'"))==0
