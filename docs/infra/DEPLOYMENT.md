# Oracle 배포·실계좌 연결·검증·복구 가이드

이 문서는 **Oracle 배포, 최초 실계좌 연결(commissioning), 재시작, 배포 후 검증, rollback·사고복구 절차의 단일 기준**입니다.

- 투자전략 계약: [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md)
- 자동운용 자금·최초 시작·정지 계약: [`../JH_AUTO_SPEC.md`](../JH_AUTO_SPEC.md)
- 보안·주문 안전불변식: [`SECURITY.md`](SECURITY.md)
- 현재 실제 배포상태: [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)

핵심 원칙:

> **배포는 코드를 바꾸는 행위이고, 실계좌 연결은 거래 인프라를 준비하는 행위이며, `/auto start`는 운영자가 위험증가 권한을 여는 별도 행위입니다.**

세 사건을 서로 대신하지 않습니다.

## 1. 운영 상태 구분

| 상태 | 의미 | 신규 AUTO BUY |
|---|---|---|
| DRY-RUN | 모의원장·모의주문 | 불가 |
| LIVE-ARMED | 실제 Toss 계좌·실거래 원장 연결, BUY 잠금 | 불가 |
| JH AUTO 시작 대기 | 코드·자금설정 준비, 최초 시작 미승인 | 불가 |
| JH AUTO RUNNING | 최초 시작승인 후 독립 안전주기 검증 완료 | 조건 충족 시 가능 |

`live_commissioned=1`과 `launch_authorized=1`은 같은 뜻이 아닙니다.

## 2. 표준 Production 배포 경로

### DRY-RUN

실거래 commissioning 전 모의운용 서버 갱신은 표준 dry-run 배포경로를 사용합니다.

### LIVE-ARMED / JH AUTO

실계좌와 실거래 DB가 이미 준비된 운영환경은 live-safe 배포경로만 사용합니다. 기존 실계좌 연결·실거래 DB·운영자 halt latch를 보존하고 서비스 교체 전에 신규 BUY 저수준 잠금을 먼저 겁니다.

배포 workflow가 `/auto start`, `/resume`, 운용 기준자금 변경, 자동운용비율 변경을 수행하면 안 됩니다.

### ChatGPT owner-only Issue ChatOps

사용자가 대화에서 **“배포해”, “배포까지”, “끝까지 마무리”**라고 명확히 승인한 runtime 변경은 사용자의 PC/터미널 연결 없이 ChatGPT가 GitHub Issue ChatOps로 끝까지 처리하는 것이 기본입니다.

```text
필수 PR CI PASS
→ main merge
→ owner가 [deploy-oracle-live-armed] 제목의 Issue 생성
→ workflow가 exact 최신 main 확인
→ 배포 전 BUY halt ON + 재확인
→ Live-Armed 안전배포
→ runtime/DB/reconciliation/Toss read-only/Telegram smoke
→ 별도 [oracle-health-check] Issue
→ 외부 service/SQLite/scheduler/config health 확인
→ 성공 Issue 정리
```

배포/health workflow 실패나 새 회귀가 발견되면 ChatGPT는 이미 승인된 배포 범위 안에서 원인을 수정하고 **branch → CI → merge → 재배포 → health**를 성공할 때까지 반복합니다. merge만 성공한 상태를 완료로 보고하지 않습니다.

## 3. 최초 실계좌 연결 원칙

최초 commissioning 시 관리종목과 실거래 원장을 청정한 상태에서 시작합니다.

필수 확인:

- 모의운용 DB를 실거래 DB로 재사용하지 않음
- 관리종목 QQQ/TQQQ/SOXL의 초기 보유상태 명시 확인
- 관리종목 미체결 주문 0
- 실제 USD buying power 정상
- Toss 실제 계좌와 새 live 원장 대조 정상
- `live_commissioned=1`
- 저수준/operator BUY halt ON
- JH AUTO 최초 시작승인 OFF
- 현재 허용원금 0

QQQ/TQQQ/SOXL 외 비관리 종목이 같은 계좌에 있을 수는 있지만 JH AUTO 목표·HWM·자동 SELL에 자동 포함하지 않습니다. 계좌 공용 buying power를 사용하므로 비관리 거래가 현금을 소비하면 AUTO BUY 가능액이 줄거나 차단될 수 있습니다.

commissioning 이후 관리종목을 Toss 앱에서 JH AUTO와 동시에 수동매매하지 않는 것이 운영 원칙입니다.

## 4. 최초 JH AUTO 시작은 Deployment가 수행하지 않습니다

