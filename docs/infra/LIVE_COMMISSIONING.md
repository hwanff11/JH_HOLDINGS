# JDSS Live Commissioning & Incident Runbook

> 이 문서는 **실거래 최초 전환과 운영사고 대응 절차**를 정의합니다. 전략 규칙을 변경하지 않으며, 현재 V3.2.2의 live hard lock을 해제하는 문서가 아닙니다.

## 1. 기본 원칙

1. **dry-run DB를 live DB로 승격하지 않는다.**
   - 모의 주문·모의 체결·모의 원장은 실계좌 원장과 섞지 않는다.
   - live는 별도 신규 SQLite DB에서 시작한다.
2. **실거래 최초 기동은 BUY HALT 상태로 시작한다.**
   - 서비스 기동, Toss read-only 조회, 정합성 확인이 끝난 뒤에만 운영자가 BUY 차단을 해제한다.
3. **위험증가 BUY는 운영자 승인, 위험축소 SELL은 자동** 원칙을 유지한다.
4. **브로커가 Source of Truth인 외부 side effect는 DB rollback으로 되돌렸다고 가정하지 않는다.**
5. 주문 결과가 불명확하면 재주문보다 `UNKNOWN`/SAFE_MODE/운영자 확인을 우선한다.
6. 배포 성공과 live 활성화 승인은 별도 절차다.

## 2. 현재 상태

V3.2.2 production은 다음 다중 잠금을 유지한다.

- `portfolio.live_enabled: false`
- Oracle 표준 `deploy.sh`가 `JDSS_TRADING_MODE=dry_run`을 강제
- `bot.py`가 portfolio live 기동을 거부
- allocation BUY가 live 모드에서 별도 차단

따라서 이 문서와 운영안전 코드가 병합되어도 **실주문은 자동으로 켜지지 않는다.** live 활성화는 별도 PR과 명시적 승인으로만 수행한다.

## 3. Live 전환 전 필수 게이트

다음 항목이 하나라도 실패하면 NO-GO다.

### Source / CI

- `main` 보호 규칙 active
- Quality Gate PASS
- Security Gate PASS
- canonical Backtest PASS
- live 전환 대상 commit SHA 고정
- strategy/config/package 버전 일치

### Oracle / Infra

- SSH host key pinning PASS
- systemd service hardening 유지
- DB/로그 디렉터리 권한 0600/0700 계열 유지
- 서버 시간 오차 120초 미만
- 파일시스템 사용률 90% 미만
- 외부 Oracle health watch 정상

### Toss 실계좌

- 인증 및 read-only smoke PASS
- QQQ/TQQQ/SOXL 기존 보유수량 0
- QQQ/TQQQ/SOXL 미체결 주문 0
- 중복/비정상 보유 row 없음
- USD 매수가능금액이 전략 초기자본 이상

### Live 전용 원장

- 신규 별도 DB
- 과거 `orders/signals/approvals/trades` 0건
- 활성 position/core position 0
- `jdss live-preflight --arm-buy-halt` PASS
- `operator_buy_halt=1` 확인

## 4. Live 최초 기동 순서

권장 순서는 아래와 같다.

```text
1. main/CI 고정
2. dry-run 서비스 정상 종료
3. 실계좌 read-only 사전점검
4. 별도 신규 live DB 생성
5. live-preflight + BUY HALT arm
6. live 전용 배포(preflight 전용 단계)
7. 서비스 기동
8. Toss holdings/open orders/buying power 재조회
9. DB↔Toss reconciliation PASS
10. Telegram /dashboard, /account, /order 확인
11. 운영자가 /resume RESUME_BUYS 실행
12. 이후에도 모든 BUY는 기존 2단계 승인 절차 적용
```

`/resume RESUME_BUYS`는 정합성 오류 또는 미체결 BUY가 있으면 거부되어야 한다.

## 5. Toss write-path commissioning

실제 주문 API의 `POST order -> orderId -> 조회 -> 취소/체결 -> DB 반영` 경로는 read-only smoke와 별개다.

live 잠금 해제 PR에서는 다음 항목을 별도 시험 대상으로 둔다.

- 실제 계정 header/account 선택 검증
- `clientOrderId` 왕복 보존
- 지정가 BUY 제출 응답 검증
- 주문 조회 결과의 symbol/side/qty/clientOrderId 검증
- 취소 요청 후 최종 상태 조회
- timeout/429/5xx 시 자동 재주문 금지
- `UNKNOWN` 발생 시 BUY HALT 또는 SAFE_MODE 유지

토스 공식 주문 계약상 `clientOrderId`를 전달하면 서버는 그 값을 그대로 반환하며, 이 값은 10분간 멱등성 키로 사용된다. JDSS는 모든 주문에 `clientOrderId`를 전달하므로 다음을 강제한다.

