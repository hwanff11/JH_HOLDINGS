# JH_HOLDINGS Current Work

> 현재 전략·개발·배포·검증 상태의 단일 상태판입니다. 이전 값을 교체하는 롤링 문서이며, 상세 전략과 승인된 기준 백테스트는 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 공식 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)를 따릅니다.

## 현재 릴리즈와 운영

- GitHub 저장소: **`hwanff11/JH_HOLDINGS`** (public)
- 공식 릴리즈: **`v3.2.2`**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- PR #197 `feat: add sell-first one-tap daily order workflow`: **병합 완료**
- PR #197 병합 기능 revision: **`ac773c7a8c95783dc9e44bf6a115d86c924766d3`**
- Oracle runtime 기능 revision: **`ac773c7a8c95783dc9e44bf6a115d86c924766d3` / 서비스 active**
- 최근 forced dry-run 배포: **성공 / smoke test 성공**
- 배포 Actions: **Deploy Oracle Dry Run #56 / run `32807839387` 성공**
- 배포 검증: **release별 venv·DB snapshot·자동 rollback·SSH host key 고정·live 잠금·Toss read-only smoke 통과**
- live: **LOCKED OFF**
- Oracle 환경: **`JDSS_TRADING_MODE=dry_run` / `JDSS_LIVE_CONFIRMATION` empty**
- 설정 잠금: **`portfolio.live_enabled=false`**

## 최근 완료 작업 — 오늘 주문 일괄 검토/순차 승인 UX

실제 운용자가 종목별 BUY 버튼을 반복해서 누르지 않고 **`오늘 주문 한번에 검토` → `N건 순차 실행` 1회 최종 승인**으로 처리할 수 있게 운영 UX를 단순화했습니다.

전략 산식·목표비중 계산·HWM75·위험축소 SELL 로직은 변경하지 않았습니다. 변경 범위는 Telegram 운영층, 최초진입 상태 표시, 운영가이드와 회귀/운영리스크 테스트입니다.

### 사용 흐름

1. `/dashboard` → `오늘 주문 한번에 검토`
2. 최신 전략 계산·주문시점·미체결 주문·정합성·SAFE_MODE·HWM75 매수가능한도 사전검사
3. QQQ/TQQQ/SOXL 중 필요한 BUY를 한 화면에서 확인
4. `오늘 모의매수 N건 순차 실행` 1회 최종 승인
5. 각 종목은 기존 TradingService/OrderManager를 통해 순서대로 독립 제출
6. 중간 실패 시 이후 BUY 중단, 이미 제출된 주문은 임의 rollback하지 않고 실제 주문상태로 추적

### 운영리스크 보완 완료

- **계산 freshness**: 최신 완결 거래일과 `last_v322_allocation_trade_date`가 다르면 `전략 계산 확인 필요`로 BUY 차단합니다.
- **새 목표 주문시점**: 목표변경 뒤 다음 미국 거래일 전에는 `다음 미국 거래일 대기`, 거래일 시작 후 고정수량 생성 전에는 `목표수량 생성 대기`로 구분합니다.
- **진짜 주문 없음 판정**: active BUY signal이 없더라도 `target_qty`와 현재수량을 다시 비교해 SELL 준비·BUY 신호생성 대기와 실제 주문 없음을 구분합니다.
- **주문 가능시간**: 허용 세션 밖과 Toss 08:50~08:59 주문 점검시간에는 새 batch approval을 만들지 않습니다.
- **SELL 우선**: 위험축소 SELL이 열려 있으면 BUY batch를 만들지 않습니다. SELL 종료 뒤에도 reconciliation과 SAFE_MODE 검사를 통과해야 합니다.
- **즉시 reconciliation**: 일괄 검토 전과 최종 실행 직전에 브로커/원장을 즉시 다시 대조합니다.
- **합계 HWM75 사전검사**: 개별 종목뿐 아니라 전체 예상 BUY 합계를 HWM75 위험예산·JDSS 현금·브로커 주문가능금액을 반영한 `available_managed_cash`와 비교합니다.
- **최종 한도 재검사**: 검토 뒤 현금·위험예산·주문가능금액이 변하면 첫 주문을 내기 전에 전체 batch를 취소합니다.
- **동시클릭 직렬화**: batch 생성부터 실행까지 `RLock`으로 직렬화하고 유효한 batch가 이미 있으면 두 번째 검토가 새 approval을 만들지 않습니다.
- **검토 중 예외 정리**: 일부 execution approval 생성 뒤 시세/승인 오류가 나면 이미 만든 batch approval을 취소하고 감사로그를 남깁니다.
- **순차 실행 명시**: `전체 실행` 대신 `N건 순차 실행`으로 표시해 원자적 basket 주문으로 오해하지 않게 했습니다.
- **중간 실패 fail-closed**: 가격·수량 변경, `UNKNOWN`, `REJECTED`, `CANCELED`, `REPLACED` 등이 발생하면 이후 주문을 중단하고 남은 approval을 취소합니다.
- **중복 자동알림 제거**: 포트폴리오 BUY 후보는 종목별 승인 카드를 반복 전송하지 않고 `오늘 매수 검토 가능` 한 장으로 알립니다. 중복 `매수 승인 대기` 이벤트도 숨깁니다.
- **최초진입 상태 구분**: 50%/75% 단계 목표를 이미 채운 상태를 100% 목표와 비교해 `매수신호 생성 대기`로 오인하지 않고 `현재 단계 완료/다음 단계 대기`로 표시합니다.
- **재시작/만료 안전**: batch는 짧은 TTL·1회용이며 서버 재시작 뒤 과거 batch 버튼은 실행되지 않고 최신 검토를 요구합니다.
- **감사기록**: batch 생성·한도차단·만료·취소·실행시작·성공/부분실패를 SQLite 이벤트로 남겨 Telegram 결과 메시지가 유실돼도 `/errors`에서 확인할 수 있습니다.

