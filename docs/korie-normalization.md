# KORIE 정규화 준비 — 2026-09-11

## 확인 결과

출처: https://github.com/MahmoudSalah/KORIE
학습용 상품 주석 링크: https://drive.google.com/file/d/1W6XYnRtsQ2E8UZlo-MGwxQ0SkByTsdQV/view

공개 CSV를 메모리에서 확인한 결과 1,731행 / image_name 고유값 580개였다.
class 열은 1,731행 모두 공란이다. Item_Name에는 코드·가격이 같이 있는 행이 존재한다.
이는 마트 영수증 580장 확보나 검수된 표준 식재료 정답 1,731개를 의미하지 않는다.
README의 전체 데이터 개수와 특정 CSV의 고유 ID 개수도 구분해야 한다.
공개 Samples 폴더는 확인 당시 이미지 2개이며 주석 파일은 없었다.

GitHub API license는 null이고 README에 명시적인 데이터 라이선스를 찾지 못했다.
연구 목적의 데이터 공개 설명은 있으나 학습 모델 배포/재배포 범위를 확정하지 못했다.
대량 이미지 다운로드, KORIE 기반 모델 학습, 사전 자동 반영은 수행하지 않았다.
원본 CSV를 저장소에 복사하지 않았다.

## 이번 코드 변경

- 정규화 전에 대괄호 안 6~14자리 상품코드를 제거하여 가격으로 잘리는 문제 방지.
- 별표가 붙은 행 번호 제거. 원시 OCR 텍스트는 유지한다.
- 상품명이 의미상 어떤 식재료인지 추측하여 새 라벨을 만들지는 않는다.
- 자체 작성한 합성 예제 및 기존 테스트 105개 통과.
- 기존 개발 영수증: 20/30행 추출, Precision 1.0 / Recall 0.6667 유지.
  KORIE 독립 정확도 평가 또는 머신러닝 개선 성과가 아니다.

## 이용 조건 확인 후 준비 명령

AI 폴더에서 이용 권한이 확인된 로컬 CSV를 지정한다:

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_korie_review --csv <items_train.csv경로> --split train
```

출력은 output/korie_review.json이다. 기존 파일은 덮어쓰지 않는다.
원본 해시, 영수증 ID, 원본 split, 행 번호, 원문, 정규화 후보를 남긴다.
store_type/is_food/canonical_name은 null, reviewed는 false로 시작한다.
모델의 예측을 자동 정답으로 사용하지 않도록 상품 사전 및 DB에는 쓰지 않는다.

검수자는 실제 이미지와 대조하여 마트 여부·식품 여부·표준 재료명을 입력해야 한다.
수량 정답은 별도로 이미지 대조가 필요하다. 같은 영수증의 행은 다른 split에 나누지 않는다.
검증/테스트 행에서 발견한 별칭을 학습 사전에 넣고 같은 행으로 평가하지 않는다.

## 제공자에게 확인할 내용 (발송하지 않은 초안)

To: mahmoud.salah@aun.edu.eg
Subject: KORIE dataset permission for a university receipt-normalization project

We are developing a university capstone application that normalizes Korean grocery
receipt items into ingredient names. Could you confirm the dataset license and
whether local research, model training, publication of evaluation results, and
distribution of trained weights are permitted? We will not redistribute receipt
images or annotations without permission. Is there a supermarket-only subset or
store-type metadata, and are corrected item-level labels available?

## 다음 단계

이용 조건 확인 → 이미지/주석 대응 확인 → 실제 마트 영수증 50장 선별 → 사람이 정답 검수
→ 영수증 단위 분리 → 현재 정규화와 학습 기반 보정기 비교 → 검증 개선 시 서버 반영.
