# 개인화 장보기 추천

## 흐름

프론트 장바구니의 `현재 조건으로 추천 받기` → Spring `POST /api/recipes/ai/shopping`
→ AI `POST /api/recipes/ai` (`shoppingMode: true`) → 부족 재료 표시 → 사용자 수량 확인 → 기존 장바구니 추가 API.

별도의 장보기 학습 모델은 아니다. 기존 Two-Tower 레시피 추천과 저장·조리 이력의 취향 벡터를 재사용한다.
최근 30일의 로그인 사용자 기록, DB 건강 목표, 접근 가능한 냉장고 재고를 사용한다.
이력이 없는 사용자는 개인화 가중치 0으로 기본 추천한다.

## 요청과 응답

인증: 기존 Bearer JWT. 다른 사용자의 ID/건강 프로필을 요청에 넣어도 추천 기준은 로그인 사용자다.

```json
{
  "fridgeId": 7,
  "targetIngredients": [],
  "additionalIngredients": [],
  "excludeIngredients": []
}
```

응답은 기존 `RecipeResponse`와 동일하다. 주요 필드는 `recipeKey`, `recipeName`,
`ingredients`, `availableIngredients`, `missingIngredients`, `recommendationScore`, `scoreBreakdown`.
`recipeId`는 미저장 미리보기이므로 null일 수 있다.

- 필수 부족 재료 1~3개, 활용 가능한 재료가 있는 후보만 허용한다.
- 기존과 동일하게 부족 재료 개수가 가장 적은 후보군 안에서 개인화 점수로 선택한다.
- 404: 조건에 맞는 추가 구매 추천 없음. 403: 냉장고 접근 불가.
- AI 연결 실패 503, 기타 AI 응답 실패 502. 연결 10초/응답 60초 타임아웃.
- 추천 조회만으로 피드백·레시피·장바구니를 저장하지 않는다.
- 실제 장바구니 추가는 기존 API로 `sourceType: RECIPE`, 설명은 `memo`에 전달한다.
  미저장 카탈로그 ID를 DB 레시피 FK에 넣지 않는다.

## 범위와 한계

부족 판정은 보유 여부와 환산 가능한 수량을 함께 사용한다. `ingredientBalances`를 추가했다.
필수 재료의 g/kg/mg, ml/L, 개/ea를 각각 g, ml, 개로 환산하고, 중복 레시피 행 및 여러 재고 배치를 합산한다.
필드: name, requiredQuantity, knownAvailableQuantity, shortageQuantity, unit, status(ENOUGH/SHORTAGE/UNKNOWN).
모르는 단위, 직접 입력, g↔개, g↔ml는 임의 환산하지 않는다. 부족 수량은 이 경우 null이다.
수량을 알 수 없는 재고가 섞여 있어도 확인된 양만으로 충분하면 ENOUGH, 아니면 UNKNOWN이다.
`missingIngredients`는 기존 이름 부재 의미를 유지한다. 수량만 부족한 품목은 ingredientBalances에서 확인한다.
장바구니 UI에는 이름 부재 또는 확실한 수량 부족 품목을 표시한다. 추천을 조회해도 실제 재고를 차감하지 않는다.
화면의 수량은 레시피 필요량이며 포장 구매량이나 가격 최적화 결과가 아니다.
장바구니 추가 자체를 선호의 정답으로 학습하지 않는다. 저장·조리 이력만 기존 방식으로 반영한다.
추천 점수는 사용자 간 만족도/확률 비교 수치가 아니다. 개인화가 없는 경우와 점수 산식도 다르다.

## 로컬 검증

`python -m scripts.verify_shopping_recommendations`

로컬 합성 계정 4개와 동일 냉장고 7을 사용한다. 콜드스타트/이력 반영, 1~3개 부족,
피드백 불변, 타 냉장고 접근 거부를 검사한다. 새 주문이나 구매를 만들지 않는다.
2026-09-11 실행에서 두부 선호 계정은 버섯곤드레밥(곤드레나물 부족),
나머지 계정은 묵계밥(콩나물 부족)이 나왔다. 같은 메뉴가 나와도 이력에 따라 점수는 달랐다.
이는 합성 이력에 대한 기능 검증이며 실제 사용자 선호 정확도를 입증하는 실험은 아니다.
