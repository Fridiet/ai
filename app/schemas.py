from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OCRItem(BaseModel):
    text: str
    confidence: float
    box: list[list[int]]


class OCRResponse(BaseModel):
    filename: str
    text: str
    count: int
    items: list[OCRItem]


class IngredientMasterCandidate(BaseModel):
    ingredient_id: None = None
    ingredient_type: Literal["INGREDIENT", "SEASONING", "BEVERAGE", "ETC"]
    canonical_name: str
    default_unit: str
    alias_text: str | None = None
    match_confidence: float = Field(ge=0.0, le=1.0)
    match_type: Literal["exact", "alias", "contains", "fuzzy", "ml_classifier", "llm_validated"]
    source: str


class InventoryBatchCandidate(BaseModel):
    ingredient_id: None = None
    consume_by_date: date | None = None
    remaining_quantity: float = Field(gt=0)
    unit: str
    quantity_source: Literal["PACKAGE_TEXT", "DEFAULT", "VERIFIED_RECEIPT_COLUMNS",
                             "RECONSTRUCTED_PRICE_COLUMNS", "SECONDARY_VERIFIED_RECEIPT_COLUMNS", "VERIFIED_RECEIPT_TOTALS", "NUMERIC_CELLS_VERIFIED"]
    storage_type: Literal["REFRIGERATED", "FROZEN", "ROOM_TEMPERATURE", "UNKNOWN"]
    storage_source: Literal["RECEIPT_TEXT", "CANONICAL_RULE", "UNKNOWN"]
    storage_confidence: float = Field(ge=0.0, le=1.0)
    status: Literal["ACTIVE"] = "ACTIVE"


class InventoryCandidate(BaseModel):
    row_index: int
    raw_text: str
    recognized_product_name: str
    normalized_product_name: str
    manufacturer: str | None = None
    food_type: str
    classification: Literal["processed_food", "ingredient"]
    prices: list[int]
    ingredient_master_candidate: IngredientMasterCandidate
    inventory_batch_candidate: InventoryBatchCandidate
    warnings: list[str]


class InventoryCandidatesResponse(BaseModel):
    filename: str | None = None
    count: int
    requires_backend_mapping: bool = True
    inventory_candidates: list[InventoryCandidate]


class ReceiptInventoryItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    raw_text: str | None = Field(default=None, alias="rawText")
    review_notes: list[str] = Field(default_factory=list, alias="reviewNotes")
    name: str
    quantity: int | float = Field(gt=0)
    unit: str
    consume_by_date: date | None = Field(default=None, alias="consumeByDate")
    location_category: Literal["refrigerated", "frozen", "room_temperature", "unknown"] = Field(
        alias="locationCategory"
    )
    category: Literal[
        "vegetable",
        "fruit",
        "meat",
        "seafood",
        "dairy",
        "egg",
        "grain",
        "seasoning",
        "beverage",
        "processed_food",
        "etc",
    ]


class ReceiptReviewCandidate(BaseModel):
    rawText: str
    purchaseCount: int | None
    reason: str


class ReceiptOCRResponse(BaseModel):
    items: list[ReceiptInventoryItem]
    reviewCandidates: list[ReceiptReviewCandidate] = Field(default_factory=list)


class AIRecipeInventory(BaseModel):
    countBasis: str | None = None
    perCountQuantity: float | None = Field(default=None, gt=0)
    perCountUnit: str | None = None
    quantityNeedsReview: bool = False
    unitType: Literal["QUANTITY", "PERCENT", "CUSTOM"] = "QUANTITY"
    customQuantityText: str | None = Field(default=None, max_length=500)
    status: str | None = Field(default="ACTIVE", max_length=32)
    ingredientName: str = Field(min_length=1, max_length=100)
    consumeByDate: date | None = None
    remainingQuantity: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    unit: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def validate_percent_range(self):
        if self.unitType == "PERCENT" and self.remainingQuantity is not None and self.remainingQuantity > 100:
            raise ValueError("PERCENT remainingQuantity는 0~100이어야 합니다.")
        return self


class AIRecipeFeedback(BaseModel):
    recipeKey: str | None = Field(default=None, max_length=160)
    recipeName: str = Field(min_length=1, max_length=100)
    eventType: Literal["SHOWN", "SAVED", "COOKED", "DISMISSED"]
    occurredAt: datetime


