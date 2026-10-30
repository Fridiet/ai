import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, status, Depends
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .container_estimate import router as container_estimate_router
from .database import (
    DatabaseNotConfiguredError,
    DatabaseSchemaMissingError,
    InventoryReviewValidationError,
    OcrContextValidationError,
    OcrScanAlreadyReviewedError,
    OcrScanNotFoundError,
    confirm_inventory_candidates,
    save_inventory_candidates,
)
from .inventory_mapper import build_inventory_candidates
from .ocr import run_ocr
from .product_pipeline import analyze_ocr_items
from .online_order import is_order_layout, refine_order_titles, analyze_order_items
from .receipt_mapper import build_receipt_items, build_review_candidates
from .receipt_shelf_life import fill_missing_dates, finalize_registration_items
from .receipt_llm import correct_receipt
from .receipt_count_fusion import needs_secondary_count_recovery, supplement_product_counts
from .receipt_name_recovery import needs_secondary_name_hint, attach_name_hints
from .receipt_numeric_runtime import recover_numeric_counts
from .receipt_header_recovery import header_seed, recover_header
from .recommendation import recommend_recipes
from .schemas import (
    AIRecipeRequest,
    AIRecipeResponse,
    InventoryCandidatesResponse,
    InventoryCandidatesSaveResponse,
    InventoryReviewRequest,
    InventoryReviewResponse,
    OCRResponse,
    RecommendationRequest,
    RecommendationResponse,
    ReceiptOCRResponse,
)
from .team_recipe import TeamRecipeNotFoundError, generate_team_recipe
from .service_auth import require_service_token, require_db_writer

@asynccontextmanager
async def lifespan(app: FastAPI):
    from .ocr_classification import warmup
    await asyncio.to_thread(warmup)
    if os.getenv('OCR_STARTUP_WARMUP', 'false').lower() in {'true', '1', 'yes'}:
        from .ocr import warmup_engines
        await asyncio.to_thread(warmup_engines)
    logging.getLogger('uvicorn.error.ocr_llm').info(
        'OCR llm configuration enabled=%s key_configured=%s scope=whole_product max_rows=20',
        os.getenv('OCR_LLM_ENABLED', 'false').lower() in {'true', '1', 'yes'},
        bool(os.getenv('OPENAI_API_KEY', '').strip()))
    yield


app = FastAPI(title=settings.app_name, version="0.13.0", lifespan=lifespan)
app.include_router(container_estimate_router)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8081"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


async def _decode_upload(file: UploadFile) -> np.ndarray:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="지원하지 않는 이미지 형식입니다.")

    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="업로드 파일이 너무 큽니다.")

    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="이미지를 해석할 수 없습니다.")
    return image


async def _recognize_upload(file: UploadFile) -> list[dict]:
    image = await _decode_upload(file)

    try:
        started = time.perf_counter()
        result = await asyncio.to_thread(run_ocr, image)
        logging.getLogger("uvicorn.error.ocr_pipeline").info(
            "OCR image recognition seconds=%.3f boxes=%d", time.perf_counter() - started, len(result))
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OCR 처리에 실패했습니다: {exc}") from exc


async def _supplement_receipt_header(file: UploadFile, items: list) -> list:
    if not settings.ocr_header_recovery_enabled or header_seed(items) is None:
        return items
    started=time.perf_counter()
    logger=logging.getLogger('uvicorn.error.ocr_pipeline')
    try:
        await file.seek(0)
        image=await _decode_upload(file)
        result=await asyncio.to_thread(recover_header,image,items)
        logger.info('OCR header_recovery seconds=%.3f added_labels=%d',
                    time.perf_counter()-started,len(result)-len(items))
        return result
    except Exception:
        logger.exception('OCR header_recovery failed; retaining original OCR')
        return items


