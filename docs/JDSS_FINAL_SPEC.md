# JDSS 공식 사양 — Production 규범 계약

현재 공식 전략 ID는 **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**입니다.

이 문서는 production 구현이 따라야 하는 **규범 계약**입니다. 실행 숫자는 [`../strategy.yaml`](../strategy.yaml), 실제 구현은 [`../src/jd_holdings/`](../src/jd_holdings/), 현재 배포·live 상태는 [`../CURRENT_WORK.md`](../CURRENT_WORK.md)가 기준입니다.

`FINAL`은 버전별 복사본을 뜻하지 않습니다. 새 릴리즈에서도 이 파일을 제자리 갱신하고 과거 계약은 Git tag와 [`HISTORY.md`](HISTORY.md)에서 복구합니다.

## 1. 관리 자산과 자금 계약

JDSS V3.2.2가 직접 관리하는 티커는 **QQQ, TQQQ, SOXL**입니다. SOXX는 상대강도 판단용 기준 ETF이며 직접 allocation 주문을 만들지 않습니다.

자금 계약:

- 시작 위험원금: **$50,000**
- HWM75 위험예산:
  `min(현재 평가액, 50,000 + 0.75 × max(0, 최고 평가액 - 50,000))`
- 새 최고자산 초과이익의 75%만 위험예산 증가에 반영
- 나머지 25% 이익은 JDSS 현금으로 남지만 위험예산 증가에 사용하지 않음
- 손실 시 외부 현금 자동보충 금지
- SGOV 자동운용 OFF
- live OFF

HWM은 완결 거래일 종가 기준 JDSS 평가액으로만 갱신합니다.

## 2. QQQ 목표 노출

사용 지표:

- SMA50, SMA200
- 21 / 63 / 126 거래일 수익률
- SMA200의 21거래일 기울기
- QQQ 20거래일 연환산 변동성

판정 순서:

1. 필요한 warmup이 없으면 1.0x
2. QQQ 20일 연환산 변동성 ≥ 30%이면 0.5x
3. QQQ 종가 ≤ SMA200이면 1.0x
4. SMA50>SMA200이고 63d>0, 126d>0이면 1.5x
5. `21d>0 / 63d>0 / 126d>0 / SMA50>SMA200 / SMA200 slope>0` 중 3개 이상이면 1.25x
6. 나머지는 1.0x

새 달 첫 거래일 완결 종가에서 전체 레짐을 reset하고 다음 미국 거래세션부터 새 목표를 반영합니다.

월중 QQQ 20일 연환산 변동성이 30% 이상이 되면 0.5x로 감속할 수 있습니다. 같은 달 안에서 위험노출을 다시 상향하지 않습니다.

## 3. 목표 노출의 ETF 배분

1.0x 이하에서는 QQQ만 사용합니다.

1.0x 초과분은 3배 ETF로 합성합니다.

`leveraged sleeve weight = (target leverage - 1) / 2`

따라서 기본 배분은 다음과 같습니다.

| 목표 노출 | QQQ | 3배 ETF sleeve | 현금 |
|---:|---:|---:|---:|
| 0.5x | 50% | 0% | 50% |
| 1.0x | 100% | 0% | 0% |
| 1.25x | 87.5% | 12.5% | 0% |
| 1.5x | 75% | 25% | 0% |

## 4. RS6M 반도체 상대강도

- 기준 ETF: **SOXX**
- lookback: **126 거래일**
- ON 조건: SOXX 126d return > 0 **그리고** SOXX 126d return > QQQ 126d return

동작:

- RS6M OFF → 3배 ETF sleeve 100% TQQQ
- RS6M ON → sleeve 50% TQQQ + 50% SOXL
- 월중 ON 조건 상실 → SOXL portion을 TQQQ로 위험축소 전환
- 같은 달 안에서는 SOXL 재진입 금지
- 다음 월 reset에서만 SOXL 재진입 여부를 새로 판단

production frozen proxy는 SOXX입니다. 대체 proxy 연구 결과만으로 production proxy를 바꾸지 않습니다.

## 5. 최대 5% 추가 레버리지 계약

기존 H40-S3 과매도·반등 로직은 virtual state로 계속 계산하되 독립 자금으로 직접 주문하지 않습니다.

- TQQQ virtual cycle만 활성 → QQQ 최대 5%를 TQQQ로 이동
- SOXL virtual cycle만 활성 → QQQ 최대 5%를 SOXL로 이동
- 둘 다 활성 → 총 5%를 2.5%씩 분배
- virtual active state는 미래 데이터 사용을 막기 위해 한 거래일 지연 반영

