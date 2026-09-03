# JH_HOLDINGS 보안·주문 안전 기준

이 문서는 **JH AUTO 1.0.0 실거래의 보안·주문 안전 불변식**을 소유합니다. 투자전략 숫자는 [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md)와 [`../../strategy.yaml`](../../strategy.yaml), 자동운용 자금·상태계약은 [`../JH_AUTO_SPEC.md`](../JH_AUTO_SPEC.md), 현재 배포상태는 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)를 따릅니다.

## 1. 운영자가 먼저 기억할 원칙

1. **배포와 자동운용 시작은 별개** — `/auto start` 최초 승인 전 실제 신규 BUY 0건
2. **정상 JH AUTO에서는 개별 BUY마다 사람이 승인하지 않음** — 기존 review/execution 2단계 검증코드는 JH AUTO가 내부적으로 소비
3. **결과가 불명확하면 재주문하지 않음** — `UNKNOWN`은 실패가 아니라 결과 미확정
4. **계좌와 원장이 다르면 신규 BUY 차단**
5. **대표 `/halt`는 시스템이 자동해제하지 않음**
6. **JDSS `$50,000`은 연구 기준값** — 실거래 한도는 대표가 정한 JH AUTO 자금상태에서 계산

## 2. 신뢰 경계

| 경계 | 신뢰 가능한 것 | 항상 다시 검증할 것 |
|---|---|---|
| Telegram | 설정된 관리자 Chat ID | private chat, `from_user`, callback token/TTL, 현재 DB 상태 |
| JDSS | 검증된 전략 설정과 목표비중 | 데이터 freshness, generation, 현재 자금경계 |
| JH AUTO | 커밋된 자동운용 상태 | 기준자금·비율·허용원금·launch/quarantine/halt 상태 불변식 |
| SQLite | transaction으로 확정된 원장 | broker 실제 상태와 reconciliation |
| Toss OpenAPI | 공식 HTTPS endpoint의 현재 응답 | 인증, 숫자범위, order ID, 실제 주문/보유/매수가능금액 |
| GitHub Actions | 보호된 main과 승인된 workflow | source SHA, 실행주체, Environment, secret 존재 |
| Oracle | 배포된 release | 서비스 단일실행, DB, 계좌·원장, BUY 잠금 |

한 경계의 성공을 다른 경계의 성공으로 간주하지 않습니다.

## 3. 공개 저장소와 비밀정보

Git·로그·Markdown·Issue·테스트 fixture에 `.env` 실제 값, Telegram token, Toss key/secret·인증 header, SSH private key, 전체 계좌번호, approval raw token, GitHub Environment secret을 기록하지 않습니다.

공개 문서에는 불필요한 서버 절대경로·OS 사용자명·host 식별자·실제 backup 파일명을 남기지 않습니다. 노출이 의심되면 BUY를 먼저 잠그고 자격증명을 폐기·재발급한 뒤 Gitleaks와 로그로 범위를 확인합니다.

## 4. GitHub 변경 통제

- 기능 변경은 별도 branch + PR
- 보호된 `main` 직접 push/force push 금지
- Quality Gate / Security Gate 필수
- 전략·백테스트 민감 변경은 canonical Backtest 확인
- Actions 기본 권한은 최소권한
- 외부 Action은 검증된 commit SHA로 고정
- LIVE 배포는 저장소 owner가 명시적으로 요청한 경로만 사용
- 배포 전 BUY halt를 먼저 ON
- 배포는 `/resume`, `/auto start`, 자금증액을 대신하지 않음

## 5. Telegram 관리자 인증

- 정확히 1개의 관리자 Chat ID 허용
- private chat만 허용
- 명령과 callback 모두 `chat.id`와 `from_user.id` 확인
- callback payload만 믿지 않고 DB 현재상태 재검증
- stale/만료/재사용 token 거부
- Telegram에는 정제된 오류요약만 표시하고 traceback은 Oracle 로그에서 확인

## 6. 최초 JH AUTO 시작승인

첫 배포 또는 아직 시작하지 않은 상태에서는 `launch_authorized=0`, 현재 허용원금 0, startup quarantine ON, 실제 신규 BUY 차단을 유지합니다.

대표는 `/auto`에서 운용 기준자금과 자동운용비율을 설정하고 `/auto start`의 2단계 확인을 직접 수행합니다.

**최종 start callback은 주문을 제출하지 않습니다.** callback 전후 열린 주문 수가 달라지면 안전격리합니다. 첫 BUY는 이후 별도의 scheduler 안전주기에서만 가능합니다.

