from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "PaddleOCR FastAPI"
    ocr_lang: str = os.getenv("OCR_LANG", "korean")
    ocr_version: str = os.getenv("OCR_VERSION", "PP-OCRv5")
    device: str = os.getenv("OCR_DEVICE", "cpu")
    ocr_detection_max_side: int = int(os.getenv("OCR_DETECTION_MAX_SIDE", "0"))
    ocr_secondary_counts_enabled: bool = os.getenv("OCR_SECONDARY_COUNTS_ENABLED", "false").lower() in {"1", "true", "yes"}
    ocr_numeric_cells_enabled: bool = os.getenv("OCR_NUMERIC_CELLS_ENABLED", "false").lower() in {"1", "true", "yes"}
    ocr_header_recovery_enabled: bool = os.getenv("OCR_HEADER_RECOVERY_ENABLED", "false").lower() in {"1", "true", "yes"}
    ocr_secondary_name_hints_enabled: bool = os.getenv("OCR_SECONDARY_NAME_HINTS_ENABLED", "false").lower() in {"1", "true", "yes"}
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
    ingredient_dictionary_path: str | None = os.getenv("INGREDIENT_DICTIONARY_PATH")
    product_catalog_db_path: str = os.getenv(
        "PRODUCT_CATALOG_DB_PATH",
        str(Path(__file__).with_name("data") / "products.sqlite3"),
    )
    mfds_api_key: str | None = os.getenv("MFDS_API_KEY")
    mfds_timeout_seconds: float = float(os.getenv("MFDS_TIMEOUT_SECONDS", "3"))
    database_url: str | None = os.getenv("DATABASE_URL")
    database_auto_create: bool = os.getenv("DATABASE_AUTO_CREATE", "false").lower() in {
        "1",
        "true",
        "yes",
    }


settings = Settings()
