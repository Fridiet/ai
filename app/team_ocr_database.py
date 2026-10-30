"""OCR persistence adapter for Spring's singular tables. No implicit DDL."""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from sqlalchemy import inspect, text
from sqlalchemy.types import Numeric
from sqlalchemy.orm import Session
from .database import (DatabaseSchemaMissingError, InventoryReviewValidationError,
    OcrContextValidationError, OcrScanNotFoundError, OcrScanAlreadyReviewedError)
from .quantity_balance import UNITS

REQUIRED = {
    'user': {'user_id', 'is_deleted'},
    'fridge': {'fridge_id', 'owner_user_id'},
    'fridge_member': {'fridge_id', 'user_id', 'status'},
    'ingredient_master': {'ingredient_id', 'canonical_name'},
    'inventory_batch': {'batch_id','fridge_id','ingredient_id','ingredient_name','consume_by_date',
        'remaining_quantity','unit','unit_type','location_category','status','created_by',
        'created_at','updated_at','quantity_needs_review'},
    'ocr_scan': {'ocr_scan_id','fridge_id','created_by','status','reviewed_at','filename',
                 'item_count','mapped_item_count','result_json','created_at'},
    'ocr_scan_item': {'ocr_scan_item_id','ocr_scan_id','row_index','ingredient_id','raw_text',
        'recognized_product_name','normalized_product_name','canonical_name','match_confidence',
        'remaining_quantity','unit','consume_by_date','storage_type','review_status',
        'candidate_json','created_at','inventory_batch_id','reviewed_at'},
}


def require_schema(engine):
    inspector=inspect(engine)
    tables=set(inspector.get_table_names())
    for name, required in REQUIRED.items():
        if name not in tables:
            raise DatabaseSchemaMissingError(f'Team OCR DB missing table: {name}')
        missing=required-{c['name'] for c in inspector.get_columns(name)}
        if missing:
            raise DatabaseSchemaMissingError(f'Team OCR DB {name} missing columns: '+', '.join(sorted(missing)))
    if engine.dialect.name == 'mysql':
        column=next(c for c in inspector.get_columns('inventory_batch') if c['name']=='remaining_quantity')
        numeric=column['type']
        if not isinstance(numeric,Numeric) or numeric.precision!=18 or numeric.scale!=6:
            raise DatabaseSchemaMissingError('inventory_batch.remaining_quantity must be DECIMAL(18,6)')
        # Row locking and atomic confirmation require transactional storage.
        with engine.connect() as connection:
            rows=connection.execute(text('SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()')).all()
        if any(str(storage).upper()!='INNODB' for name,storage in rows if name in REQUIRED):
            raise DatabaseSchemaMissingError('Team OCR tables must use InnoDB')


def validate_context(session, fridge_id, user_id):
    user=session.execute(text('SELECT user_id FROM `user` WHERE user_id=:u AND is_deleted=0'),{'u':user_id}).first()
    if user is None:
        raise OcrContextValidationError('존재하지 않거나 삭제된 사용자입니다.')
    allowed=session.execute(text("SELECT f.fridge_id FROM fridge f WHERE f.fridge_id=:f AND "
        "(f.owner_user_id=:u OR EXISTS (SELECT 1 FROM fridge_member m WHERE m.fridge_id=f.fridge_id "
        "AND m.user_id=:u AND m.status='ACCEPTED'))"),{'u':user_id,'f':fridge_id}).first()
    if allowed is None:
        raise OcrContextValidationError('해당 냉장고에 접근 권한이 없습니다.')


def normalized_quantity(value, unit):
    spec=UNITS.get(str(unit or '').strip().lower())
    try:
        amount=Decimal(str(value))
        if not spec or not amount.is_finite() or amount<=0:
            raise ValueError()
        amount=(amount*spec[1]).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP)
        if not 0<amount<Decimal('1e12'):
            raise ValueError()
    except (ValueError, InvalidOperation, TypeError):
        raise InventoryReviewValidationError('수량과 단위(g·개·mL)를 확인해주세요.') from None
    return amount, {'mass':'g','volume':'mL','count':'개'}[spec[0]]