async def _supplement_receipt_counts(file: UploadFile, analysis: dict) -> dict:
    counts_requested = settings.ocr_secondary_counts_enabled and needs_secondary_count_recovery(analysis)
    hints_requested = settings.ocr_secondary_name_hints_enabled and needs_secondary_name_hint(analysis)
    if (not (counts_requested or hints_requested) or settings.ocr_detection_max_side == 960):
        return analysis
    started = time.perf_counter()
    logger = logging.getLogger('uvicorn.error.ocr_pipeline')
    try:
        await file.seek(0)
        image = await _decode_upload(file)
        items = await asyncio.to_thread(run_ocr, image, detection_max_side=960)
        secondary = await asyncio.to_thread(analyze_ocr_items, items, 0.35, 0.72)
        result = attach_name_hints(analysis, secondary) if hints_requested else analysis
        result = supplement_product_counts(result, secondary) if counts_requested else result
        if counts_requested and settings.ocr_numeric_cells_enabled:
            try:
                result = await asyncio.to_thread(recover_numeric_counts, image, result, secondary, items)
            except Exception:
                logger.exception('OCR numeric_cells failed; retaining prior count recovery')
        logger.info('OCR secondary_counts seconds=%.3f recovered=%d numeric_recovered=%d name_hints=%d', time.perf_counter()-started,
                    sum(p.get('purchase_quantity_source') == 'SECONDARY_VERIFIED_RECEIPT_COLUMNS' for p in result['products']),
                    sum(p.get('purchase_quantity_source') == 'NUMERIC_CELLS_VERIFIED' for p in result['products']),
                    sum(bool(p.get('secondary_name_hint')) for p in result['products']))
        return result
    except Exception:
        # Secondary inference is optional; preserve usable primary results.
        logger.exception('OCR secondary_counts failed after %.3fs; retaining primary results', time.perf_counter()-started)
        return analysis


@app.post("/ocr/raw", response_model=OCRResponse)
async def recognize_raw(file: UploadFile = File(...)) -> OCRResponse:
    items = await _recognize_upload(file)
    return OCRResponse(
        filename=file.filename or "upload",
        text="\n".join(item["text"] for item in items),
        count=len(items),
        items=items,
    )


@app.post("/ocr/inventory-candidates", response_model=InventoryCandidatesResponse)
async def recognize_inventory_candidates(
    file: UploadFile = File(...),
    min_confidence: float = Query(default=0.35, ge=0.0, le=1.0),
    match_threshold: float = Query(default=0.72, ge=0.0, le=1.0),
) -> InventoryCandidatesResponse:
    items = await _recognize_upload(file)
    analysis = await asyncio.to_thread(
        analyze_ocr_items,
        items,
        min_confidence,
        match_threshold,
    )
    candidates = build_inventory_candidates(analysis)
    return InventoryCandidatesResponse(
        filename=file.filename or "upload",
        count=len(candidates),
        inventory_candidates=candidates,
    )


@app.post("/ocr", response_model=ReceiptOCRResponse)
@app.post("/api/ocr/receipt", response_model=ReceiptOCRResponse)
async def recognize_receipt(file: UploadFile = File(...)) -> ReceiptOCRResponse:
    started = time.perf_counter()
    items = await _recognize_upload(file)
    if is_order_layout(items):
        try:
            await file.seek(0)
            image = await _decode_upload(file)
            items = await asyncio.to_thread(refine_order_titles, image, items)
        except Exception:
            logging.getLogger('uvicorn.error.ocr_pipeline').warning('Order capacity recovery failed; retaining original quantities')
        analysis = await asyncio.to_thread(analyze_order_items, items)
    else:
        items = await _supplement_receipt_header(file, items)
        analysis = await asyncio.to_thread(analyze_ocr_items, items, 0.35, 0.72)
        analysis = await _supplement_receipt_counts(file, analysis)
    analysis = await correct_receipt(analysis)
    candidates = build_inventory_candidates(analysis)
    receipt_items = await fill_missing_dates(build_receipt_items(candidates), include_existing=True)
    receipt_items = finalize_registration_items(receipt_items)
    response = ReceiptOCRResponse(items=receipt_items, reviewCandidates=build_review_candidates(analysis))
    logging.getLogger('uvicorn.error.ocr_pipeline').info(
        'OCR receipt total_seconds=%.3f accepted=%d review=%d observed_counts=%d reconstructed_counts=%d',
        time.perf_counter()-started, len(response.items), len(response.reviewCandidates),
        sum(p.get('purchase_quantity_source') == 'VERIFIED_RECEIPT_COLUMNS' for p in analysis['products']),
        sum(p.get('purchase_quantity_source') == 'RECONSTRUCTED_PRICE_COLUMNS' for p in analysis['products']))
    return response


