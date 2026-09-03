#

## JH AUTO 최초 활성화 단계

기존 LIVE commissioning은 실계좌 연결 자격을 증명하고, JH AUTO 시작승인은 자동 위험증가 BUY 권한을 별도로 여는 절차입니다. 둘은 같은 사건이 아닙니다.

```text
LIVE commissioned
→ JH AUTO 코드 배포
→ BUY halt / startup quarantine
→ 기준자금 설정
→ 자동운용비율 설정
→ 운영자 최초 시작 2단계 확인
→ 시작 callback에서는 주문 0건
→ 다음 독립 안전주기 재대조
→ 미국 정규장 + 모든 안전조건 통과 시 첫 AUTO BUY 가능
```

최초 활성화 전 기존 관리 포지션·미체결·계좌 불일치가 있으면 시작을 거부합니다. 배포나 서버 재시작은 기존 운영자 시작승인을 새로 만들지 않으며, 운영자 `/halt` latch는 자동 복구 대상이 아닙니다.
 JDSS 실거래 준비 전환·사고 대응 가이드

> 이 문서는 JDSS V3.2.2를 **실계좌에 연결하되 신규 매수는 잠근 상태**로 기동하는 절차와 사고 대응 원칙을 정의합니다. 전략 수학·목표비중·HWM75·50→75→100 첫 투자 규칙은 변경하지 않습니다.

## 1. 운영 상태 정의

- **모의운용(DRY-RUN)**: 모의 원장과 모의 주문 사용. 실제 Toss 주문 제출 불가.
- **실계좌 연결·매수 잠금(LIVE-ARMED)**: 실제 Toss 계좌와 별도 실거래 원장을 사용하지만 `operator_buy_halt=1`. 신규 매수 제출 불가.
- **실매수 허용(LIVE-BUY-ENABLED)**: 운영자가 Telegram으로 신규 매수 잠금을 명시적으로 해제한 상태. 그래도 각 매수는 기존 2단계 승인을 추가로 통과해야 함.

`strategy.yaml`의 `portfolio.live_enabled: false`는 그대로 유지합니다. 일반 경로의 오작동 방지 잠금은 닫힌 채로 두고, 실거래는 `jdss-bot -> jd_holdings.runtime -> jd_holdings.live_bot`의 별도 준비 전환 경로로만 진입합니다.

## 2. 절대 원칙

1. **모의운용 DB를 실거래 DB로 재사용하지 않는다.**
2. 최초 실거래는 항상 **새 별도 `jdss-live.db`** 에서 시작한다.
3. 실거래 프로그램은 시작·재시작할 때마다 신규 매수 잠금을 자동으로 다시 건다.
4. 위험을 늘리는 매수는 운영자 승인, 위험을 줄이는 매도는 자동 원칙을 유지한다.
5. 주문 결과가 불명확하면 확인 없이 재전송하지 않고 `UNKNOWN(결과 확인 필요)`·안전정지·계좌 대조를 우선한다.
6. 실제 주문 가능성이 생긴 뒤에는 과거 DB 백업을 맹목적으로 복원하지 않는다.
7. 배포 승인과 신규 매수 잠금 해제 승인은 서로 다른 행위다.
8. 최초 commissioning은 **JDSS 관리종목(QQQ/TQQQ/SOXL)이 청정한 실계좌**에서만 수행한다. 기존 개인 QQQ/TQQQ/SOXL 보유분은 자동 입양하거나 같은 계좌에서 공존시키지 않는다. **QQQ/TQQQ/SOXL 외 비관리 종목은 같은 계좌에 보유할 수 있으며 JDSS 원장·HWM75·자동 SELL·reconciliation 대상에서 제외한다.**
9. **취소 요청 접수는 원주문 취소 완료가 아니다.** 원래 broker `orderId`의 상태가 `CANCELED`로 확인되기 전에는 취소 결과를 확정하지 않는다.

## 3. 최초 실계좌 연결 전 필수검사

하나라도 실패하면 전환하지 않습니다.

### Source / CI