- 주문 생성 200 응답에서 `clientOrderId`가 누락되거나 요청값과 다르면 성공으로 확정하지 않는다.
- 누락된 응답 식별자를 로컬 요청값으로 임의 보충하지 않는다.
- 식별자를 증명할 수 없는 주문은 `UNKNOWN`으로 남기고 broker 주문내역·보유수량을 조회한 뒤 reconciliation한다.
- 10분 멱등성 유효시간을 지난 뒤 동일 `clientOrderId`를 broker에 맹목적으로 재전송하지 않는다. 장기 중복 방지는 live DB의 주문 원장과 broker reconciliation을 기준으로 한다.

실계좌 canary 주문을 사용할 경우 **자동으로 실행하지 않는다.** 주문 종목·수량·가격·취소 계획을 운영자가 별도 승인한 경우에만 수행한다.

## 6. Operator BUY Halt

### 긴급정지

Telegram:

```text
/halt
```

동작:

- 신규 BUY 브로커 제출 차단
- 활성 BUY 승인 폐기
- 미체결 BUY 취소 요청
- 취소 결과가 불확실한 주문은 운영자 확인 대상으로 유지
- SELL 허용
- OrderMonitor 허용
- Reconciliation 허용

`/halt`는 서비스를 종료하는 명령이 아니다. 위험축소와 상태 확인이 계속되어야 하므로 프로세스는 살아 있어야 한다.

### 차단 해제

Telegram:

```text
/resume RESUME_BUYS
```

해제 조건:

- broker/DB reconciliation PASS
- 미체결 BUY 0
- 운영자 명시 확인문구 일치

SAFE_MODE가 별도로 남아 있다면 BUY HALT를 해제해도 기존 SAFE_MODE 게이트가 계속 우선한다.

## 7. 배포/rollback 원칙

### Dry-run

현재 표준 `deploy.sh`의 DB snapshot + 코드/DB rollback을 사용한다.

### Live

live에서 외부 주문 side effect가 발생할 수 있는 시점 이후에는 과거 DB snapshot을 단순 복원하지 않는다.

권장 live 배포:

```text
BUY HALT
-> 미체결 주문 0 확인
-> reconciliation PASS
-> 서비스 stop
-> DB 일관 snapshot(감사/재해복구용)
-> 새 코드 release 전환
-> 서비스 start(BUY HALT 유지)
-> broker를 기준으로 reconciliation
-> smoke PASS
-> 운영자 수동 resume
```

배포 실패 시:

- 코드 release는 직전 검증 버전으로 되돌릴 수 있다.
- **DB는 주문 side effect 발생 가능성이 있으면 자동 과거복원 금지.**
- Toss holdings/orders 조회 후 현 상태를 원장에 reconcile하는 절차가 우선이다.

## 8. 외부 Health Watch

`.github/workflows/oracle-health-watch.yml`은 매시간 Oracle 바깥에서 다음을 확인한다.

- SSH 연결 및 pinned host trust
- systemd enabled/active
- current release/venv 존재
- SQLite `PRAGMA quick_check`
- 서버 시간 오차
- 디스크 사용률
- config validation
- Toss read-only smoke

실패 시 `[oracle-health]` GitHub Issue를 생성하거나 기존 incident에 추가 기록한다. 이후 정상 복구가 확인되면 해당 incident를 닫는다.

## 9. 사고 유형별 대응

### 주문 결과 UNKNOWN

1. 신규 BUY 중단
2. 같은 주문을 임의 재전송하지 않음
3. Toss 주문내역/보유수량 확인
4. reconciliation
5. 실제 체결분만 원장 반영
6. 상태 확정 후 수동 재개

### 부분체결 BUY

- 체결분만 반영
- 잔량 자동 재주문 금지
- 최신 조건으로 재승인

### 부분체결/실패 SELL

- SAFE_MODE
- 신규 BUY 금지
- 잔량 및 실계좌 보유량 확인
- 위험축소 완료 후 정합성 검증

### Oracle 장애

- GitHub health incident 확인
- Toss 앱에서 미체결/체결 상태를 우선 확인
- 서버 복구 후 BUY HALT 상태 유지
- DB quick_check + reconciliation 후 재개

### Telegram 계정/기기 이상

- Telegram bot token 회전 검토
- `TELEGRAM_ALLOWED_CHAT_IDS` 확인
- 서버에서 BUY HALT 직접 설정
- 승인 토큰은 TTL 만료 또는 전체 취소 처리

## 10. 최종 Go-Live 승인 기준

실거래 승인 시점에는 별도 체크리스트에 아래 결과를 모두 기록한다.

- 대상 commit SHA
- CI 3종 결과
- Oracle health 결과
- live DB 경로 및 fresh ledger 결과
- Toss account preflight 결과
- BUY HALT arm 확인
- 서비스 시작 후 reconciliation 결과
- Telegram 통신 확인
- write-path commissioning 결과(수행 시)
- 최종 운영자 승인 시각

모든 항목이 PASS가 아니면 live BUY를 열지 않는다.