기존 직접 H40 포지션, TP plan, direct booster 주문은 정상 V3.2.2 allocation 상태가 아니며 reconciliation 오류 대상으로 취급합니다.

사용자 화면에서는 `overlay` 같은 내부 구현명보다 `추가매수 판단` 또는 `추가 레버리지` 표현을 우선합니다.

## 6. 최초진입 50% → 75% → 100%

최초 계좌 적용 시 최종 전략 목표와 HWM75 위험예산을 바꾸지 않고, **위험증가 BUY의 허용 목표만 단계적으로 확대**합니다.

- 1차: 최종 전략 목표의 누적 50%
- 2차: 누적 75%
- 3차: 누적 100%
- 다음 단계는 현재 단계 목표가 전량 체결된 뒤 최소 3 미국 거래일 경과 필요
- 다음 단계는 자동 개방 금지; `/onboarding`에서 운영자 승인 필요
- 단계 개방은 주문 제출이 아니며 실제 BUY는 일반 BUY 승인 계약을 다시 통과
- 전략 목표가 낮아지면 onboarding 단계보다 위험축소 SELL 우선
- 관리 포지션·미체결 allocation 주문·SAFE_MODE 등 시작 안전조건이 충족되지 않으면 onboarding 시작 차단
- 최종 단계 목표가 모두 체결되면 `COMPLETED`로 종료
- Telegram 단계 callback은 생성 당시 단계와 현재 DB 단계를 대조하고 stale 버튼을 거부

정확한 비율·대기일은 `strategy.yaml`의 `market_regime.v322_allocation.initial_onboarding`을 따릅니다.

## 7. 완결봉, 목표수량, 주문 세션

전략 판단과 주문수량 고정은 분리합니다.

1. 완결봉에서 목표비중과 전략 generation을 저장
2. 목표가 새로 바뀌면 다음 미국 거래세션 시작 후 최신 가격으로 `target_qty`를 한 번 고정
3. 고정 target과 현재 보유·열린 주문·미반영 체결의 차이로 주문 gap을 계산

목표수량 고정, 자동 위험축소 SELL, BUY 검토·실행은 설정상 허용된 주문 세션에서만 수행합니다.

Toss 주문 점검시간인 **08:50~08:59 KST**에는 새 주문 검토·제출을 만들지 않습니다.

## 8. 위험축소 SELL 우선 계약

위험축소 SELL은 사람 승인을 기다리지 않는 자동화 대상입니다.

같은 목표변경에서 SELL과 BUY가 함께 필요하면 반드시 다음 순서를 지킵니다.

1. 기존 allocation 주문 상태 최신화
2. 필요한 위험축소 SELL 제출
3. SELL 종료 상태 확인
4. 확정 체결을 원장에 delta 반영
5. 브로커/원장 reconciliation 확인
6. SAFE_MODE가 없을 때만 위험증가 BUY 허용

`SUBMITTED`, `PARTIAL_FILLED`, `PENDING_CANCEL`, `PENDING_REPLACE`, `UNKNOWN` 등 종료되지 않은 SELL이 남아 있으면 신규 BUY를 허용하지 않습니다.

불완전 위험축소, 체결상태 불명, 브로커/DB 수량 불일치는 SAFE_MODE 사유입니다.

## 9. 위험증가 BUY와 Telegram 일괄 승인 계약

BUY는 반자동입니다. 기본 운영자 UX는 **`오늘 주문 한번에 검토` → `N건 순차 실행` 최종 승인**입니다.

### 9.1 일괄 검토 preflight

batch 검토를 만들기 전에 최소한 다음을 확인합니다.

- 현재 주문 허용 세션
- Toss 점검시간이 아님
- 최신 완결 거래일의 V3.2.2 계산 freshness
- 새 목표의 다음 거래일 / target_qty 준비 상태
- 열린 위험축소 SELL 없음
- 기존 BUY 미체결과 중복 위험 없음
- 즉시 broker/DB reconciliation 성공
- SAFE_MODE 없음
- 각 BUY 신호가 현재 전략 generation·버전과 일치
- 전체 예상 BUY 합계가 HWM75 위험예산, JDSS 현금, 브로커 주문가능금액의 경계를 넘지 않음

단순히 active BUY signal이 0건이라는 이유만으로 `오늘 주문 없음`이라고 판정하지 않습니다. 저장 target_qty와 현재 보유수량을 다시 비교해 계산 지연·SELL 준비·BUY 신호 생성 대기·onboarding 단계대기와 진짜 주문 없음을 구분합니다.