- 보호된 `main`의 정확한 최신 runtime SHA
- Quality Gate PASS
- Security Gate PASS
- canonical Backtest PASS
- Ruff PASS
- live commissioning/runtime safety 집중테스트 PASS

### Oracle / Infra

- pinned SSH host key PASS
- systemd active/enabled
- SQLite quick check PASS
- 서버 clock drift < 120초
- 파일시스템 사용률 < 90%
- Toss read-only smoke PASS

### Toss 실계좌 — 관리종목 청정계좌 계약

최초 commissioning 시 `RealAccountPreflight`가 다음을 확인합니다.

- QQQ 기존 보유 0
- TQQQ 기존 보유 0
- SOXL 기존 보유 0
- QQQ/TQQQ/SOXL 관리대상 미체결 주문 0
- USD 매수가능금액이 전략 초기자본 이상

기존에 개인적으로 보유하던 QQQ/TQQQ/SOXL이 있다면 **commissioning 전에 다른 증권계좌로 이동**하는 것을 production 운영방식으로 합니다. 기존 보유량을 JDSS 원장에 자동 등록하거나 baseline으로 차감하여 공존시키지 않습니다.

QQQ/TQQQ/SOXL 외 종목은 같은 Toss 계좌에 보유할 수 있습니다. 이 비관리 종목은:

- JDSS managed equity/HWM75 계산에 포함하지 않음
- JDSS 목표수량·리밸런싱·자동 위험축소 SELL 대상이 아님
- broker↔DB reconciliation 수량 비교 대상이 아님
- 비관리 종목의 평가손익이 JDSS 위험예산을 늘리거나 줄이지 않음

Toss의 USD `cashBuyingPower`는 계좌 공용 현금 기반 매수가능금액이므로 **JDSS 자산으로 합산하지 않고 실제 주문 가능한 유동성 상한으로만 사용**합니다. JDSS BUY 가능액은 `JDSS 원장 가용현금`, `HWM75 위험예산`, `broker cashBuyingPower` 중 가장 작은 값으로 제한합니다. 따라서 개인 현금이 많아도 JDSS가 HWM75 한도를 넘어 과대매수하지 않습니다. 반대로 비관리 종목 매수나 개인 주문이 계좌 현금을 사용해 `cashBuyingPower`가 줄면 JDSS BUY는 줄거나 차단될 수 있습니다.

이 원칙을 두는 이유는 다음과 같습니다.

- JDSS 자동 위험축소 SELL의 소유권 경계를 QQQ/TQQQ/SOXL로 명확하게 유지
- broker holdings와 JDSS 원장의 reconciliation을 관리종목으로 한정
- 개인 비관리 종목의 평균단가·원가·평가손익을 JDSS HWM75 회계에 잘못 포함하는 위험 제거
- 계좌의 추가 현금/비관리 자산 때문에 JDSS 위험예산이 부풀려지는 것을 방지

따라서 preflight가 `REAL_ACCOUNT_MANAGED_SYMBOL_PRESENT:<SYMBOL>`을 반환하면 **우회하지 않고 commissioning을 중단**합니다. 기존 관리종목 보유분 이동과 관리종목 미체결 주문 정리가 완료된 뒤 fresh live DB로 다시 시작합니다.

commissioning 이후에도 동일 Toss 계좌의 QQQ/TQQQ/SOXL은 JDSS 외부에서 수동으로 별도 매매하지 않습니다. 불가피한 수동 조치가 발생하면 먼저 `/halt`하고 broker/DB reconciliation을 수행합니다. 비관리 종목의 개인 매매는 허용하지만 JDSS 주문 검토·실행 시점에는 USD `cashBuyingPower`가 JDSS 주문에 충분한지 다시 확인합니다.

### Live 원장

- 과거 `orders/signals/approvals/trades` 0건
- 활성 position/core position 0
- `jdss live-preflight --arm-buy-halt` PASS
- `live_commissioned=1`
- `operator_buy_halt=1`

## 4. 실제 전환 순서

owner-only ChatOps 제목:

```text
[commission-oracle-live-armed] <설명>
```

`.github/workflows/commission-oracle-live-armed.yml`은 다음 순서로 동작합니다.

