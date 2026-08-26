# JH_HOLDINGS Current Work

> **현재 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 규범 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 운영 방법은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md), 실거래 최초 전환·사고 대응은 [`docs/infra/LIVE_COMMISSIONING.md`](docs/infra/LIVE_COMMISSIONING.md)를 따릅니다.

## 1. Production 상태

- 공식 릴리즈: **v3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- GitHub `main`: 보호 브랜치, PR + 필수 CI 경유
- 최신 runtime 기능 revision: **`b29196ea94e4bb94ae847f9c94cb47a8c2268799`**
- Oracle runtime 기능 revision: **`b29196ea94e4bb94ae847f9c94cb47a8c2268799`**
- Oracle 서비스: **active**
- 현재 운용 모드: **forced dry-run**
- `portfolio.live_enabled`: **false**
- 실제 Toss 주문: **LOCKED / 미활성화 / 0건**
- SGOV 자동운용: **OFF**
- 운영자 긴급 BUY 차단: **`/halt` 지원**
- BUY 재개: **reconciliation PASS + 미체결 BUY 0 + 운영자 명시적 `/resume`**
- 외부 Oracle health watch: **매시간 + owner-only on-demand**

runtime 영향이 없는 문서·CI·운영 workflow commit이 `main`에 추가될 수 있으므로 GitHub HEAD와 Oracle 기능 revision 문자열은 항상 같을 필요는 없습니다. 현재 Oracle에는 최신 runtime 기능 revision이 forced dry-run으로 배포되어 있습니다.

## 2. 최근 완료

### yfinance 현재가 일시적 빈 응답 내성 보강

2026-08-26 저녁 배분 점검에서 `yfinance 현재가 조회 실패: QQQ`가 발생한 원인을 추적해 PR #239에서 현재가 조회 경로를 보강했습니다.

원인:

- 배분 점검이 다음 세션 목표수량을 만들 때 `MarketDataDryRunBroker.get_price()`가 yfinance 1분봉 현재가를 조회
- 기존 `current_price()`는 `Ticker.history()`를 **단 1회** 호출하고 빈 응답이면 즉시 전체 배분 점검 오류로 전파
- 일봉 조회에는 3회 재시도가 있었지만 현재가 조회에는 같은 장애내성이 없었음
- 전략·배분 수학 오류가 아니라 Yahoo/yfinance의 일시적 빈 응답을 단일 조회 경로가 그대로 운영 오류로 확대하는 구조였음

수정:

- 현재가 조회를 최대 **3회 bounded retry**
- 각 attempt에서 `Ticker.history()`가 비거나 예외면 같은 시점에 `yf.download()` 1분봉 대체 경로 사용
- intraday yfinance 호출을 data-source lock으로 직렬화
- stale 일봉이나 임의 종가를 현재가로 대체하지 않음
- 두 현재가 경로가 모든 재시도에서 실패한 경우에만 기존처럼 `MarketDataError`로 **fail-closed**
- V3.2.2 전략/배분/HWM75/50→75→100/주문·승인 로직은 변경 없음

회귀테스트:

- `Ticker.history()` 빈 응답 → 같은 attempt의 `yf.download()` 성공
- 두 경로 일시 실패 → 다음 attempt 성공
- 모든 경로 실패 → 3회 후 fail-closed

PR #239 CI:

- Quality Gate ✅
- Security Gate ✅
- canonical Backtest ✅

merge/runtime revision: **`b29196ea94e4bb94ae847f9c94cb47a8c2268799`**

Oracle forced dry-run 배포 run **32960842917**:

- exact latest main 확인 PASS
- focused deployment gate PASS, 집중테스트 50건 PASS
- pinned SSH trust PASS
- release/SQLite snapshot/forced dry-run smoke PASS
- Toss read-only 인증 및 QQQ/TQQQ/SOXL 시세 smoke PASS
- exact deployed SHA `b29196e...` 확인

Runtime Verifier run **32961116752**:

- focused V3.2.2 runtime safety tests PASS
- pinned SSH PASS
- Oracle runtime mode `dry_run` 확인
- phase `pre_market` 확인
- deployed SHA / runtime contract PASS
- Telegram outbound runtime PASS
- 재시작 검증은 시장 상태에 따라 **skipped**
- verifier 전체 결론 **PASS**

실계좌 LIVE-ARMED commissioning이나 실제 주문/canary는 수행하지 않았습니다.

### `/halt` 취소확정 fail-closed hardening

