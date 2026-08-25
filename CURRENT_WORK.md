# JH_HOLDINGS Current Work

> 현재 전략·개발·배포·검증 상태의 단일 상태판입니다. 이전 값을 교체하는 롤링 문서이며, 상세 전략과 승인된 기준 백테스트는 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 공식 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)를 따릅니다.

## 현재 릴리즈와 운영

- GitHub 저장소: **`hwanff11/JH_HOLDINGS`** (public)
- 공식 릴리즈: **`v3.2.2`**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- Oracle runtime: **최신 기능 main 배포 완료 / 서비스 active**
- 기능 source/runtime revision: **`14b0ddd4022b12184fff96d39af1e447043b75de` 일치 확인 완료**
- 최근 forced dry-run 배포: **성공 / smoke test 성공**
- live: **LOCKED OFF**
- Oracle 환경: **`JDSS_TRADING_MODE=dry_run` / `JDSS_LIVE_CONFIRMATION` empty**
- 설정 잠금: **`portfolio.live_enabled=false`**

## 최근 완료 작업 — Telegram 운영 화면·주문 바로가기

- PR #194에서 Telegram 운영 화면의 내부 개발용 표현을 실제 운용자가 바로 이해할 수 있는 표현으로 정리했습니다.
- `배분`, `오버레이`, `allocation`, `위험증가 BUY` 중심 표현을 `목표비중`, `현재 보유비중`, `보유/목표`, `추가매수 판단`, `매수 주문 승인 대기` 중심으로 바꿨습니다.
- 대시보드에 `매수 승인 대기 보기`, `미체결 주문 보기` 바로가기 버튼을 추가해 `/signal`, `/order` 명령을 직접 입력하지 않아도 주문 흐름을 확인할 수 있게 했습니다.
- 기존 매수 흐름은 `매수 주문 검토하기` → 최신 가격·수량·현금·세션 재검증 → `종목 N주 모의/실매수 실행`의 2단계 승인과 주문번호·상태·체결수량 회신을 그대로 유지합니다.
- Toss OpenAPI 실제 주문 어댑터는 유지하되 V3.2.2 live hard lock은 변경하지 않았습니다.
- Telegram 포맷·버튼 테스트, 전체 Quality Gate, Security Gate, JDSS V3 canonical Backtest를 통과했습니다.
- PR #194 병합 후 최신 기능 main을 Oracle forced dry-run으로 배포했고 release별 venv, DB snapshot, 자동 rollback, pinned SSH host key, live 잠금, Toss read-only smoke를 모두 통과했습니다.

## 현재 안전장치

- `strategy.yaml`의 `portfolio.live_enabled=false`
- 런타임 live hard lock과 빈 live confirmation
- 매수는 최신 가격·수량 검토 후 60초 최종 승인
- 위험축소 SELL은 자동이지만 미완료·UNKNOWN이면 신규 BUY 차단
- 주문 client ID 멱등성, 브로커 응답 종목·방향·수량 검증, 부분체결 delta 반영
- 시작·주기 reconciliation 불일치 시 sticky SAFE_MODE
- 최초진입 50% → 75% → 100%, 단계별 전량 체결 후 최소 3 미국 거래일, 단계 개방은 운영자 확인 필요
- 배포 workflow는 최신 `main`만 받아 pinned SSH·강제 dry-run·rollback-safe smoke를 검증

## 현재 개발 상태

Telegram 운영 화면·주문 바로가기 작업은 **완료** 상태입니다. 기능 main과 Oracle forced dry-run runtime이 일치하며, 실거래 잠금은 유지됩니다.

## live 전환 전에만 남아 있는 항목

- 실제 Toss 관리 티커 기존 보유·열린 주문·주문가능금액의 live 전환 계획 확정
- 실제 주문 어댑터·회계·migration 리허설과 별도 명시적 live 승인
- 충분한 forced dry-run soak와 운영자 최종 확인

## 바로 다음 작업

1. 다음 미국 시장일 오전 7시 일일 운용보고에서 새 Telegram 용어와 대시보드 버튼 표시를 확인합니다.
2. `매수 승인 대기 보기`, `미체결 주문 보기` 버튼이 실제 운영 메시지에서도 정상 동작하는지 forced dry-run 상태에서 확인합니다.
3. 주문 감시·정합성 점검·안전 경고의 기존 주기가 유지되는지 운영 로그에서 확인합니다.
4. 별도 live 승인 전까지 실제 Toss 주문 잠금을 해제하지 않습니다.
