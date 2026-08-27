# JH_HOLDINGS Current Work

> **현재 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 규범 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 운영 방법은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md), 실거래 전환·사고 대응은 [`docs/infra/LIVE_COMMISSIONING.md`](docs/infra/LIVE_COMMISSIONING.md)를 따릅니다.

## 1. Production 상태

- 공식 릴리즈: **v3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 최신 runtime 기능 revision: **`0f4fe92ebbae4e91b6564645e4618ea5b4e8f5ff`**
- Oracle runtime 기능 revision: **`0f4fe92ebbae4e91b6564645e4618ea5b4e8f5ff`**
- Oracle 서비스: **active**
- 현재 운용 모드: **LIVE-ARMED (`trading_mode=live`)**
- live commissioning marker: **ON**
- 운영자 BUY 차단: **ON (`operator_buy_halt=1`)**
- `portfolio.live_enabled`: **false** — 일반/default live 경로는 계속 잠금
- 실제 Toss 주문: **0건**
- 실제 BUY/SELL canary: **미실행**
- SGOV 자동운용: **OFF**
- 관리종목: **QQQ / TQQQ / SOXL**
- 정수주 주문만 허용
- 위험증가 BUY: **운영자 BUY 잠금 해제 + 기존 2단계 승인 필요**
- 위험축소 SELL: **자동**, 단 불확실 상태에서는 SAFE_MODE/reconciliation 우선
- LIVE 프로세스 재시작 시 BUY HALT 자동 재설정
- 외부 Oracle health watch: **매시간 + owner-only on-demand**

runtime 영향이 없는 문서 commit이 `main`에 추가될 수 있으므로 GitHub HEAD와 Oracle runtime 기능 revision은 항상 같을 필요는 없습니다. 문서-only main 변경은 LIVE runtime 재배포 사유가 아닙니다.

## 2. 2026-08-27 LIVE-ARMED 전환 완료

### Pre-live 코드리뷰 및 P0 hardening — PR #253

첫 실계좌 전환 직전 주문·복구·운영 경계를 다시 리뷰해 다음을 보강했습니다.

1. **자동 위험축소 SELL 지정가 호가단위 정규화**
   - 현재가에 SELL buffer를 곱하면 미국주식 주문 허용 소수자릿수를 넘길 수 있던 경계를 수정
   - `$1 이상`은 0.01, `$1 미만`은 0.0001 단위로 **하향 정규화** 후 원장 예약/브로커 제출
   - 위험축소 SELL이 산술 소수자릿수 때문에 broker boundary에서 거부되는 경로를 제거

2. **sticky SAFE_MODE 최종 BUY 경계 강화**
   - OrderManager가 BUY 예약 전 portfolio/symbol SAFE_MODE 확인
   - 실제 broker submit 직전 execution lock 내부에서 다시 확인
   - 중간에 SAFE_MODE가 켜지면 예약 주문은 REJECTED 처리하고 broker write 금지

3. **`/resume` SAFE_MODE 해제 방지**
   - reconciliation이 깨끗해도 sticky SAFE_MODE가 남아 있으면 BUY HALT 해제 거부
   - 최종 flag 전환 직전 execution lock 내부에서 SAFE_MODE를 다시 확인

4. **LIVE Telegram 안내 정합성**
   - LIVE runtime에서 forced-dry-run 안내가 노출되지 않도록 mode-aware 표시
   - LIVE 연결 여부와 BUY 잠금 상태를 구분하여 표시

5. **commissioning 집중 게이트 강화**
   - 위 pre-live 회귀테스트를 실제 LIVE-ARMED commissioning 직전 집중테스트에도 포함

검증:

- Quality Gate ✅
- Security Gate ✅
- canonical Backtest ✅ — V3.2.2 전략 결과 변경 없음
- merge/runtime revision: **`0f4fe92ebbae4e91b6564645e4618ea5b4e8f5ff`**

### LIVE-ARMED commissioning — run `33028159215`

owner-only commissioning에서 다음을 순서대로 통과했습니다.

- exact latest main 확인 ✅
- live 집중게이트: Ruff + **95 tests PASS** ✅
- config/전략 계약 검증 ✅
- pinned SSH trust ✅
- 최신 main을 forced dry-run으로 먼저 안전 배포/검증 ✅
- Toss 계좌/보유/미체결/매수가능금액 **read-only preflight** ✅
- 관리종목 QQQ/TQQQ/SOXL commissioning blocker 없음 ✅
- fresh live 원장 생성 및 commissioning ✅
- live release check/reconciliation ✅
- Toss read-only smoke ✅
- 최종 **`BUY_HALT=1`** 확인 ✅

commissioning은 실제 BUY/SELL 주문이나 canary 주문을 제출하지 않았습니다.

### Post-commission independent verifier — run `33028387196`

commissioning과 별도 workflow로 다시 확인했습니다.

- exact deployed SHA `0f4fe92e...` ✅
- focused runtime safety tests **68 PASS** ✅
- runtime mode **live** ✅
- live commissioning marker ✅
- **BUY_HALT=1** ✅
- 전략/config/runtime contract ✅
- Toss 인증/시세 read-only smoke ✅
- Telegram outbound smoke ✅
- LIVE에서는 운영자 의도 없는 상태변화를 막기 위해 자동 restart 검증 **의도적으로 미수행** ✅