```text
정확한 latest main checkout
→ live 집중테스트/Ruff/config 검증
→ 최신 main을 기존 forced dry-run 방식으로 먼저 배포·smoke
→ fresh candidate live DB 생성
→ 실계좌 read-only preflight
→ commissioning marker + BUY HALT arm
→ dry-run 서비스 stop
→ candidate DB를 jdss-live.db로 승격
→ installed systemd unit의 DB path만 live DB로 전환
→ JDSS_TRADING_MODE=live + 정확한 live confirmation 설정
→ live 서비스 start
→ 시작 즉시 BUY HALT 자동 재설정
→ broker↔DB reconciliation
→ live-release-check
→ Toss read-only smoke
→ BUY HALT=1 재확인
```

이 과정은 실제 BUY 주문을 자동 제출하지 않습니다.

실계좌 preflight가 기존 관리대상 보유나 관리대상 미체결 주문을 발견하면 candidate live DB를 production live 원장으로 승격하지 않고 fail-closed 종료하며, 기존 forced dry-run 환경으로 복구합니다. 비관리 종목 보유나 비관리 종목 미체결 주문만으로는 commissioning을 차단하지 않습니다. 단, 계좌 공용 USD `cashBuyingPower`가 전략 초기자본보다 작으면 commissioning은 차단합니다.

## 5. Systemd / DB 경계

repository의 기본 systemd template은 계속 dry-run DB를 가리킵니다.

```text
shared/data/jdss.db
```

최초 LIVE-ARMED commissioning 시에만 **Oracle에 설치된 unit**의 `JDSS_DB_PATH`를 아래 live DB로 바꿉니다.

```text
shared/data/jdss-live.db
```

repository template 자체를 live 기본값으로 바꾸지 않으므로, 일반 배포가 실수로 live 원장을 기본값으로 삼지 않습니다.

commissioning rollback을 위한 `.env`/service-unit 임시 복사본은 `/tmp`에 0600으로 만들고 성공·실패 후 삭제합니다. Toss/Telegram 비밀값이 persistent backup 폴더에 복제되지 않도록 합니다.

## 6. 프로세스 재시작 규칙

LIVE 상태에서 process/systemd가 재시작되면 `arm_live_startup_buy_halt()`가 broker 객체 생성보다 먼저 실행됩니다.

따라서 이전에 BUY를 풀어둔 상태였더라도 재시작 이후에는:

```text
실계좌 연결 = LIVE
신규 BUY = LOCKED
SELL / monitor / reconciliation = 계속 가능
```

운영자가 다시 명시적으로 BUY 잠금을 해제해야 합니다.

주간 runtime verifier는 LIVE 상태에서 자동 systemd restart를 수행하지 않습니다. LIVE에서는 read-only 검증만 수행합니다.

## 7. Telegram BUY 긴급정지 / 재개

### 긴급정지

```text
/halt
```

동작:

- 신규 BUY final broker boundary 차단
- 활성 BUY 승인 취소
- 미체결 BUY 취소 시도
- **취소 API 요청 성공만으로 취소 완료로 간주하지 않음**
- 취소 요청 후 원래 broker `orderId`를 다시 조회
- 동일 원주문이 `CANCELED`일 때만 취소 완료로 확정
- `PENDING_CANCEL`, 취소 중 동시체결, 조회 실패, 다른 `orderId` 응답은 모두 불확실 상태로 유지
- 취소 결과 불명확 주문은 임의 재주문·역매매하지 않고 OrderMonitor + reconciliation으로 최종상태 확인
- SELL 허용
- OrderMonitor 허용
- reconciliation 허용

따라서 `/halt` 응답의 `uncertain_order_ids`가 비어 있지 않으면 긴급 BUY 차단 자체는 활성화되어 있어도 기존 주문의 정리가 끝난 것으로 보지 않습니다. 실제 broker 주문 상태가 증명될 때까지 BUY를 재개하지 않습니다.

### BUY 재개

기본 UI 경로:

```text
/resume
→ 🔓 BUY 잠금 해제 검토
→ ✅ 정말 BUY 잠금 해제
```