### 9.2 approval과 동시성

- 각 신호의 review approval에서 최신 가격·수량·세션을 계산
- execution approval은 짧은 TTL과 1회용 token을 유지
- batch 생성과 실행은 lock으로 직렬화
- 유효한 batch가 이미 있으면 중복 batch를 새로 만들지 않음
- 검토 중 일부 execution approval 생성 후 오류가 나면 이미 생성된 approval을 cleanup
- 서버 재시작 뒤 메모리 batch는 유효한 주문권한으로 복구하지 않고 새 검토 요구

### 9.3 최종 실행 직전 재검증

최종 순차 실행 버튼을 누르는 순간 다음을 다시 검사합니다.

- broker/DB reconciliation
- SAFE_MODE
- 새 미체결 주문 여부
- 전체 HWM75/현금/브로커 주문가능금액
- 각 종목 가격·수량·세션

검토 때와 가격·수량·세션이 다르면 기존 승인을 사용하지 않고 재검토를 요구합니다.

### 9.4 순차 제출

batch는 원자적 basket 주문이 아닙니다.

- QQQ, TQQQ, SOXL의 안정된 정의 순서에 따라 필요한 종목만 독립 주문으로 제출
- 각 주문은 기존 TradingService/OrderManager 주문예약·멱등성 계약을 그대로 사용
- 한 주문이 가격변경, `UNKNOWN`, `REJECTED`, `CANCELED`, `REPLACED` 또는 기타 fail-closed 조건에 걸리면 이후 BUY를 중단하고 남은 approval을 취소
- 이미 제출된 앞선 주문을 자동으로 반대매매해 rollback했다고 가정하지 않음
- 제출된 주문은 실제 주문상태로 계속 감시

### 9.5 개별 승인 경로

`/signal` 개별 2단계 승인은 비상용·상세 확인 경로로 유지할 수 있습니다. 일괄 batch와 개별 승인을 동시에 운용하지 않는 것을 기본 원칙으로 합니다.

## 10. 주문 멱등성·부분체결·재시작

- 모든 주문은 결정적 client order ID와 DB 예약으로 멱등성 확보
- 주문예약 트랜잭션에서 HWM75 예산과 종목 잔여 target을 함께 재검사
- 열린 BUY의 잔여 지정가·수수료를 현금/위험예산에서 예약
- 열린 코어 BUY와 아직 allocation 원장에 반영되지 않은 체결수량을 잔여 목표에서 차감
- 동일 client order ID 재시도는 브로커 최신 상태를 DB에 먼저 저장한 뒤 체결 delta만 반영
- broker receipt의 client order ID, broker order ID, 종목, 방향, 주문수량, 체결수량을 예약값과 대조
- 불일치 receipt는 성공으로 처리하지 않고 `UNKNOWN`
- 누적 체결수량은 감소할 수 없음
- 종료 주문은 열린 상태로 되돌릴 수 없음
- 누적 체결수량·누적 체결금액은 이전 원장 적용값과의 delta만 반영
- `PENDING_CANCEL`, `PENDING_REPLACE`를 포함한 비종료 상태는 열린 주문으로 예약·감시
- 재시작 때 같은 strategy generation의 저장 target_qty와 보유·열린 주문의 차이만 미완료 BUY gap 복구 후보로 사용
- 과거 H40-S3 직접 BUY 신호는 V3.2.2 실행 계층에서 무효화

## 11. batch 감사로그

일괄 주문의 다음 사건은 SQLite 이벤트로 남겨 Telegram 결과 메시지가 유실돼도 추적할 수 있어야 합니다.

- batch 검토 생성
- preflight/한도 차단
- batch 만료·취소
- 실행 시작
- 종목별 제출 결과
- 전체 성공 또는 부분실패

감사로그에 승인 token·인증정보·전체 계좌번호를 기록하지 않습니다.

## 12. SAFE_MODE

다음은 신규 BUY를 차단하는 대표 조건입니다.

- 주문 결과 `UNKNOWN`
- 위험축소 SELL 미완료 또는 상태 불명
- 브로커/DB 보유수량 불일치
- 열린 주문 정합성 불일치
- 전략 generation·버전 불일치
- 시작/restart 복구 상태를 증명할 수 없음

QQQ 이상은 portfolio 차원의 SAFE_MODE로 취급하고, TQQQ/SOXL 이상은 종목 SAFE_MODE와 portfolio reconciliation에 반영합니다.