## 7. `$50,000` 연구 기준과 LIVE 자금 분리

JDSS 3.2.2의 `$50,000`은 공식 연구·백테스트 회귀 기준입니다. LIVE에서는 JH AUTO 상태가 존재하면 이 값을 실거래 한도로 fallback하지 않습니다.

```text
운용 기준자금
× 자동운용비율
= 목표 자동원금
→ 50/75/100 단계
= 현재 허용원금
→ HWM75
= 현재 위험예산
```

최초 JH AUTO launch에서는 과거 V3.2.2 원장에 남아 있을 수 있는 legacy `$50,000` HWM/risk state를 그대로 이어받지 않습니다. launch preflight가 성공한 뒤 HWM 기준을 새 JH AUTO 1단계 현재 허용원금으로 재기준화합니다.

운용 기준자금·자동운용비율 변경은 외부 자금흐름으로 처리하여 수익률을 왜곡하지 않습니다.

## 8. 위험증가 BUY 최종 불변식

신규 BUY는 최종 주문예약 경계에서 최소 다음을 다시 확인합니다.

- JH AUTO 설치·정상상태
- 최초 시작승인 ON
- startup/system quarantine OFF
- 대표 긴급정지 latch OFF
- AUTO state `RUNNING`
- `operator_buy_halt=0`
- SAFE_MODE 없음
- 기준자금·자동운용비율·목표원금·현재 허용원금의 상호관계 정상
- 현재 허용원금 > 0
- HWM75 현재 위험예산 정상
- 현재 보유 + committed open BUY + 이번 주문이 목표수량을 넘지 않음
- managed cash / HWM75 / broker buying power 범위 안
- 실제 broker POST 직전 미국 정규장 재확인

하나라도 증명되지 않으면 fail-closed입니다.

## 9. 내부 2단계 검증과 자동실행

과거 사람이 누르던 review → execution approval 코드를 삭제하거나 우회하지 않습니다. JH AUTO는 한 안전주기에서 활성 BUY signal → review approval → 최신 가격·수량 검증 → execution approval → 실행 직전 재검증 → OrderManager → Toss 순서로 내부 처리합니다.

정상 JH AUTO 운영에서 운영자에게 **개별 주문 최종승인을 요구하지 않습니다.** 사람이 직접 누르는 기존 승인 UI는 호환/비상 경로일 수 있으나 현재 기본 운영계약이 아닙니다.

## 10. 정규장과 주문속도 제한

JH AUTO 자동 allocation 주문은 미국 정규장에만 허용합니다.

- 한 깨끗한 안전주기 신규 BUY 최대 1건
- 동시에 활성 BUY가 있으면 다음 신규 BUY 금지
- 동일 signal/일일 자동시도 상한 적용
- 실제 POST 직전 현재시각으로 세션 재검사

전략설정의 장전·장후 허용값은 기존 경로 호환정보이며 JH AUTO 자동 allocation 세션을 넓히지 않습니다.

## 11. SELL-first

목표 위험이 줄어들면 SELL을 BUY보다 먼저 처리합니다. SELL 주문상태 확정 → 체결 delta 원장반영 → broker/DB reconciliation이 정상이어야 다음 BUY가 가능합니다.

부분체결·미완료·`UNKNOWN`·취소확인 실패가 있으면 BUY를 차단합니다.

## 12. 주문 멱등성과 write 재시도 금지

- 결정적 `client_order_id`
- DB transaction에서 주문예약
- broker receipt의 client/broker ID, symbol, side, qty 검증
- 누적 filled qty 감소 거부
- 종료주문의 비종료상태 회귀 거부
- cumulative fill은 이전 적용분과의 delta만 원장반영
- POST timeout/응답유실/결과불명은 `UNKNOWN`
- **계좌를 바꾸는 POST/취소 요청은 결과가 애매하면 blind replay 금지**

조회전용 GET의 명확한 일시오류만 제한적으로 재시도할 수 있습니다.

## 13. 미체결·부분체결

신규 AUTO BUY가 체결대기 한도를 넘으면 취소 요청 후 원주문을 다시 확인합니다. 취소가 확실하면 실제 체결분을 반영한 뒤 다음 주기에 새 목표를 계산하고, 취소상태가 불명확하면 SAFE_MODE로 신규 BUY를 막습니다.

부분체결 잔량을 즉시 같은 주문으로 반복전송하지 않습니다.