비상/수동 명령:

```text
/resume RESUME_BUYS
```

실제 해제 전에 반드시:

- broker↔DB reconciliation PASS
- 미체결 BUY 0
- BUY execution lock 내부 최종 재확인

을 통과해야 합니다.

BUY 잠금이 풀려도 주문이 자동 발생하지 않습니다. 각 BUY는 기존 review → execution의 2단계 승인을 다시 거칩니다.

## 8. 실제 주문 write-path 계약

실제 BUY를 처음 승인할 때는 다음 계약이 적용됩니다.

- 정확한 live confirmation이 OrderManager에서 재확인됨
- BUY HALT가 OrderManager final broker boundary에서 재확인됨
- DB order reservation 후 broker submit
- `clientOrderId` 누락/불일치는 성공으로 간주하지 않음
- Toss `clientOrderId`의 broker 멱등성만 믿고 자동 재전송하지 않으며, JDSS 자체 원장과 reconciliation을 장기 중복방지 기준으로 유지
- timeout/429/5xx/식별 불능은 blind retry하지 않음
- **401에서도 write POST를 자동 replay하지 않음**: 토큰은 갱신할 수 있지만 주문 생성/취소 요청 자체는 현재 호출에서 다시 보내지 않음
- 401 이후 다시 주문하려면 새로운 운영자 실행 흐름에서 원장/신호 상태를 확인한 뒤 명시적으로 진행
- GET/HEAD/OPTIONS 같은 read-only 요청만 401 토큰 갱신 뒤 제한적으로 자동 재요청 가능
- 미국주식 LIMIT 가격은 broker adapter에서 공식 자릿수 규격을 검증: **$1 이상 소수 2자리, $1 미만 소수 4자리**
- 주문취소 HTTP 성공 응답도 별도 cancellation operation `orderId`가 없거나 원주문 ID와 같으면 성공으로 확정하지 않음
- cancellation operation이 정상 접수됐더라도 **원주문의 동일 broker `orderId`를 재조회해 `CANCELED`를 확인하기 전에는 취소 완료로 확정하지 않음**
- 증명할 수 없는 결과는 `UNKNOWN`/uncertain으로 유지하고 broker 조회 + reconciliation
- 부분체결은 실제 체결분만 원장 반영
- 잔여 BUY는 새로운 승인 필요

실계좌 canary 주문은 자동으로 실행하지 않습니다. 위 계약은 테스트·dry-run으로 먼저 검증하고, 실제 broker write-path의 최종 확인은 **QQQ/TQQQ/SOXL 관리종목 청정 상태**의 LIVE-ARMED 계좌에서 별도 명시적 commissioning으로 수행합니다. 비관리 종목의 존재는 이 write-path 소유권 경계를 변경하지 않습니다.

## 9. 배포 / rollback 원칙

### LIVE에서 코드 배포 전

```text
/halt
→ 미체결 주문 0
→ reconciliation PASS
→ service stop
→ 감사/재해복구용 일관 snapshot
→ 코드 release 전환
→ service start
→ 자동 BUY HALT 확인
→ reconciliation
→ smoke
→ 필요할 때만 운영자가 BUY 재개
```

broker side effect 가능 이후에는 과거 DB를 자동 복원해 현재 broker 상태를 덮어쓰지 않습니다. broker holdings/orders가 Source of Truth이며 reconciliation이 우선입니다.

## 10. 정기 실거래 운영 프로그램 갱신

최초 실거래 전환이 끝난 뒤 운영 프로그램만 갱신할 때는 `commission_live_armed.sh`를 다시 실행하지 않습니다. 기존 실거래 DB가 존재하므로 최초 전환 절차는 의도적으로 중단됩니다. 정기 갱신은 [`DEPLOYMENT.md`](DEPLOYMENT.md)의 **기존 실거래 운영 프로그램 갱신** 절차를 따릅니다.

정기 갱신 전에는 `/halt` 상태, 미체결 주문 0건, 활성 승인 0건, 계좌·원장 대조 정상을 확인합니다. 배포는 기존 실거래 DB와 환경설정을 보존하며, 새 서비스 시작 시 신규 매수 잠금을 다시 설정합니다.