class AIRecipeRequest(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def normalize_optional_lists(cls, data):
        # Java DTOs serialize absent optional lists as null rather than omitting them.
        if isinstance(data, dict):
            data = dict(data)
            for name in ("activeInventories", "inventories", "targetIngredients",
                         "additionalIngredients", "excludeIngredients", "recentFeedback"):
                if name in data and data[name] is None:
                    data[name] = []
        return data

    shoppingMode: bool = False
    healthCondition: str | None = Field(default=None, max_length=100)
    healthGoal: str | None = Field(default=None, max_length=100)
    activeInventories: list[str] = Field(default_factory=list)
    inventories: list[AIRecipeInventory] = Field(default_factory=list)
    targetIngredients: list[str] = Field(default_factory=list)
    additionalIngredients: list[str] = Field(default_factory=list)
    excludeIngredients: list[str] = Field(default_factory=list)
    recentFeedback: list[AIRecipeFeedback] = Field(default_factory=list)


class AIRecipeIngredient(BaseModel):
    nameText: str
    quantity: float = Field(gt=0)
    unit: str
    optional: bool


class AIRecipeStep(BaseModel):
    stepNumber: int = Field(gt=0)
    description: str


class IngredientBalance(BaseModel):
    name: str
    requiredQuantity: float | None
    knownAvailableQuantity: float
    shortageQuantity: float | None
    unit: str
    status: Literal["ENOUGH", "SHORTAGE", "UNKNOWN"]


class AIRecipeResponse(BaseModel):
    recipeKey: str | None = None
    title: str
    description: str
    cookingTime: int = Field(gt=0)
    category: str
    score: float = Field(default=0, ge=0, le=100)
    scoreBreakdown: dict[str, float | None] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    availableIngredients: list[str] = Field(default_factory=list)
    missingIngredients: list[str] = Field(default_factory=list)
    ingredientBalances: list[IngredientBalance] = Field(default_factory=list)
    ingredients: list[AIRecipeIngredient]
    steps: list[AIRecipeStep]


class StoredInventoryItem(BaseModel):
    ocr_scan_item_id: int
    row_index: int
    ingredient_id: int | None = None
    canonical_name: str
    review_status: Literal["PENDING_REVIEW"]


class InventoryCandidatesSaveResponse(BaseModel):
    ocr_scan_id: int
    status: Literal["PENDING_REVIEW"]
    item_count: int
    mapped_item_count: int
    requires_review_count: int
    items: list[StoredInventoryItem]
    inventory_candidates: list[InventoryCandidate]


class InventoryReviewItem(BaseModel):
    ocr_scan_item_id: int = Field(gt=0)
    decision: Literal["CONFIRM", "REJECT"]
    ingredient_id: int | None = Field(default=None, gt=0)
    remaining_quantity: float | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, min_length=1, max_length=10)
    consume_by_date: date | None = None
    storage_type: Literal["REFRIGERATED", "FROZEN", "ROOM_TEMPERATURE", "UNKNOWN"] | None = None


class InventoryReviewRequest(BaseModel):
    created_by: int = Field(gt=0)
    items: list[InventoryReviewItem] = Field(min_length=1)


class ConfirmedInventoryBatch(BaseModel):
    batch_id: int
    ocr_scan_item_id: int
    ingredient_id: int
    remaining_quantity: float
    unit: str
    consume_by_date: date | None = None
    storage_type: Literal["REFRIGERATED", "FROZEN", "ROOM_TEMPERATURE", "UNKNOWN"]


class InventoryReviewResponse(BaseModel):
    ocr_scan_id: int
    status: Literal["REVIEWED"]
    created_batch_count: int
    rejected_item_count: int
    batches: list[ConfirmedInventoryBatch]


class RecommendationRequest(BaseModel):
    fridge_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    limit: int = Field(default=5, ge=1, le=10)
    max_missing_required: int = Field(default=1, ge=0, le=5)


class ExpiringIngredientRecommendation(BaseModel):
    batch_id: int
    ingredient_id: int
    canonical_name: str
    consume_by_date: date
    days_remaining: int


class RecommendationScoreBreakdown(BaseModel):
    expiry_urgency: float
    inventory_coverage: float
    stock_utilization: float
    menu_diversity: float
    missing_required_penalty: float
    base_inventory_score: float
    nutrition_fit_score: float | None = None


class RecipeNutrition(BaseModel):
    serving_size_g: float | None = None
    calories_kcal: float
    carbohydrate_g: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    sodium_mg: float | None = None
    source: str


class AppliedDietProfile(BaseModel):
    goal: Literal["GENERAL", "WEIGHT_LOSS", "DIABETES_MANAGEMENT", "LOW_SODIUM"]
    conditions: list[str]
    calorie_min: float | None = None
    calorie_max: float | None = None
    carbohydrate_min_g: float | None = None
    carbohydrate_max_g: float | None = None
    protein_min_g: float | None = None
    sodium_max_mg: float | None = None


class RecipeRecommendation(BaseModel):
    recipe_id: int
    recipe_name: str
    cook_time: int | None = None
    category: str | None = None
    description: str | None = None
    steps: list[str]
    source: str
    nutrition: RecipeNutrition | None = None
    health_reasons: list[str] = Field(default_factory=list)
    total_score: float
    score_breakdown: RecommendationScoreBreakdown
    coverage_ratio: float
    available_ingredients: list[str]
    missing_ingredients: list[str]
    expiring_ingredients: list[ExpiringIngredientRecommendation]
    used_batch_ids: list[int]
    reason: str


class RecommendationResponse(BaseModel):
    fridge_id: int
    user_id: int
    generated_at: datetime
    active_inventory_count: int
    excluded_expired_inventory_count: int
    applied_profile: AppliedDietProfile | None = None
    warnings: list[str] = Field(default_factory=list)
    score_policy: dict[str, float]
    recommendations: list[RecipeRecommendation]