@app.post("/api/recipes/ai", response_model=AIRecipeResponse)
async def create_ai_recipe(request: AIRecipeRequest) -> AIRecipeResponse:
    try:
        result = await asyncio.to_thread(
            generate_team_recipe,
            health_condition=request.healthCondition,
            health_goal=request.healthGoal,
            active_inventories=request.activeInventories,
            inventories=[item.model_dump() for item in request.inventories],
            target_ingredients=request.targetIngredients,
            additional_ingredients=request.additionalIngredients,
            exclude_ingredients=request.excludeIngredients,
            recent_feedback=[item.model_dump() for item in request.recentFeedback],
            shopping_mode=request.shoppingMode,
            allow_target_fallback=True,
        )
    except TeamRecipeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OcrContextValidationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DatabaseNotConfiguredError, DatabaseSchemaMissingError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="AI 레시피 생성에 실패했습니다.") from exc

    return AIRecipeResponse.model_validate(result)


@app.post(
    "/ocr/inventory-candidates/save",
    dependencies=[Depends(require_service_token), Depends(require_db_writer)],
    response_model=InventoryCandidatesSaveResponse,
    status_code=status.HTTP_201_CREATED,
)
async def recognize_and_save_inventory_candidates(
    file: UploadFile = File(...),
    fridge_id: int = Form(..., gt=0),
    created_by: int = Form(..., gt=0),
    min_confidence: float = Query(default=0.35, ge=0.0, le=1.0),
    match_threshold: float = Query(default=0.72, ge=0.0, le=1.0),
) -> InventoryCandidatesSaveResponse:
    items = await _recognize_upload(file)
    analysis = await asyncio.to_thread(
        analyze_ocr_items,
        items,
        min_confidence,
        match_threshold,
    )
    candidates = build_inventory_candidates(analysis)
    result = InventoryCandidatesResponse(
        filename=file.filename or "upload",
        count=len(candidates),
        inventory_candidates=candidates,
    )

    try:
        saved = await asyncio.to_thread(
            save_inventory_candidates,
            result,
            fridge_id=fridge_id,
            created_by=created_by,
        )
    except OcrContextValidationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DatabaseNotConfiguredError, DatabaseSchemaMissingError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="OCR 결과를 DB에 저장하지 못했습니다.") from exc

    return InventoryCandidatesSaveResponse.model_validate(saved)


@app.post(
    "/ocr/scans/{ocr_scan_id}/confirm",
    dependencies=[Depends(require_service_token), Depends(require_db_writer)],
    response_model=InventoryReviewResponse,
)
async def confirm_ocr_inventory(
    ocr_scan_id: int,
    request: InventoryReviewRequest,
) -> InventoryReviewResponse:
    try:
        confirmed = await asyncio.to_thread(
            confirm_inventory_candidates,
            ocr_scan_id,
            created_by=request.created_by,
            review_items=[item.model_dump(mode="python") for item in request.items],
        )
    except OcrScanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OcrScanAlreadyReviewedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InventoryReviewValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OcrContextValidationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DatabaseNotConfiguredError, DatabaseSchemaMissingError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="재고 확정에 실패했습니다.") from exc

    return InventoryReviewResponse.model_validate(confirmed)


@app.post("/recommendations", response_model=RecommendationResponse, dependencies=[Depends(require_service_token)])
async def get_recipe_recommendations(
    request: RecommendationRequest,
) -> RecommendationResponse:
    try:
        result = await asyncio.to_thread(
            recommend_recipes,
            fridge_id=request.fridge_id,
            user_id=request.user_id,
            limit=request.limit,
            max_missing_required=request.max_missing_required,
        )
    except OcrContextValidationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DatabaseNotConfiguredError, DatabaseSchemaMissingError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="레시피 추천 계산에 실패했습니다.") from exc

    return RecommendationResponse.model_validate(result)