Issue #254와 #255는 성공 확인 후 `completed`로 종료했습니다.

## 3. 실계좌 / 자산 격리 계약

- 최초 commissioning 시 QQQ/TQQQ/SOXL 기존 보유 **0**, 관리종목 미체결 **0** 필요
- QQQ/TQQQ/SOXL 외 비관리 종목은 같은 Toss 계좌에 공존 가능
- 비관리 종목은 JDSS 원장, HWM75, target/rebalancing, 자동 SELL, reconciliation 대상에서 제외
- Toss `cashBuyingPower`는 JDSS 자산으로 합산하지 않고 **실제 계좌 유동성 상한**으로만 사용
- 개인 주문으로 buying power가 줄면 JDSS BUY가 축소/차단될 수 있음
- commissioning 이후 QQQ/TQQQ/SOXL을 JDSS 외부에서 수동 매매하지 않음
- 관리종목 broker↔DB 불일치 시 신규 BUY보다 SAFE_MODE/reconciliation 우선

## 4. 확정 전략 및 최초진입

2026-09-01 적용 production은 **V3.2.2 유지**입니다.

- 최초진입: **50% → 75% → 100%**
- 단계 간격: 각 단계 목표 충족 후 **최소 3 미국 거래세션**
- 최초 1단계: 목표수량의 50%, **정수주**
- 9월 첫 거래일이라는 이유로 1거래일을 임의 대기하는 규칙은 추가하지 않음
- 월초 reset으로 full target이 바뀌면 onboarding stage cap 안에서 새 목표를 반영하고, 새 stage 목표 충족 시점부터 3거래세션을 다시 계산
- V3.3 / Ultra Alpha는 SHADOW 연구 유지, production 승격 보류

최초진입 연구 및 월초 타이밍 결과는 [`docs/research/STRATEGY_FREEZE.md`](docs/research/STRATEGY_FREEZE.md)를 따릅니다.

## 5. 주요 운영사고 시 fail-closed 계약

### 데이터/시세 장애

- bounded retry와 허용된 대체 경로 사용
- 끝까지 가격을 증명하지 못하면 stale 가격으로 주문하지 않고 실패 처리
- 신규 배분/BUY 오류와 별개로 가능한 monitor/reconciliation은 계속 수행

### BUY 부분체결 / 거부

- 실제 체결분만 원장 반영
- 잔여수량 자동 재주문 금지
- 최신 가격·수량으로 새 승인 필요

### timeout / 429 / 5xx / 네트워크 불확실

- broker write 결과를 증명하지 못하면 `UNKNOWN`
- 동일 write 요청 blind retry 금지
- broker 조회 + reconciliation으로 최종상태 증명

### 401 인증 오류

- 토큰 갱신은 가능하지만 side-effecting 주문/취소 POST를 같은 호출에서 자동 replay하지 않음
- read-only 요청만 제한적으로 재시도 가능

### 자동 위험축소 SELL 실패/부분체결

- 체결분만 원장 반영
- 미완료 위험축소가 있으면 신규 BUY 차단
- SAFE_MODE / monitor / reconciliation 우선

### 여러 BUY 순차 실행 중 중간 실패

- batch는 원자적 basket이 아님
- 앞선 주문이 이미 broker에 제출됐다면 자동 역매매 rollback 금지
- 뒤 주문/잔여 승인 중단 후 현재 broker 상태를 기준으로 reconciliation

### `/halt` 취소 race

- BUY barrier를 먼저 설정
- cancel 요청 접수만으로 취소완료 간주 금지
- 동일 원주문의 broker 상태가 `CANCELED`임을 확인해야 확정
- PENDING_CANCEL, 동시체결, 조회 실패 등은 uncertain으로 남기고 monitor/reconciliation

### 서버/프로세스 재시작

- LIVE 시작/재시작 시 BUY HALT 자동 재설정
- broker side effect 가능 이후 과거 DB snapshot을 현재 broker 상태 위에 맹목 복원하지 않음
- broker holdings/orders와 reconciliation을 우선

## 6. 현재 남은 P0 — 실제 첫 BUY 전

1. **BUY HALT를 유지한다.** 현재 `operator_buy_halt=1`.
2. 2026-09-01 첫 진입 직전 최신 시장·매크로·JDSS 목표를 재확인한다.
3. 실제 계좌 QQQ/TQQQ/SOXL 보유/미체결 및 USD buying power를 read-only로 다시 확인한다.
4. broker↔live 원장 reconciliation PASS를 다시 확인한다.
5. Telegram `/dashboard`, `/account`, `/order`, `/errors` 상태를 확인한다.
6. 최초진입 50% 목표의 **정수주 수량과 지정가**를 운영자가 검토한다.
7. 위 검증 후에만 별도 명시적 결정으로 BUY HALT 해제를 수행한다.
8. BUY HALT 해제 후에도 각 위험증가 BUY는 기존 **2단계 승인**을 반드시 거친다.

현재 상태에서는 실계좌 연결은 완료됐지만 **실제 주문 실행 권한은 열지 않은 상태**입니다.
