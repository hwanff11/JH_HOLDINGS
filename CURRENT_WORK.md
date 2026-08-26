# JH_HOLDINGS Current Work

> **현재 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 규범 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 운영 방법은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md), 실거래 최초 전환·사고 대응은 [`docs/infra/LIVE_COMMISSIONING.md`](docs/infra/LIVE_COMMISSIONING.md)를 따릅니다. 완료된 상세 작업은 이 파일에 누적하지 않습니다.

## 1. Production 상태

- 공식 릴리즈: **v3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- GitHub `main`: 보호 브랜치, PR + 필수 CI 경유
- Oracle runtime 기능 revision: **`29b198321a69050651a4ed12242a4eca19d22d13`**
- Oracle 서비스: **active**
- 운용 모드: **forced dry-run**
- `portfolio.live_enabled`: **false**
- `JDSS_LIVE_CONFIRMATION`: **empty**
- 실제 Toss 주문: **LOCKED OFF**
- SGOV 자동운용: **OFF**
- 운영자 긴급 BUY 차단: **`/halt` 지원**
- BUY 차단 해제: **reconciliation PASS + 미체결 BUY 0 + `/resume RESUME_BUYS`**
- 외부 Oracle health watch: **매시간 실행 + on-demand ChatOps 지원**

`main`에 Oracle runtime 영향이 없는 문서·CI·운영 workflow commit이 추가될 수 있으므로 GitHub HEAD와 Oracle 기능 revision이 항상 같은 문자열일 필요는 없습니다. **runtime 코드·설정이 달라졌는데 Oracle revision이 뒤처진 경우만 배포 불일치**로 봅니다.

## 2. 최근 완료

### 실거래 전 운영안전 hardening

PR #209를 통해 전략 수학이나 매매비중을 변경하지 않고 실거래 운영사고의 확산 방지 계층을 추가했습니다.

- 모든 신규 BUY의 최종 broker 경계에 운영자 긴급정지 게이트 추가
- `/halt`와 BUY 제출을 동일 process lock으로 직렬화해 경합 시 신규 BUY fail-closed
- `/halt` 시 활성 BUY 승인 폐기, 미체결 BUY 취소 시도, 불확실 주문은 임의 재주문하지 않고 운영자 확인 대상으로 유지
- SELL·OrderMonitor·reconciliation은 긴급정지 중에도 계속 허용
- `/resume RESUME_BUYS`는 broker/DB reconciliation PASS와 미체결 BUY 0을 확인한 뒤에만 허용
- 잘못된 live confirmation은 주문 원장 예약 전에 차단해 broker ID 없는 고아 주문이 남지 않도록 보강
- dry-run DB를 live DB로 승격하지 않고 **별도 신규 live DB**를 사용하도록 commissioning 계약 고정
- `jdss live-preflight --arm-buy-halt` 추가: fresh ledger, 실계좌 기존 관리종목/미체결, USD buying power를 점검하고 최초 live 기동을 BUY HALT 상태로 준비
- Oracle 외부에서 SSH/systemd/SQLite quick check/시간오차/디스크/config/Toss read-only를 확인하는 hourly health watch 추가
- live에서는 broker side effect 가능 이후 과거 DB snapshot을 맹목적으로 복원하지 않고 broker를 Source of Truth로 reconciliation하도록 runbook 확정

현재 live hard lock은 **그대로 유지**합니다. 위 기능은 실거래 잠금을 해제하지 않으며, 실제 Toss 주문 활성화는 별도 live-enablement PR과 명시적 승인 전까지 금지입니다.

### Oracle 배포 및 독립 검증

운영안전 hardening merge commit **`29b198321a69050651a4ed12242a4eca19d22d13`**을 Oracle에 forced dry-run으로 배포했습니다.