## 11. 외부 Health Watch

`.github/workflows/oracle-health-watch.yml`은 DRY-RUN/LIVE 모드를 구분해서 실제 사용 DB를 검사합니다.

- SSH / pinned trust
- systemd enabled/active
- current release / venv
- SQLite `PRAGMA quick_check`
- clock drift
- disk usage
- config validation
- Toss read-only smoke
- LIVE일 경우 `live_commissioned=1`
- BUY HALT 상태 표시

실패 시 `[oracle-health]` incident를 생성/갱신하고 복구 확인 시 닫습니다.

## 12. 사고 대응

### 주문 상태 UNKNOWN

1. `/halt`
2. 동일 주문 재전송 금지
3. Toss 주문내역/보유수량 확인
4. reconciliation
5. 실제 체결분만 반영
6. 상태 증명 후 필요 시 수동 재개

### 취소 요청 결과가 불명확한 BUY

1. BUY HALT 유지
2. 같은 취소 요청이나 원주문을 blind retry하지 않음
3. 원래 broker `orderId` 상태 재조회
4. `PENDING_CANCEL`이면 monitor/reconciliation으로 최종상태 추적
5. `FILLED`/부분체결이면 실제 체결분부터 원장에 반영
6. `CANCELED`와 broker↔DB 정합성이 증명된 뒤에만 재개 검토

### 부분체결 BUY

- 체결분만 반영
- 잔량 자동 재주문 금지
- 최신 가격/수량으로 새 승인

### SELL 실패/부분체결

- SAFE_MODE 우선
- 신규 BUY 금지
- broker holdings/open orders 확인
- 위험축소 완료 후 reconciliation

### Oracle 장애

- GitHub health incident 확인
- Toss 앱에서 실제 체결/미체결 상태 우선 확인
- 서버 복구 후 BUY HALT 유지
- DB quick check + reconciliation 후 재개

### Telegram 보안 이상

- bot token 회전
- `TELEGRAM_ALLOWED_CHAT_IDS` 재검증
- 서버 측 BUY HALT 유지
- 활성 승인 취소/TTL 만료 확인

### JDSS 관리종목에 외부/수동 거래가 생긴 경우

1. `/halt`
2. QQQ/TQQQ/SOXL의 실제 보유·미체결 확인
3. 임의로 DB 수량을 맞추거나 주문을 재전송하지 않음
4. broker/DB reconciliation
5. 원인과 실제 소유수량이 증명될 때까지 BUY 재개 금지

비관리 종목의 개인 거래는 이 사고절차 대상이 아닙니다. 다만 비관리 종목 주문으로 USD `cashBuyingPower`가 줄어 JDSS 주문이 거부되면 개인 주문/현금 사용량을 확인하고, JDSS 주문을 임의 축소·재전송하기보다 최신 승인 흐름에서 다시 검토합니다.

## 13. BUY 해제 전 최종 체크리스트

- 대상 runtime SHA 확인
- CI 3종 PASS
- Oracle health PASS
- 최초 commissioning이라면 QQQ/TQQQ/SOXL 기존 보유 0
- QQQ/TQQQ/SOXL 외부 미체결 주문 0
- 비관리 종목은 존재 가능하며 JDSS 원장/HWM/SELL/reconciliation 대상에서 제외됨을 확인
- USD `cashBuyingPower`가 필요한 JDSS 주문/초기자본에 충분함을 확인
- `JDSS_TRADING_MODE=live`
- live DB 경로 확인
- `live_commissioned=1`
- `operator_buy_halt=1`
- `/halt` 이후 uncertain cancellation 0 또는 broker 상태 증명 완료
- broker holdings/open orders 조회 PASS
- reconciliation PASS
- Telegram `/ping`, `/dashboard`, `/account`, `/order` 확인
- 실제 매수 신호와 주문 수량/지정가 검토
- 운영자가 BUY 잠금 해제를 명시적으로 수행

위 조건 중 하나라도 불명확하면 BUY를 열지 않습니다.