배포·실계좌 연결 완료 후 운영자는 Telegram에서 별도로 최초 시작을 수행합니다.

```text
LIVE-ARMED 배포 완료
→ BUY halt 상태 확인
→ 계좌·원장 대조
→ /auto에서 운용 기준자금 설정
→ /auto에서 자동운용비율 설정
→ /account 확인
→ /auto start
→ 운영자 2단계 확인
→ callback 주문 0건
→ 다음 독립 안전주기
→ 정합성·미체결·SAFE_MODE·정규장·가격·목표·자금 재검증
→ 모두 정상일 때만 첫 AUTO BUY 가능
```

정확한 자금개방 단계와 시작승인 계약은 `JH_AUTO_SPEC.md`가 소유합니다.

## 5. 배포 전 필수 게이트

하나라도 실패하면 배포하지 않습니다.

### Source / CI

- 원격 최신 `main` SHA 확인
- Quality Gate PASS
- Security Gate PASS
- 전략·백테스트 영향이 있으면 canonical Backtest PASS
- config validation PASS
- 주문·DB·AUTO 변경이면 관련 집중테스트 PASS

### LIVE runtime

- live DB 존재·무결성 정상
- 계좌·원장 대조 정상 또는 배포를 안전하게 차단할 상태
- 미체결/UNKNOWN 상태 확인
- 신규 BUY 잠금을 배포 전에 ON으로 만들 수 있음
- 운영자 `/halt` latch 상태 보존 가능
- schema/config migration 필요 여부 확인

### 인프라

- 검증된 SSH host key 고정
- 필요한 Environment secret 존재
- runtime 파일 권한 정상
- server clock/disk/SQLite 상태 정상

schema/config version이 바뀌면 일반 코드 업데이트로 취급하지 않고 migration·호환성·rollback 계획을 먼저 검증합니다.

## 6. SSH 및 인증 신뢰경계

- `StrictHostKeyChecking=yes`
- 사전에 검증된 host public key만 사용
- Actions 실행 중 새 host key를 즉석 신뢰하지 않음
- host key 변경 시 원인 확인 전 배포 중단
- API key·token·계좌번호·SSH private key를 로그나 Issue에 기록하지 않음

Toss client-credentials는 실행 중인 다른 프로세스와 충돌할 수 있으므로 외부 LIVE health checker가 독립 token issuer가 되어서는 안 됩니다. 외부 health에서는 service/DB/config/scheduler 상태를 검증하고 Toss token 발급은 생략합니다. LIVE runtime 내부의 read-only smoke는 shared token 경계를 따릅니다.

## 7. LIVE 배포 순서

```text
최신 main 확인
→ 필수 CI/설정검증
→ 신규 BUY 잠금 ON
→ 기존 주문·원장·서비스 상태 확인
→ 새 release 준비
→ service 정지
→ 안전한 SQLite snapshot
→ 새 release 전환
→ DB/schema/config 검증
→ service 시작
→ startup quarantine
→ 미반영 주문·체결 복구
→ 계좌·원장 대조
→ service/Telegram/Toss read-only smoke
→ 외부 health 확인
→ 최종 BUY 잠금·quarantine 상태 판정
```

배포 중 금지사항:

- `/auto start` 실행
- `/resume` 실행
- 운용 기준자금·자동운용비율 임의 변경
- 임의 BUY 주문
- 운영자 `/halt` latch 삭제
- UNKNOWN 주문을 성공/실패로 추정

## 8. 일시적 provider 장애와 SAFE_MODE 경계

### 명확한 read-only 일시장애

holdings, OPEN orders, 이미 식별된 주문상태 같은 GET이 `token-revoked`, expired/invalid token, retryable 429/timeout/5xx 등으로 실패했지만 **상반된 실제 broker 상태가 관찰되지 않은 경우**에는 원장 손상으로 단정하지 않습니다.

```text
bounded GET retry
→ 계속 실패하면 신규 BUY 임시격리
→ read 정상화
→ canonical reconciliation 2회 연속 PASS
→ 다른 안전조건이 정상일 때 system quarantine 자동복구
→ 다음 독립 안전주기에서만 위험증가 가능
```

### 구조적 불일치

GET이 정상 응답한 뒤 다음이 확인되면 sticky SAFE_MODE를 유지합니다.

- 실제 broker 수량과 원장 불일치
- UNKNOWN 주문
- 주문 identity/상태 불일치
- 자동으로 안전함을 증명할 수 없는 execution state

이 경우 자동으로 `/resume`하지 않습니다.

### write-path

