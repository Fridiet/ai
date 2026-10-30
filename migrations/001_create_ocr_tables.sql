-- MySQL 8.x / Fridiet DB
-- OCR 결과는 사용자 검토 전까지 실제 inventory_batch에 넣지 않습니다.

CREATE TABLE IF NOT EXISTS ocr_scan (
    ocr_scan_id BIGINT NOT NULL AUTO_INCREMENT,
    fridge_id BIGINT NOT NULL,
    created_by BIGINT NOT NULL,
    filename VARCHAR(255) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_REVIEW',
    item_count INT NOT NULL,
    mapped_item_count INT NOT NULL DEFAULT 0,
    result_json LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ocr_scan_id),
    INDEX idx_ocr_scan_fridge_id (fridge_id),
    INDEX idx_ocr_scan_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ocr_scan_item (
    ocr_scan_item_id BIGINT NOT NULL AUTO_INCREMENT,
    ocr_scan_id BIGINT NOT NULL,
    row_index INT NOT NULL,
    ingredient_id BIGINT NULL,
    raw_text LONGTEXT NOT NULL,
    recognized_product_name VARCHAR(500) NOT NULL,
    normalized_product_name VARCHAR(500) NOT NULL,
    canonical_name VARCHAR(255) NOT NULL,
    match_confidence DOUBLE NOT NULL,
    remaining_quantity DOUBLE NOT NULL,
    unit VARCHAR(32) NOT NULL,
    consume_by_date DATE NULL,
    storage_type VARCHAR(32) NOT NULL,
    review_status VARCHAR(32) NOT NULL DEFAULT 'PENDING_REVIEW',
    candidate_json LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ocr_scan_item_id),
    UNIQUE KEY uk_ocr_scan_item_row (ocr_scan_id, row_index),
    INDEX idx_ocr_scan_item_scan_id (ocr_scan_id),
    INDEX idx_ocr_scan_item_ingredient_id (ingredient_id),
    CONSTRAINT fk_ocr_scan_item_scan
        FOREIGN KEY (ocr_scan_id) REFERENCES ocr_scan (ocr_scan_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