PR #235에서 운영자 긴급 BUY 차단 시 **취소 요청 접수와 실제 원주문 취소 완료를 분리**하도록 보강했습니다.

- `/halt`는 먼저 BUY 차단 barrier를 설정하고 기존 미체결 BUY에 취소 요청을 보냄
- `cancel_order()` HTTP/API 성공만으로 취소 완료를 선언하지 않음
- 취소 요청 후 **원래 broker `orderId`를 다시 조회**
- 조회한 원주문이 동일 `orderId`이고 상태가 `CANCELED`일 때만 `canceled_order_ids`로 확정
- `PENDING_CANCEL`, 취소 중 동시 `FILLED`, 상태조회 실패, 다른 `orderId` snapshot 등은 모두 `uncertain_order_ids`
- uncertain 주문은 임의 재주문/역매매하지 않고 OrderMonitor + broker/DB reconciliation으로 최종상태를 증명
- SELL·monitor·reconciliation과 기존 BUY HALT/SAFE_MODE 경계는 유지

회귀테스트에는 정상 취소확정, PENDING_CANCEL, cancel/fill race, 취소 후 조회 실패, 다른 orderId 응답을 포함했습니다.

PR #235 CI:

- Quality Gate ✅
- Security Gate ✅
- canonical Backtest ✅

merge/runtime revision: **`ec05f778f7b55a01d6d58daec8d41fbbfe0f47c2`**

Oracle forced dry-run 배포 run **32953151181**:

- exact latest main 확인 PASS
- focused deployment gate PASS
- pinned SSH trust PASS
- release/SQLite snapshot/forced dry-run smoke PASS
- Toss read-only smoke PASS
- exact deployed SHA `ec05f778...` 확인

Runtime Verifier run **32953437894**:

- focused V3.2.2 runtime safety tests PASS
- pinned SSH PASS
- Oracle runtime mode `dry_run` 확인
- phase `pre_market` 확인
- deployed SHA / runtime contract PASS
- Telegram outbound runtime PASS
- 재시작 검증은 당시 안전조건에 따라 **skipped**
- verifier 전체 결론 **PASS**

실계좌 LIVE-ARMED commissioning이나 실제 주문/canary는 수행하지 않았습니다.

### Toss write-path no-replay hardening

PR #231에서 Toss broker adapter의 실거래 write 경계를 공식 OpenAPI 계약에 맞춰 추가 보강했습니다.

- 401 응답 시 OAuth 토큰은 갱신하되 **GET/HEAD/OPTIONS read-only 요청만 자동 재요청**
- 주문 생성/취소 **POST는 401이어도 자동 재전송하지 않음**
- timeout/429/5xx/네트워크 불확실성은 기존대로 `UNKNOWN`/reconciliation 우선, blind retry 없음
- force auth refresh 실패 시 거부된 cached token을 남기지 않음
- 미국주식 LIMIT 가격을 broker 경계에서 검증: **$1 이상 소수 2자리, $1 미만 소수 4자리**
- 주문취소 성공 응답은 **별도 operation `orderId`가 존재하고 원주문 ID와 달라야 함**
- malformed cancellation success는 성공으로 간주하지 않고 불확실 상태로 처리
- V3.2.2 전략/배분/HWM75/50→75→100 최초진입/BUY 승인 로직은 변경 없음

PR #231 CI:

- Quality Gate ✅
- Security Gate ✅
- canonical Backtest ✅

merge/runtime revision: **`163cfd9fb0b7325b0e403e59d589c6ac203dd6d2`**

### 실계좌 read-only commissioning 검증

이전 owner-only LIVE-ARMED commissioning run **32948894181**에서 다음까지 실제 검증했습니다.

- live commissioning gate: Ruff + 집중테스트 77건 PASS
- pinned SSH trust PASS
- Toss 인증/시세 smoke PASS
- Toss 계좌 목록·보유·미체결·USD buying power read-only API PASS
- standalone `live-preflight`의 Oracle protected `.env` 로딩 PASS
- 계좌정보·잔고금액 공개 로그 비노출
- 실제 주문/canary 0건

최종 preflight는 당시 실계좌의 기존 **SOXL 보유**를 감지해 `REAL_ACCOUNT_MANAGED_SYMBOL_PRESENT:SOXL`로 의도대로 fail-closed 중단했습니다. 이후 외부 Health Watch run **32950205268**로 Oracle이 정상 forced dry-run에 복구된 것을 독립 확인했습니다.