### 검증 결과

PR #197 최종 기능 head `e833468bbdd9482b51f6a28d20a34362af984f84` 기준으로 다음 필수 검증을 모두 통과했습니다.

- CI - Quality Gate **#862** ✅
- Security **#644** ✅
- JDSS V3 canonical Backtest **#325** ✅
- Ruff / 전체 Pytest / Config validation ✅
- Security Gate / Bandit / CodeQL / Secret scan ✅

신규 테스트에는 stale 전략계산, 다음 거래일 대기, 미생성 BUY 신호, SELL 미완료, 합계 HWM75 초과, 동시클릭, 검토 중 예외 cleanup, 최종 즉시 reconciliation 불일치, 중간 QuoteChanged, 중복 callback, 자동알림 통합, 주문시간 차단, 최초진입 단계대기를 포함합니다.

병합 후 Oracle 배포에서는 최신 main `ac773c7a8c95783dc9e44bf6a115d86c924766d3`를 다시 고정 확인한 뒤 focused deployment gate와 smoke를 모두 통과했습니다.

## 현재 안전장치

- `strategy.yaml`의 `portfolio.live_enabled=false`
- 런타임 live hard lock과 빈 live confirmation
- 위험축소 SELL 자동 실행, 미완료·UNKNOWN이면 신규 BUY 차단
- SELL 종료 후 reconciliation 전 BUY 차단
- BUY execution approval의 짧은 TTL과 1회용 token/callback
- 주문 client ID 멱등성, 브로커 응답 검증, 부분체결 delta 반영
- HWM75 현금/위험예산과 open BUY reservation의 원자적 주문 예약
- 시작·주기·최종 BUY 직전 reconciliation 불일치 시 신규 BUY 차단
- 최초진입 50% → 75% → 100%, 단계별 전량 체결 후 최소 3 미국 거래일
- 배포 workflow는 최신 `main`만 받아 pinned SSH·강제 dry-run·rollback-safe smoke를 검증

## 운영상 유지해야 할 원칙

- `순차 실행`은 하나의 원자적 basket 주문이 아니므로 일부 종목만 제출된 상태가 생길 수 있습니다. 이 경우 이미 제출된 주문을 임의로 되돌리지 않고 `/order`와 `/errors`에서 실제 상태를 확인합니다.
- 일괄 검토가 열려 있는 동안 같은 신호를 `/signal` 개별 승인으로 동시에 처리하지 않는 것을 기본 운영 원칙으로 합니다. 소프트웨어는 중복 주문·초과 목표를 다시 검사하지만 서로 다른 승인화면을 동시에 운용하면 불필요한 취소/재검토가 생길 수 있습니다.
- JDSS가 관리하는 QQQ/TQQQ/SOXL을 Toss 앱에서 동시에 수동 거래하지 않습니다. 최종 클릭 직전 reconciliation은 수행하지만 브로커 외부 거래와 프로그램 주문을 완전히 원자적으로 잠그는 것은 불가능합니다.

## live 전환 전에 남은 항목

- 실제 Toss 관리 티커의 기존 보유·열린 주문·주문가능금액에 대한 live 전환 계획 확정
- 실제 주문 어댑터·회계·migration 리허설과 별도 명시적 live 승인
- 충분한 forced dry-run soak와 실제 Telegram 운영자 사용성 확인
- live 전환 직전 실제 계좌를 대상으로 preflight/reconciliation 재검증

## 바로 다음 작업

1. Oracle forced dry-run에서 실제 Telegram `주문 없음/대기/SELL 진행/BUY 순차실행/부분실패` 화면을 운용 관점에서 확인합니다.
2. 신규 일괄 승인 흐름을 일정 기간 forced dry-run으로 soak하며 `/errors`, reconciliation, 주문상태를 관찰합니다.
3. 문제가 없더라도 별도 live 승인 전까지 실제 Toss 주문 잠금을 해제하지 않습니다.
4. 전략 연구 PR과 production 운영 변경은 계속 분리합니다.