계좌상태를 바꾸는 주문/취소 POST에는 **blind retry**를 하지 않습니다. timeout, receipt 불명확, 인증실패 뒤 성공여부가 불명확하면 신규 BUY를 막고 실제 주문상태를 확인합니다.

## 9. 일일 시세 provider 장애

07:00 일일 분석 시 최신 완료 일봉을 확보하지 못하면 전일 stale 데이터를 오늘 데이터처럼 사용하지 않습니다.

- 신규 BUY 임시 차단
- 5 → 10 → 15 → 30 → 60분 bounded backoff
- 이후 장애 지속 시 60분 간격 재확인
- 동일 장애 알림 반복 억제
- 복구 완료 알림 1회
- 당일 분석 완료 전 전일 BUY 후보 실행 금지

Toss daily candle은 조정주가·분할·전략 parity가 별도로 검증되기 전 LIVE 전략 fallback으로 자동 활성화하지 않습니다.

## 10. 배포 후 상태 판정

### 최초 시작승인 전

```text
launch_authorized = 0
현재 허용원금     = 0
신규 BUY          = 차단
```

이어야 합니다.

### 이미 최초 시작승인 후

서비스 재시작 시 먼저 BUY 잠금과 startup quarantine을 적용합니다. 자동복귀하려면 기존 시작승인 보존, 운영자 `/halt` OFF, SAFE_MODE 없음, 미체결/UNKNOWN 없음, 계좌·원장 대조 정상, runtime/config 정상임을 새로 증명해야 합니다.

### 운영자 `/halt`

배포 전 운영자 `/halt`가 ON이었다면 새 release에서도 그대로 ON이어야 하며 시스템이 자동해제하면 안 됩니다.

## 11. 프로세스 재시작과 rollback

실거래 service 시작·재시작 시 동일 live 원장 중복 runtime을 막고, 신규 BUY 안전화 → 미반영 체결 복구 → startup quarantine → 계좌·원장 대조 순서를 따릅니다.

### 새 service 시작 전 실패

검증된 이전 release와 일관된 DB snapshot으로 rollback할 수 있습니다.

### 새 service 시작 후 상태변경 가능성이 생긴 뒤 실패

실제 주문·체결·자금상태가 변할 수 있으므로 DB를 과거 snapshot으로 무조건 되감지 않습니다. 먼저 실제 Toss 주문·보유와 현재 원장을 대조하고 데이터 손실 없는 복구경로를 선택합니다.

원칙:

- 소스 rollback과 DB rollback을 같은 의미로 보지 않음
- broker 실제 상태보다 과거 DB snapshot을 우선하지 않음
- UNKNOWN/open order가 있으면 rollback 전에 실제 상태 확인
- 복구 후에도 startup quarantine과 계좌·원장 대조 재수행

## 12. 오래된 미체결·부분체결

AUTO BUY가 허용 대기시간을 넘으면 취소를 요청하고 **원주문 상태를 다시 확인**합니다.

- 확실한 취소 → 실제 체결분 반영 → 다음 주기 목표 재계산
- 취소여부 불명 → SAFE_MODE / 신규 BUY 차단
- 부분체결 잔량 → 동일 요청 blind retry 금지

한 안전주기 신규 BUY 최대건수와 일일 회로차단 상한은 `JH_AUTO_SPEC.md`가 소유합니다.

## 13. 배포 후 검증 체크리스트

- [ ] 기대한 `main` SHA / package / config / strategy 일치
- [ ] service active
- [ ] live commissioning 상태 보존
- [ ] 운영자 `/halt` latch / BUY halt 보존
- [ ] startup quarantine 의도대로 적용
- [ ] DB quick check 정상
- [ ] 미체결·UNKNOWN 및 계좌·원장 대조 정상
- [ ] Toss read-only runtime smoke 정상
- [ ] Telegram 메뉴/smoke 정상
- [ ] scheduler heartbeat 정상
- [ ] 별도 외부 health PASS
- [ ] 배포/health Issue 결과 정리

사용자가 배포까지 승인한 runtime 변경은 이 체크리스트가 끝나기 전에는 완료로 간주하지 않습니다.

## 14. 문서-only 변경

Markdown 설명·링크·문서 구조만 바뀌고 코드·설정·runtime 동작이 바뀌지 않았다면:

- 문서 계약·링크·금지식별자 테스트를 실행합니다.
- PR 필수 check를 통과시킵니다.
- `main`에 병합합니다.
- **Oracle runtime은 재배포하지 않습니다.**

문서가 실제 동작과 다른 새 계약을 약속한다면 문서-only 변경이 아니므로 코드·테스트와 함께 변경해야 합니다.
