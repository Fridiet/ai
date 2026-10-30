# PERCENT·CUSTOM 조리 후 잔량과 AI 추천 연동

## 담당 범위

AI의 `/api/recipes/ai` 입력과 추천 수량 비교를 보완했다. 프론트, 백엔드, DB는 변경하지 않았다. 조리 완료 트랜잭션·중복 요청 방지·잔량 갱신은 백엔드 담당이며 AI에 차감 API를 추가하지 않았다.

## 백엔드에서 전달할 재고 스냅샷

기존 `inventories` 배열에 다음 필드를 사용한다. 같은 냉장고의 여러 배치는 합치지 않고 각각 전달한다.

| 필드 | 의미 |
|---|---|
| `ingredientName` | 표준 재료명 |
| `unitType` | `QUANTITY`, `PERCENT`, `CUSTOM` 중 하나. 생략 시 기존처럼 QUANTITY |
| `remainingQuantity` | QUANTITY는 저장 단위 수량, PERCENT는 0~100의 현재 잔량. CUSTOM에서는 숫자로 해석하지 않음 |
| `unit` | QUANTITY의 g·개·mL 등 저장 단위. PERCENT·CUSTOM은 null 또는 기존 표시 단위 허용, 물리 단위 계산에는 사용하지 않음 |
| `customQuantityText` | 선택 필드. CUSTOM의 현재 설명(예: 반 통). 최대 500자, 숫자로 파싱하지 않음 |
| `status` | 선택 필드. 기본 ACTIVE. ACTIVE가 아닌 상태는 추천 재고에서 제외. 소진 시 백엔드의 실제 소진 상태명을 전달 |
| `consumeByDate` | 기존 소비기한. 지난 재고는 추천에서 제외 |

예시:

```json
{
  "inventories": [
    {"ingredientName":"두부", "unitType":"PERCENT", "remainingQuantity":50, "unit":null, "status":"ACTIVE"},
    {"ingredientName":"대파", "unitType":"CUSTOM", "remainingQuantity":null, "unit":null, "customQuantityText":"반 통", "status":"ACTIVE"}
  ]
}
```

현재 백엔드 `AiRecipeClientDto.InventoryDto`에는 `customQuantityText`와 `status`가 없으므로 팀원이 선택적으로 추가해 전달해야 한다. 두 필드는 생략해도 기존 클라이언트는 호출 가능하다.

중요: CUSTOM에서 `remainingQuantity=0`은 구버전 숫자 자리값일 수 있어 소진으로 판단하지 않는다. 텍스트의 `없음`, `0g`, 빈 문자열도 AI가 자의적으로 해석하지 않는다. **CUSTOM 소진은 백엔드에서 status를 ACTIVE가 아닌 값으로 저장·전달하거나 해당 배치를 스냅샷에서 제외해야 한다.** 제외할 때 `activeInventories` 이름 목록도 최신화해야 구버전 이름 보완으로 재고가 살아나지 않는다. 가능하면 소진 배치도 상태를 포함한 스냅샷으로 전달한다.

## 조리 완료 후 처리

1. QUANTITY는 기존 검증된 사용량 차감을 유지한다.
2. PERCENT는 사용자가 확인한 최종 잔량을 저장한다. 예를 들어 75%에서 50%로 바뀌었다면 다음 AI 요청의 `remainingQuantity`는 50이다. 25g을 사용했다는 뜻이 아니며, g·mL와 직접 차감하지 않는다.
3. CUSTOM은 사용자가 입력한 최종 잔량 설명을 저장한다. 예를 들어 `반 통`에서 `조금 남음`으로 갱신한다.
4. 새 추천을 요청할 때 갱신된 재고 스냅샷을 전달한다. AI는 요청별로 계산하며 이전 요청의 재고 상태를 저장하거나 재사용하지 않는다.
5. 조리 완료 재시도 중복 방지, 동시 수정 충돌, 캘린더·행동 이력과 잔량의 트랜잭션 처리는 백엔드에서 구현한다.

## AI가 반환하는 기존 응답

- PERCENT가 0보다 크거나 ACTIVE인 CUSTOM은 보유 재료로 추천에 활용한다.
- PERCENT 0%, 소진 상태, 소비기한 경과 재고는 제외한다. 같은 요청의 오래된 `activeInventories` 이름으로 제외한 스냅샷을 복원하지 않는다.
- PERCENT·CUSTOM만으로는 `ingredientBalances.status=UNKNOWN`, `shortageQuantity=null`, `knownAvailableQuantity=0`을 반환한다. 여기서 0은 **확인된 g·mL 재고량이 0**이라는 뜻이지 해당 재료 자체가 없다는 뜻이 아니다.
- 보유 중인 불명확 수량에는 기존 `reasons`에 `잔량 확인 필요: 두부, 대파.`처럼 짧게 안내한다. 프론트는 UNKNOWN을 확정 부족이나 자동 차감량으로 취급하면 안 된다.
- QUANTITY가 300g이고 별도 PERCENT 배치가 있어 400g 필요량의 충족 여부를 모르면 UNKNOWN이다. QUANTITY만으로 500g을 확실히 보유했다면 400g 필요량에는 ENOUGH이다.
- PERCENT의 100%를 100g으로 보거나, CUSTOM 텍스트의 `800g`을 자동 재고량으로 사용하지 않는다. 배치별 개당 무게가 있어도 PERCENT·CUSTOM을 물리량으로 환산하지 않는다.
- 응답 필드와 ENOUGH·SHORTAGE·UNKNOWN 구분은 기존 계약 그대로 유지한다.

PERCENT 범위 초과, 음수·무한대·NaN 수량, 정의되지 않은 unitType은 입력 검증에서 거절한다. 이번 변경은 직접 DB를 조회하는 별도 레거시 `/recommendations` 경로의 DB 스키마 변경을 포함하지 않는다.

## 검증

`tests/test_inventory_unit_modes.py`에서 세 단위 유형, CUSTOM 숫자 0 자리값, 소진/기한 경과, 여러 배치 혼합, 잔량 갱신 후 새 API 요청, 0~100 범위 검증을 확인한다. API 테스트는 FastAPI TestClient로 수행하며 실제 DB를 수정하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
