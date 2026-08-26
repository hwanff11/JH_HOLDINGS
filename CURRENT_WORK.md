# JH_HOLDINGS Current Work

> **현재 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 규범 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 운영 방법은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md), 실거래 최초 전환·사고 대응은 [`docs/infra/LIVE_COMMISSIONING.md`](docs/infra/LIVE_COMMISSIONING.md)를 따릅니다.

## 1. Production 상태

- 공식 릴리즈: **v3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- GitHub `main`: 보호 브랜치, PR + 필수 CI 경유
- 최신 runtime 기능 revision: **`b4e4dd511eaabadad9b8a0aa7b12c9e7fcb8cedb`**
- Oracle runtime 기능 revision: **`b4e4dd511eaabadad9b8a0aa7b12c9e7fcb8cedb`**
- Oracle 서비스: **active**
- 현재 운용 모드: **forced dry-run**
- `portfolio.live_enabled`: **false**
- 실제 Toss 주문: **LOCKED / 미활성화**
- SGOV 자동운용: **OFF**
- 운영자 긴급 BUY 차단: **`/halt` 지원**
- BUY 재개: **reconciliation PASS + 미체결 BUY 0 + 운영자 명시적 `/resume`**
- 외부 Oracle health watch: **매시간 + owner-only on-demand**

runtime 영향이 없는 문서·CI·운영 workflow commit이 `main`에 추가될 수 있으므로 GitHub HEAD와 Oracle 기능 revision 문자열은 항상 같을 필요가 없습니다. 현재 Oracle에는 최신 runtime 기능 revision이 forced dry-run으로 배포되어 있습니다.

## 2. 최근 완료

### Toss / live commissioning hardening

실거래 진입 직전의 인증·주문식별·commissioning 경계를 다음과 같이 보강했습니다.

- 주문 생성 응답의 `clientOrderId` 누락/불일치를 성공으로 간주하지 않고 `UNKNOWN` + reconciliation 우선
- Toss 인증 요청은 commissioning 중 직렬화하고 인증에 한해서만 제한적 재시도
- 실제 주문 POST는 timeout/429/5xx라도 blind retry하지 않음
- standalone `live-preflight` 실행 전에 Oracle의 protected `shared/.env`를 로드하도록 수정
- secret 값은 공개 Actions 로그에 출력하지 않음
- dry-run DB를 live DB로 승격하지 않고 **fresh 별도 live DB**만 허용
- live 프로세스 시작/재시작 시 BUY HALT 자동 재설정

관련 merge:

- PR #225 `Serialize Toss auth during live commissioning` → `9befeb0fc65b511233b3c658f7fa9bcae912b7c8`
- PR #227 `Load Toss credentials for standalone live preflight` → `b4e4dd511eaabadad9b8a0aa7b12c9e7fcb8cedb`

### 실제 계좌 read-only commissioning 검증

owner-only LIVE-ARMED commissioning을 최신 runtime revision으로 실제 실행했습니다.

- live commissioning gate: Ruff + 집중테스트 **77건 PASS**
- pinned SSH trust PASS
- 최신 main의 forced dry-run Oracle 배포 PASS
- Toss 인증/시세 smoke PASS
- Toss 계좌 목록·보유·미체결·USD buying power **read-only API PASS**
- standalone `live-preflight`의 Oracle `.env` 로딩 PASS
- 계좌정보·잔고금액은 공개 로그에서 비공개 유지
- 실제 주문/canary **0건**

commissioning run **32948894181**은 현재 실계좌에 관리대상 종목 **SOXL 기존 보유가 존재**하여 `REAL_ACCOUNT_MANAGED_SYMBOL_PRESENT:SOXL`로 **의도대로 fail-closed** 중단됐습니다. live 원장은 활성화되지 않았고 BUY 잠금도 해제되지 않았습니다.

### 실패 후 dry-run 복구 독립 검증

commissioning 중단 뒤 Oracle이 안전한 기존 상태로 돌아왔는지 외부 Health Watch를 별도로 실행했습니다.

- SSH / pinned trust PASS
- systemd PASS
- SQLite quick check PASS
- clock / disk PASS
- strategy/config PASS
- Toss read-only PASS
- on-demand trigger Issue 자동 PASS comment + close PASS

health run: **32950205268**  
trigger Issue: **#229**

현재 Oracle은 정상 **forced dry-run** 상태입니다.

## 3. 실거래 계좌 운영 계약

실제 JDSS 운용계좌는 **전용·청정 계좌**로 사용합니다.

최초 LIVE-ARMED commissioning 직전 반드시:

- QQQ 기존 보유 **0**
- TQQQ 기존 보유 **0**
- SOXL 기존 보유 **0**
- QQQ/TQQQ/SOXL 미체결 주문 **0**
- 필요한 USD buying power 충족

이어야 합니다.

현재 다른 목적으로 보유한 관리대상 종목은 실제 운용 전에 **다른 증권계좌로 이동**하고, JDSS 계좌에서는 기존 개인 보유분과 JDSS 관리분을 공존시키지 않는 것을 production 원칙으로 합니다.

이에 따라 `feat/live-external-holdings-baseline`에서 연구한 **기존 보유분 baseline 공존 기능은 production에 병합하지 않습니다.** 전용계좌 방식이 회계·자동 SELL·reconciliation 경계를 더 단순하고 안전하게 유지합니다.

commissioning 이후에도 JDSS 계좌의 QQQ/TQQQ/SOXL을 수동으로 별도 매매하지 않습니다. broker↔DB 수량이 달라지면 신규 BUY보다 SAFE_MODE/reconciliation을 우선합니다.

## 4. 실거래 준비 상태

현재 평가는 다음과 같습니다.

- **Oracle dry-run production:** GO
- **Toss account read-only API:** GO
- **live commissioning 코드/절차:** 준비됨
- **실계좌 최초 commissioning:** 계좌 청정화 전까지 BLOCKED
- **LIVE-ARMED:** 아직 아님
- **LIVE BUY:** LOCKED
- **실제 Toss 주문:** 아직 0건

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
4. 실제 Toss write path(`POST order → orderId/clientOrderId → 주문조회 → 부분/완전체결·취소 → 원장반영`)를 별도 commissioning으로 검증
5. timeout/429/5xx/UNKNOWN에서 동일 주문을 blind resubmit하지 않는 계약 유지
6. 모든 Go-Live 체크리스트 PASS 뒤에만 **별도 명시적 승인**으로 BUY 잠금을 해제

실계좌 canary나 최초 BUY는 자동 실행하지 않습니다.

## 6. 운영상 유지 원칙

- 위험축소 SELL은 자동, 위험증가 BUY는 운영자 승인
- 긴급 이상 시 `/halt`로 BUY를 우선 차단하고 SELL·monitor·reconciliation은 유지
- `N건 순차 실행`은 원자적 basket이 아니며 일부 주문만 제출될 수 있음
- 이미 제출된 주문을 임의 역매매하여 자동 rollback하지 않음
- dry-run 원장과 live 원장을 절대 혼용하지 않음
- UNKNOWN·원장 불일치·미완료 위험축소는 신규 BUY보다 SAFE_MODE 우선
- broker side effect 가능 이후 과거 DB snapshot을 현재 broker 상태 위에 맹목 복원하지 않음
- Oracle 배포 승인, LIVE-ARMED commissioning, BUY 잠금 해제 승인을 서로 다른 행위로 취급
- QLD/SSO/v3.3 연구는 production V3.2.2와 계속 분리
