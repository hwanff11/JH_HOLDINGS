# JH_HOLDINGS 현재 작업 상태

> 이 문서는 **현재 운영 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 투자전략 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 자동운용 계약은 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md), Telegram 운영은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md), 실거래 전환·사고 대응은 [`docs/infra/LIVE_COMMISSIONING.md`](docs/infra/LIVE_COMMISSIONING.md)를 따릅니다.

## 1. 마지막 확인된 운영 상태

- 투자전략: **JDSS 3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 자동매매 실행계층: **JH AUTO 1.0.0**
- Oracle 실거래 runtime 배포본: **`086f9f63dc3e0715a2c5080bc7190b27ab133ac0`**
- Oracle 서비스: **active**
- 운용 모드: **실계좌 연결(`trading_mode=live`)**
- 실거래 준비 완료 표시(`live_commissioned`): **ON**
- 신규 BUY 잠금: **ON (`operator_buy_halt=1`)**
- JH AUTO 최초 시작승인: **수행하지 않음**
- 자동운용 실제 BUY: **배포 작업으로 시작하지 않음**
- `portfolio.live_enabled`: **false** — 일반 경로 오작동 방지 잠금 유지
- SGOV 자동운용: **OFF**
- 관리종목: **QQQ / TQQQ / SOXL**
- 주문: **정수주만 허용**
- 위험축소 SELL: **자동**, 불확실 상태에서는 안전정지와 계좌·원장 대조 우선
- UNKNOWN 주문: **자동 재전송 금지**

2026-09-06 외부 점검에서 서비스·DB·신규매수 잠금 유지와 위 배포본을 확인했습니다. 현재 작업 브랜치의 수정은 운영 서버에 아직 배포하지 않았습니다.

## 2. 현재 수정 작업

첫 운용 안전강화 PR #334는 병합됐습니다. 운영 배포의 DB 파일 동일성 검사에서 트랜잭션 개선을 구분할 수 없어, 검증된 정확한 이전·신규 파일 해시 쌍만 허용하는 호환성 검사를 추가합니다. 임의 스키마·초기화 변경은 계속 차단합니다.

- 활성 branch: `fix/live-transaction-deploy`
- 목적: 첫 매수 전 운영 오류 수정과 Telegram 사용성 개선
- 주문 결과 알림 실패가 주문감시를 중단하지 않도록 분리
- 스케줄러 최근 동작시각을 기록하고 외부 점검에서 확인
- 검토 당시 설정과 달라진 자금·최초 시작 확인 버튼 거부
- 최초 시작·자금변경·단계확대를 원자적으로 저장하고 실패 시 전체 취소
- 대표 긴급정지·임시격리 중 자금확대 중지
- 시작 전 성과 표시, 지금 할 일, 자금확대 조건, 한글 주문상태·체결내역 개선
- 로컬 전체 테스트 및 커버리지 기준 통과, 추가 장애·동시성·외부 점검·문서 검증 통과
- GitHub CI 결과는 이 PR과 Actions에서 확인하며 운영 배포와 구분

## 3. 바로 다음 작업

1. 수정 PR의 품질·보안·기존 전략 백테스트 검증 확인
2. 검토·병합 후 운영 배포가 승인된 경우 LIVE-ARMED 경로로 반영
3. 배포 후 서비스·주문감시 최근 동작시각·계좌 대조·매수 잠금 유지 확인
4. 대표가 Telegram에서 운용자금과 비율을 확인하고 최초 시작을 직접 승인

첫 시작은 `/dashboard` → `/auto` → 자금·비율 설정 → `/account` → `/auto start` 순서입니다. 최초 시작 버튼은 주문을 제출하지 않으며 다음 독립 안전주기에서 모든 주문 조건을 재검증합니다. `/halt`는 시스템이 자동으로 해제하지 않습니다.

## 4. 기준 문서

전략 수학은 `strategy.yaml`과 [투자전략 계약](docs/JDSS_FINAL_SPEC.md), 자동운용은 [JH AUTO 계약](docs/JH_AUTO_SPEC.md), 운영 화면은 [Telegram 가이드](docs/TELEGRAM_BOT_GUIDE.md)를 따릅니다. 완료된 과거 작업은 [이력](docs/HISTORY.md)과 병합 PR에서 확인합니다.