def confirm(engine, scan_id, user_id, review_items):
    require_schema(engine)
    requested={i['ocr_scan_item_id']:i for i in review_items}
    if len(requested)!=len(review_items):
        raise InventoryReviewValidationError('동일한 OCR 항목이 중복되었습니다.')
    suffix='' if engine.dialect.name=='sqlite' else ' FOR UPDATE'
    now=datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)
    batches=[]; rejected=0
    with Session(engine) as session, session.begin():
        scan=session.execute(text('SELECT ocr_scan_id,fridge_id,created_by,status FROM ocr_scan WHERE ocr_scan_id=:id'+suffix),{'id':scan_id}).mappings().one_or_none()
        if scan is None: raise OcrScanNotFoundError('존재하지 않는 OCR 결과입니다.')
        validate_context(session,scan['fridge_id'],user_id)
        if scan['created_by']!=user_id: raise OcrContextValidationError('OCR 요청 사용자와 검토 사용자가 일치하지 않습니다.')
        if scan['status']!='PENDING_REVIEW': raise OcrScanAlreadyReviewedError('이미 검토가 완료된 OCR 결과입니다.')
        stored=session.execute(text('SELECT ocr_scan_item_id FROM ocr_scan_item WHERE ocr_scan_id=:id'+suffix),{'id':scan_id}).scalars().all()
        if set(stored)!=set(requested): raise InventoryReviewValidationError('저장된 모든 OCR 항목에 대한 결정이 필요합니다.')
        for item_id in stored:
            review=requested[item_id]
            if review['decision']=='REJECT':
                session.execute(text("UPDATE ocr_scan_item SET review_status='REJECTED',reviewed_at=:time WHERE ocr_scan_item_id=:id"),{'id':item_id,'time':now})
                rejected+=1; continue
            if review['decision']!='CONFIRM': raise InventoryReviewValidationError('지원하지 않는 검수 결정입니다.')
            name=session.execute(text('SELECT canonical_name FROM ingredient_master WHERE ingredient_id=:id'),{'id':review.get('ingredient_id')}).scalar_one_or_none()
            if name is None: raise InventoryReviewValidationError('존재하지 않는 재료 ID입니다.')
            amount,unit=normalized_quantity(review.get('remaining_quantity'),review.get('unit'))
            location={'REFRIGERATED':'REFRIGERATED','FROZEN':'FROZEN','ROOM_TEMPERATURE':'ROOM_TEMP'}.get(review.get('storage_type'))
            if location is None or review.get('consume_by_date') is None:
                raise InventoryReviewValidationError('보관 위치와 소비기한을 확정해주세요.')
            values=dict(f=scan['fridge_id'],i=review['ingredient_id'],name=name,d=review['consume_by_date'],
                        q=amount,u=unit,loc=location,user=user_id,time=now)
            # DBAPI SQLite cannot bind Decimal; production MySQL binds Decimal exactly.
            if engine.dialect.name=='sqlite': values['q']=str(amount)
            result=session.execute(text("INSERT INTO inventory_batch (fridge_id,ingredient_id,ingredient_name,consume_by_date,remaining_quantity,unit,unit_type,location_category,status,created_by,created_at,updated_at,quantity_needs_review) VALUES (:f,:i,:name,:d,:q,:u,'QUANTITY',:loc,'ACTIVE',:user,:time,:time,0)"),values)
            batch_id=result.lastrowid
            session.execute(text("UPDATE ocr_scan_item SET ingredient_id=:i,remaining_quantity=:q,unit=:u,consume_by_date=:d,storage_type=:storage,review_status='CONFIRMED',inventory_batch_id=:batch,reviewed_at=:time WHERE ocr_scan_item_id=:id"),dict(i=review['ingredient_id'],q=values['q'],u=unit,d=review['consume_by_date'],storage=review['storage_type'],batch=batch_id,time=now,id=item_id))
            batches.append(dict(batch_id=batch_id,ocr_scan_item_id=item_id,ingredient_id=review['ingredient_id'],remaining_quantity=float(amount),unit=unit,consume_by_date=review['consume_by_date'],storage_type=review['storage_type']))
        session.execute(text("UPDATE ocr_scan SET status='REVIEWED',reviewed_at=:time WHERE ocr_scan_id=:id"),{'id':scan_id,'time':now})
    return dict(ocr_scan_id=scan_id,status='REVIEWED',created_batch_count=len(batches),rejected_item_count=rejected,batches=batches)
