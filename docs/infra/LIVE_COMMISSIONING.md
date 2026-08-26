# JDSS LIVE-ARMED Commissioning & Incident Runbook

> 이 문서는 JDSS V3.2.2를 **실계좌에 연결하되 신규 BUY는 잠근 상태(LIVE-ARMED)** 로 기동하는 절차와 사고 대응 원칙을 정의합니다. 전략 수학·배분·HWM75·50→75→100 최초진입 규칙은 변경하지 않습니다.

## 1. 운영 상태 정의

- **DRY-RUN**: 모의 원장/모의 주문. 실제 Toss 주문 제출 불가.
- **LIVE-ARMED**: 실제 Toss 계좌와 별도 live 원장을 사용하지만 `operator_buy_halt=1`. 신규 BUY 제출 불가.
- **LIVE-BUY-ENABLED**: LIVE-ARMED 상태에서 운영자가 Telegram으로 BUY 잠금을 명시적으로 해제한 상태. 그래도 각 BUY는 기존 2단계 승인 절차를 추가로 통과해야 함.

`strategy.yaml`의 `portfolio.live_enabled: false`는 그대로 유지합니다. 일반/default 경로의 하드락은 닫힌 채로 두고, live는 `jdss-bot -> jd_holdings.runtime -> jd_holdings.live_bot`의 별도 commissioning 경로로만 진입합니다.

## 2. 절대 원칙

1. **dry-run DB를 live DB로 재사용하지 않는다.**
2. 최초 live는 항상 **fresh 별도 `jdss-live.db`** 에서 시작한다.
3. live 프로세스는 시작/재시작할 때마다 BUY HALT를 자동 재설정한다.
4. 위험증가 BUY는 운영자 승인, 위험축소 SELL은 자동 원칙을 유지한다.
5. 주문 결과가 불명확하면 blind retry보다 `UNKNOWN`/SAFE_MODE/reconciliation을 우선한다.
6. broker side effect 가능 이후에는 과거 DB snapshot을 맹목적으로 복원하지 않는다.
7. 배포 승인과 BUY 잠금 해제 승인은 서로 다른 행위다.

## 3. 최초 LIVE-ARMED 전환 게이트

하나라도 실패하면 전환하지 않습니다.

### Source / CI

- 보호된 `main`의 정확한 최신 SHA
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

### Toss 실계좌

최초 commissioning 시 `RealAccountPreflight`가 다음을 확인합니다.

- QQQ/TQQQ/SOXL 기존 관리대상 보유 0
- 관리대상 미체결 주문 0
- USD 매수가능금액이 전략 초기자본 이상

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
- 취소 결과 불명확 주문은 임의 재주문하지 않음
- SELL 허용
- OrderMonitor 허용
- reconciliation 허용

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
- timeout/429/5xx/식별 불능은 blind retry하지 않음
- 증명할 수 없는 결과는 `UNKNOWN`으로 유지하고 broker 조회 + reconciliation
- 부분체결은 실제 체결분만 원장 반영
- 잔여 BUY는 새로운 승인 필요

실계좌 canary 주문은 자동으로 실행하지 않습니다.

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

## 10. 외부 Health Watch

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

## 11. 사고 대응

### 주문 상태 UNKNOWN

1. `/halt`
2. 동일 주문 재전송 금지
3. Toss 주문내역/보유수량 확인
4. reconciliation
5. 실제 체결분만 반영
6. 상태 증명 후 필요 시 수동 재개

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

## 12. BUY 해제 전 최종 체크리스트

- 대상 main SHA 확인
- CI 3종 PASS
- Oracle health PASS
- `JDSS_TRADING_MODE=live`
- live DB 경로 확인
- `live_commissioned=1`
- `operator_buy_halt=1`
- broker holdings/open orders 조회 PASS
- reconciliation PASS
- Telegram `/ping`, `/dashboard`, `/account`, `/order` 확인
- 실제 매수 신호와 주문 수량/지정가 검토
- 운영자가 BUY 잠금 해제를 명시적으로 수행

위 조건 중 하나라도 불명확하면 BUY를 열지 않습니다.
