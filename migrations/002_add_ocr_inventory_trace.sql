-- OCR 검토 결과를 실제 재고로 전환하기 위한 추적 컬럼입니다.

ALTER TABLE ocr_scan
    ADD COLUMN reviewed_at DATETIME NULL;

ALTER TABLE ocr_scan_item
    ADD COLUMN inventory_batch_id BIGINT NULL,
    ADD COLUMN reviewed_at DATETIME NULL;

ALTER TABLE inventory_batches
    ADD COLUMN storage_type ENUM(
        'REFRIGERATED', 'FROZEN', 'ROOM_TEMPERATURE', 'UNKNOWN'
    ) NOT NULL DEFAULT 'UNKNOWN',
    ADD COLUMN source_type ENUM('MANUAL', 'OCR') NOT NULL DEFAULT 'MANUAL',
    ADD COLUMN source_ocr_item_id BIGINT NULL,
    ADD UNIQUE KEY uk_inventory_source_ocr_item (source_ocr_item_id),
    ADD CONSTRAINT fk_inventory_source_ocr_item
        FOREIGN KEY (source_ocr_item_id) REFERENCES ocr_scan_item (ocr_scan_item_id);