- 배포 전 Ruff PASS
- focused deployment safety tests **39건 PASS**
- config validation PASS
- pinned SSH trust PASS
- 서비스 중지 후 SQLite 일관 snapshot 생성
- release별 venv 설치 및 rollback-safe 전환 PASS
- systemd runtime service active 확인
- Toss read-only 인증·시세·시장일자 smoke PASS
- 배포 후 별도 Runtime Verifier PASS
- verifier 실행 당시 미국장 `closed` 확인 후 systemd 실제 재시작·복구 검증 PASS
- Telegram outbound `getMe → sendMessage → deleteMessage` smoke PASS

배포 run: **32935072232**  
Runtime verifier run: **32935336557**

## 3. 기준 검증 상태

현재 production V3.2.2는 다음 게이트를 유지합니다.

- Quality Gate ✅
- Security Gate ✅
- JDSS V3 canonical Backtest ✅
- focused operational deployment tests 39건 ✅
- forced dry-run Oracle 배포·smoke ✅
- 독립 Runtime Verifier ✅
- pinned SSH trust·DB snapshot·rollback-safe dry-run release ✅
- Toss read-only smoke ✅
- Telegram outbound runtime smoke ✅
- 운영자 BUY halt/resume 안전계층 ✅
- fresh live-ledger commissioning preflight ✅
- live hard lock ✅

## 4. 실거래 준비 상태

현재 평가는 다음과 같습니다.

- **Dry-run production 운영:** GO
- **실거래용 안전 인프라/절차:** 준비 완료 단계
- **실제 live BUY 활성화:** 아직 LOCKED / 별도 승인 필요

실거래 최초 전환 시에는 반드시 다음 순서를 사용합니다.

```text
fresh live DB
→ real-account preflight
→ BUY HALT arm
→ live 전용 배포/기동
→ Toss holdings/open orders/buying power 재조회
→ broker/DB reconciliation PASS
→ Telegram/account/order 확인
→ 운영자 명시적 BUY 재개
```

실제 Toss write path(`POST order → orderId → 주문조회 → 취소/체결 → 원장반영`)는 read-only smoke와 별개이므로 live 잠금 해제 전에 별도 commissioning 검증 대상으로 유지합니다. 실계좌 canary 주문은 자동으로 실행하지 않습니다.

## 5. 운영상 유지해야 할 원칙

- 위험축소 SELL은 자동, 위험증가 BUY는 운영자 승인 방식 유지
- 긴급 이상 시 서비스 종료보다 `/halt`로 BUY만 먼저 차단해 SELL·monitor·reconciliation을 살려둠
- `N건 순차 실행`은 원자적 basket 주문이 아니므로 일부 종목만 제출될 수 있음
- 이미 제출된 앞 주문을 임의로 되팔아 자동 rollback하지 않음
- JDSS가 관리하는 QQQ/TQQQ/SOXL을 같은 Toss 계좌에서 동시에 수동 거래하지 않음
- dry-run 원장과 live 원장을 절대 혼용하지 않음
- 장애·UNKNOWN·원장 불일치·미완료 위험축소는 신규 BUY보다 SAFE_MODE가 우선
- live 환경에서 외부 주문 side effect 가능 이후 DB 과거 snapshot 자동복원 금지
- Oracle 배포 승인과 live 활성화 승인을 같은 것으로 해석하지 않음

## 6. 다음 우선순위

1. Oracle 외부 health watch의 실제 on-demand 실행까지 검증하고 이후 매시간 자동 감시 유지
2. 다음 정상 일일 분석 시점에 아침 운용 브리핑과 `/halt`/대시보드 상태표시를 실제 운영 루틴에서 관찰
3. forced dry-run에서 `주문 없음 / 다음 거래일 대기 / SELL 진행 / 다건 BUY / 부분실패 / 긴급 BUY 차단` 시나리오 지속 관찰
4. 실거래 전환 시점에 **별도 신규 live DB**를 생성하고 `live-preflight --arm-buy-halt`를 당일 실계좌 상태로 실행
5. 실제 Toss write-path commissioning과 live 전용 deploy/recovery를 별도 PR에서 검증
6. 모든 Go-Live 체크리스트 PASS 후에만 별도 명시적 승인으로 live 잠금을 해제
7. QLD/SSO 등 연구 후보는 production과 분리하여 유지
