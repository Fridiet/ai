# 신규 개발 영수증 확인: 27장으로 확대

2026-09-12. 예측을 보기 전에 원본 이미지 10장을 확인했다. IMG00114의 레몬녹차 340ml(구매 1개)와 제습제(비식품), IMG00126의 또르띠아(구매 2개)를 정답 초안으로 추가했다. 용량 없는 또띠아의 낱장 수나 중량은 기록하지 않았다. 나머지 8장은 생활용품/외식 주문으로 별도 triage에 기록했으며 식품 정답률 분모에 섞지 않았다.

선택 중 IMG00085를 잘못 열었으나 즉시 manifest를 확인했다. development_overlap이므로 새 평가 라벨에서 제외하고 inspection_exclusions에 기록했다. frozen_evaluation 이미지는 열지 않았다.

추가 2장 기본 OCR 평가 결과: 구매 개수는 각각 1/2로 맞았지만 두 상품 모두 이름 분류가 보류됐다. 960 보조 검출도 이름을 해결하지 못했다. 이름 정규화/식품 분류 단계의 미해결 문제를 보여주며 기존 25장의 개선이 새 사진에 충분히 일반화됐다는 증거는 아니다.

원시 결과: output/new_receipts/detector_experiment/IMG00114-default.json, IMG00114-max960.json, IMG00126-default.json, IMG00126-max960.json.

현재 개발용 152장 중 정답 초안 27장, 시각 triage 19장, 아직 둘 다 없는 이미지 106장. 기존 중복 그룹 제외와 라벨 파일 중복 검사를 통과했다. 초안은 human_verified=false, training_eligible=false로 유지했다. 이번에는 운영 코드·서버·모델을 변경하지 않았다.