## 3. 실거래 계좌 운영 계약

실제 JDSS 운용계좌는 **전용·청정 계좌**로 사용합니다.

최초 LIVE-ARMED commissioning 직전 반드시:

- QQQ 기존 보유 **0**
- TQQQ 기존 보유 **0**
- SOXL 기존 보유 **0**
- QQQ/TQQQ/SOXL 미체결 주문 **0**
- 필요한 USD buying power 충족

이어야 합니다.

기존 개인 보유분은 실제 운용 전에 **다른 증권계좌로 이동**하고 JDSS 계좌에서는 개인 보유분과 JDSS 관리분을 공존시키지 않습니다. `feat/live-external-holdings-baseline`의 공존 기능은 production에 병합하지 않습니다.

commissioning 이후에도 JDSS 계좌의 QQQ/TQQQ/SOXL을 외부에서 별도 수동 매매하지 않습니다. broker↔DB 수량이 달라지면 신규 BUY보다 SAFE_MODE/reconciliation을 우선합니다.

## 4. 실거래 준비 상태

- **Oracle dry-run production:** GO
- **yfinance intraday 현재가 장애내성:** GO
- **Toss account read-only API:** GO
- **Toss write-boundary 정적/회귀 검증:** GO
- **`/halt` 취소확정 안전계층:** GO
- **live commissioning 코드/절차:** 준비됨
- **실계좌 최초 commissioning:** 계좌 청정화 전까지 BLOCKED
- **LIVE-ARMED:** 아직 아님
- **LIVE BUY:** LOCKED
- **실제 Toss 주문:** 0건

최초 전환은 아래 순서만 허용합니다.

```text
관리대상 기존 보유/미체결 0 확인
→ fresh live DB
→ real-account preflight
→ BUY HALT arm
→ live 전용 기동
→ Toss holdings/open orders/buying power 재조회
→ broker/DB reconciliation PASS
→ Telegram/account/order 상태 확인
→ 별도 명시적 승인 이후에만 BUY 재개 검토
```

계좌 청정화가 끝나기 전에는 LIVE-ARMED commissioning을 재시도하지 않습니다.

## 5. 실제 주문 전 남은 P0

1. 실운영 직전 QQQ/TQQQ/SOXL 기존 보유 및 미체결 주문 0을 당일 read-only preflight로 확인
2. **fresh separate live DB**로 owner-only LIVE-ARMED commissioning PASS
3. `live_commissioned=1`, `operator_buy_halt=1`, broker↔DB reconciliation PASS 확인
4. 실제 Toss write path(`POST order → orderId/clientOrderId → 주문조회 → 부분/완전체결·취소 → 원장반영`)를 **실계좌에서 별도 commissioning**으로 최종 검증
5. timeout/401/429/5xx/UNKNOWN에서 동일 write 요청을 자동 replay하지 않는 계약 유지
6. 모든 Go-Live 체크리스트 PASS 뒤에만 **별도 명시적 승인**으로 BUY 잠금을 해제

실계좌 canary나 최초 BUY는 자동 실행하지 않습니다.

## 6. 운영상 유지 원칙

- 위험축소 SELL은 자동, 위험증가 BUY는 운영자 승인
- 현재가 공급자 일시 오류는 bounded retry + 대체 intraday 경로로 흡수하되, 끝까지 가격을 증명하지 못하면 임의 stale 가격 대신 fail-closed
- 긴급 이상 시 `/halt`로 BUY를 우선 차단하고 SELL·monitor·reconciliation은 유지
- `/halt`의 취소 요청 성공을 원주문 취소 완료로 간주하지 않으며, 원주문 `CANCELED`가 확인되지 않으면 uncertain으로 유지
- uncertain 취소·UNKNOWN·원장 불일치·미완료 위험축소는 신규 BUY보다 SAFE_MODE/reconciliation 우선
- `N건 순차 실행`은 원자적 basket이 아니며 일부 주문만 제출될 수 있음
- 이미 제출된 주문을 임의 역매매하여 자동 rollback하지 않음
- dry-run 원장과 live 원장을 절대 혼용하지 않음
- broker side effect 가능 이후 과거 DB snapshot을 현재 broker 상태 위에 맹목 복원하지 않음
- Oracle 배포 승인, LIVE-ARMED commissioning, BUY 잠금 해제 승인을 서로 다른 행위로 취급
- QLD/SSO/v3.3 연구는 production V3.2.2와 계속 분리