## 14. 자금투입 50→75→100

최초 시작과 증액은 새 위험을 단계적으로 엽니다. 각 단계 승격에는 현재 목표충족, 미체결 0, reconciliation 정상, SAFE_MODE 없음, 최소 거래세션, **해당 단계에서 최소 1건 실제 AUTO 체결 증거**가 필요합니다.

정수주 반올림 때문에 목표가 0주라는 이유만으로 다음 자금을 열지 않습니다. 감액은 단계대기 없이 위험축소에 반영합니다.

## 15. 대표 긴급정지와 시스템 임시격리

### 대표 `/halt`

- 신규 BUY 즉시 차단
- durable operator latch ON
- 가능한 BUY 주문 취소 시도
- 위험축소 SELL·주문감시·reconciliation은 계속
- **시스템 자동해제 금지**

`/resume`은 대표 긴급정지/SAFE_MODE 복구의 2단계 확인이며, 첫 JH AUTO 시작승인과 다릅니다. `/resume` 자체가 주문을 제출하는 버튼도 아닙니다.

### 시스템 임시격리

재시작·일시적인 안전상태 갱신 실패 등 시스템이 관리하는 BUY 차단입니다. 이미 launch authorization이 있고 clean reconciliation, 미체결 0, SAFE_MODE 없음이 증명되면 시스템이 정상상태로 복구할 수 있습니다.

## 16. SAFE_MODE

대표 진입조건은 주문결과 `UNKNOWN`, broker/DB 보유 불일치, 열린 주문 불일치, 위험축소 SELL 미완료, 취소결과 미확정, strategy generation/version 불일치, 자동운용 상태 증명불가 등입니다.

SAFE_MODE는 단 한 번의 정상조회만으로 성공으로 추정하지 않습니다.

## 17. 프로세스·재시작 안전

- 같은 live SQLite에 두 runtime이 동시에 붙지 못하도록 OS 파일잠금
- 프로세스 시작마다 startup quarantine
- 미반영 체결 복구 후 reconciliation
- UNKNOWN/open order 추정 성공처리 금지
- 저장 목표수량과 broker-confirmed actual/committed order 차이만 새 BUY gap으로 사용

## 18. 실제 Toss 계좌 운영원칙

- 실제 Toss 보유·미체결·buying power가 최종 외부 사실
- 내부 원장과 계속 대조
- QQQ/TQQQ/SOXL을 Toss 앱에서 JH AUTO와 동시에 수동매매하지 않음
- 비관리 종목은 JH AUTO 자금/HWM에 자동 포함하지 않음
- 계좌조회 실패를 0주/정상으로 해석하지 않음

## 19. Oracle 배포

LIVE-ARMED 배포는 main 필수검증 확인 → 배포 전 저수준 BUY halt ON → 기존 실거래 DB·환경 보존 → JH AUTO/주문/재시작 회귀테스트 → 서비스 교체 → startup quarantine → 계좌 read-only/reconciliation/Telegram smoke 순서로 진행합니다.

배포 workflow는 `/auto start`, `/resume`, 기준자금·비율 변경을 수행하지 않습니다.

배포 직후의 저수준 BUY halt는 **대표 긴급정지와 동일한 의미가 아닙니다.**

- 최초 시작승인 전이면 `launch_authorized=0`이므로 BUY는 계속 차단됩니다.
- 이미 최초 시작승인이 완료된 runtime이면 fresh reconciliation, 미체결 0, SAFE_MODE 없음, 대표 `/halt` latch OFF가 모두 증명된 뒤 JH AUTO가 후속 독립 안전주기에서 **시스템 임시격리만** 자동해제할 수 있습니다.
- 대표 `/halt` latch가 ON이면 배포·재시작 후에도 시스템이 자동해제하면 안 됩니다.

따라서 배포 도구가 BUY를 직접 풀어주는 것이 아니라, 운영 프로그램의 fail-closed 재검증 결과에 따라 기존 승인상태가 안전하게 복구되는 구조입니다.

## 20. 보안/운영 변경 완료 기준

자금/HWM, BUY/SELL/취소/OrderManager, Telegram 관리자 callback, startup/restart, reconciliation/SAFE_MODE, LIVE 배포 workflow 변경은 관련 회귀테스트와 전체 Quality/Security 검증을 요구합니다.

실제 사고나 새 운영리스크를 발견하면 재현 fixture를 남겨 같은 문제가 회귀하지 않게 합니다.