SAFE_MODE는 단 한 번의 정상 조회만으로 자동 해제하지 않습니다.

## 13. forced dry-run과 실제 Toss 경계

- forced dry-run의 주문·보유·열린 주문은 `MarketDataDryRunBroker`와 SQLite JDSS 원장에서 관리
- dry-run reconciliation은 SQLite와 모의 브로커를 대조
- 실제 Toss 계좌는 `/account`, 계좌 요약, `toss-smoke` 등 read-only 경로로 별도 조회
- Toss 조회 결과를 dry-run 원장에 자동 채택하지 않음
- dry-run 주문을 Toss 실주문으로 자동 변환하지 않음
- 실제 계좌 조회 실패·불명확 값을 0 또는 성공으로 임의 해석하지 않음

따라서 dry-run reconciliation 성공을 실제 Toss 보유수량과의 일치로 표현하지 않습니다.

## 14. 동일 Toss 계좌의 관리 티커

브로커는 같은 티커 수량을 합산하므로 동일 Toss 계좌에 개인 QQQ/TQQQ/SOXL을 섞으면 JDSS 원장과 분리할 수 없습니다.

따라서 JDSS 관리 계좌에서 **개인 QQQ/TQQQ/SOXL 혼합보유를 금지**합니다. QQQM처럼 다른 티커는 별도 수량으로 구분할 수 있습니다.

JDSS 주문과 Toss 앱의 수동 동일티커 주문을 동시에 수행하지 않는 것을 운영 원칙으로 합니다.

## 15. 실제 계좌 적용 preflight

전략 채택, Oracle 배포, live 주문 활성화는 서로 다른 변경입니다.

live 잠금을 검토하기 전 최소한 다음을 증명해야 합니다.

1. 배포 SHA, package/config/strategy ID와 이 사양의 일치
2. 설정 검증, 단위·통합 테스트, canonical no-lookahead 백테스트 통과
3. 기존 DB 전략세대·schema·열린 주문·부분체결·UNKNOWN·legacy 상태와 복구 가능한 backup
4. 실제 Toss 관리 티커 보유수량·열린 주문·주문가능금액·개인 동일티커 혼합 여부
5. forced dry-run에서 목표 산출, SELL-first, batch BUY, onboarding, 주문 감시, 재시작, reconciliation 한 사이클
6. 가격·수량·세션·onboarding 단계 변경 시 기존 approval 폐기 확인
7. 실제 주문 어댑터·회계·복구 경계 검증
8. 사용자의 별도 명시적 live 승인

체크리스트가 문서에 존재한다는 이유만으로 live 준비가 완료됐다고 간주하지 않습니다.

## 16. 백테스트 계약

- 시작: 2011-01-01
- 초기자금: $50,000
- buy fee: 0.1%
- sell fee: 0.1%
- 기본 slippage: 0.1%
- next-session execution
- 보고 시작일을 빈 계좌의 최초진입일로 간주
- 1~3번째 미국 거래세션: 최종 목표의 50%
- 4~6번째: 75%
- 7번째부터: 100%
- 백테스트는 각 단계 체결 완료를 가정해 최소 세션 경과 후 자동 진행; production의 `/onboarding` 운영자 승인을 대체하지 않음
- HWM75 적용
- SGOV OFF
- production과 동일 allocation/JDSS virtual-state 함수를 공유

승인된 사람이 읽는 기준 결과는 [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md)가 소유합니다. 실행별 run ID와 artifact를 이 규범 문서에 누적하지 않습니다.

## 17. 연구와 production 경계

연구 브랜치의 후보, SHADOW 전략, 높은 백테스트 수치는 사용자의 별도 채택과 정식 구현 PR 없이는 production 계약이 아닙니다.

연구 검증은 [`research/RESEARCH_PROTOCOL.md`](research/RESEARCH_PROTOCOL.md), 대표 채택·기각 이력은 [`HISTORY.md`](HISTORY.md)를 따릅니다.

## 18. live hard lock

현재 계약은 **실제 Toss 주문 활성화를 포함하지 않습니다.**

다음 잠금을 동시에 유지합니다.

- `portfolio.live_enabled=false`
- 애플리케이션 live hard lock
- Oracle forced dry-run
- 빈 live confirmation

미래에 live를 변경하려면 제15절 preflight와 별도의 명시적 승인, 코드·설정·문서·테스트의 동시 변경이 필요합니다.
