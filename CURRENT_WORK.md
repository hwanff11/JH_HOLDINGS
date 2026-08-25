# JH_HOLDINGS Current Work

> 현재 전략·개발·배포·검증 상태의 단일 상태판입니다. 이전 값을 교체하는 롤링 문서이며, 상세 전략과 승인된 기준 백테스트는 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 공식 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)를 따릅니다.

## 현재 릴리즈와 운영

- GitHub 저장소: **`hwanff11/JH_HOLDINGS`** (public)
- 공식 릴리즈: **`v3.2.2`**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- Oracle runtime: **최신 배포 기능 revision `14b0ddd4022b12184fff96d39af1e447043b75de` / 서비스 active**
- 최근 forced dry-run 배포: **성공 / smoke test 성공**
- live: **LOCKED OFF**
- Oracle 환경: **`JDSS_TRADING_MODE=dry_run` / `JDSS_LIVE_CONFIRMATION` empty**
- 설정 잠금: **`portfolio.live_enabled=false`**

## 최근 완료 작업 — Telegram 운영 화면·주문 바로가기

- PR #194에서 `배분`, `오버레이`, `allocation`, `위험증가 BUY` 같은 내부 표현을 `목표비중`, `현재 보유비중`, `보유/목표`, `추가매수 판단`, `매수 주문 승인 대기` 중심으로 정리했습니다.
- 종목 상태·추가매수 판단·매수 승인·미체결 주문을 대시보드 버튼으로 확인할 수 있게 했습니다.
- Quality Gate, Security Gate, JDSS V3 canonical Backtest 통과 후 Oracle forced dry-run에 배포했고 smoke와 live 잠금 유지도 확인했습니다.

## 현재 개발 상태 — 오늘 주문 일괄 검토/승인 UX

- 활성 개발 브랜치: **`feature/telegram-batch-orders`**
- 목적: 실제 운용자가 종목별 매수 버튼을 반복해서 누르지 않고, **`오늘 주문 한번에 검토` → 전체 BUY 최종 1회 승인**으로 처리할 수 있게 단순화합니다.
- 전략 산식·목표비중 계산·위험축소 로직은 변경하지 않습니다. 오전 7시 이후 이미 확정된 V3.2.2 목표를 그대로 사용합니다.
- 기존 엔진의 **SELL 우선** 계약을 유지합니다. 위험축소 SELL이 열려 있으면 일괄 BUY 검토를 만들지 않고 `매도 완료 대기`만 표시합니다.
- SELL 전량 완료 후 브로커/원장 reconciliation과 SAFE_MODE 검사를 통과해야 BUY 후보를 만들 수 있습니다.
- BUY만 필요한 경우 QQQ/TQQQ/SOXL의 수량·지정가·예상금액·총 예상매수·예상 잔여현금을 한 화면에 모읍니다.
- 최종 `오늘 모의매수 N건 전체 실행` 버튼은 기존 종목별 execution approval을 사용하며, 각 주문 제출 직전에 가격·수량·현금·허용 세션을 다시 검증합니다.
- 실행 도중 한 종목의 조건이 변경되거나 주문 상태가 불명확하면 **그 이후 주문은 자동 중단**합니다. 이미 제출된 앞선 주문을 임의로 되돌렸다고 가정하지 않습니다.
- 기존 `/signal` 종목별 2단계 승인은 비상용·상세 확인 경로로 그대로 남깁니다.
- `/onboarding` 50% → 75% → 100% 단계는 한도만 열고, 단계별 실제 BUY도 `오늘 주문 한번에 검토`에서 묶어 승인할 수 있게 합니다.
- 신규 `/today` 명령과 대시보드 최상단 `오늘 주문 한번에 검토` 버튼을 추가했습니다.
- live hard lock과 forced dry-run은 변경하지 않습니다.
- 상태: **구현·단위테스트·운영가이드 작성 완료 / 전체 CI 검증 예정**

## 현재 안전장치

- `strategy.yaml`의 `portfolio.live_enabled=false`
- 런타임 live hard lock과 빈 live confirmation
- 위험축소 SELL 자동 실행, 미완료·UNKNOWN이면 신규 BUY 차단
- SELL 종료 후 reconciliation 전 BUY 차단
- 일괄 BUY 최종 승인도 기존 짧은 execution TTL과 1회용 callback/approval 사용
- 최종 실행 시 종목별 가격·수량·현금·세션 재검증
- 기존 BUY 미체결 주문이 있으면 새 일괄 BUY 생성 차단
- 중간 실패 시 남은 BUY 승인 취소, 이미 제출된 주문은 별도 주문 상태로 추적
- 주문 client ID 멱등성, 브로커 응답 검증, 부분체결 delta 반영
- 시작·주기 reconciliation 불일치 시 sticky SAFE_MODE
- 최초진입 50% → 75% → 100%, 단계별 전량 체결 후 최소 3 미국 거래일
- 배포 workflow는 최신 `main`만 받아 pinned SSH·강제 dry-run·rollback-safe smoke를 검증

## live 전환 전에만 남아 있는 항목

- 실제 Toss 관리 티커 기존 보유·열린 주문·주문가능금액의 live 전환 계획 확정
- 실제 주문 어댑터·회계·migration 리허설과 별도 명시적 live 승인
- 충분한 forced dry-run soak와 운영자 최종 확인

## 바로 다음 작업

1. 일괄 주문 단위테스트와 전체 Quality Gate·Security Gate·JDSS V3 canonical Backtest를 통과시킵니다.
2. 전략/백테스트 결과가 기존 main과 동일한지 확인합니다.
3. Draft PR 상태에서 주문 UX와 실패 시나리오를 검토합니다.
4. 명시적 병합·배포 승인 전까지 main/Oracle은 변경하지 않습니다.
5. 별도 live 승인 전까지 실제 Toss 주문 잠금을 해제하지 않습니다.
