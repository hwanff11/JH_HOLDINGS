# JH_HOLDINGS 보안·주문 안전 기준

이 문서는 **인증·주문·DB·멱등성·계좌 대조·SAFE_MODE의 기술적 안전 불변식**을 소유합니다.

- 전략 수학: [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md) + [`../../strategy.yaml`](../../strategy.yaml)
- 자동운용 자금·최초 시작·자금개방·회로차단 계약: [`../JH_AUTO_SPEC.md`](../JH_AUTO_SPEC.md)
- 배포·실계좌 연결·복구: [`DEPLOYMENT.md`](DEPLOYMENT.md)
- 현재 실제 상태: [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)

## 1. 운영자가 먼저 기억할 원칙

1. 배포와 `/auto start`는 별개입니다.
2. 결과를 확정할 수 없는 주문은 재전송하지 않습니다.
3. Toss와 내부 원장이 다르면 신규 BUY를 차단합니다.
4. 위험축소 SELL을 위험증가 BUY보다 먼저 처리합니다.
5. 운영자 `/halt`는 시스템이 자동해제하지 않습니다.
6. JH AUTO의 자금·단계·일일 주문시도 상한은 `JH_AUTO_SPEC.md`가 단일 기준입니다.

## 2. 신뢰 경계

| 경계 | 신뢰 가능한 것 | 항상 다시 검증할 것 |
|---|---|---|
| Telegram | 설정된 관리자 식별자 | private chat, `from_user`, callback token/TTL/revision, DB 현재상태 |
| JDSS | 검증된 전략 설정과 목표비중 | 데이터 freshness, strategy generation/version |
| JH AUTO | 커밋된 자동운용 상태 | 자금·현재 허용원금·launch/quarantine/halt 불변식 |
| SQLite | transaction으로 확정된 내부 원장 | 실제 Toss 주문·보유와 계좌 대조 |
| Toss OpenAPI | 현재 HTTPS 응답 | 인증, 숫자범위, order ID, 보유·미체결·buying power |
| GitHub Actions | 보호된 main과 승인된 workflow | source SHA, 실행주체, Environment, secret 존재 |
| Oracle | 배포된 release | 단일 runtime, DB 상태, 실제 계좌·원장, BUY 잠금 |

한 경계의 성공을 다른 경계의 성공으로 간주하지 않습니다.

## 3. 비밀정보와 공개 저장소

Git·로그·Markdown·Issue·테스트 fixture에 다음을 기록하지 않습니다.

- `.env` 실제 값
- Telegram token
- Toss key/secret·인증 header
- SSH private key
- 전체 계좌번호
- approval raw token
- GitHub Environment secret

공개 Markdown에는 서버 절대경로·OS 사용자명·서비스 실명·host 식별자·실제 backup/snapshot 파일명도 기록하지 않습니다.

## 4. GitHub 변경 통제

- 기능 변경은 별도 branch + PR
- 보호된 `main` 직접 push/force push 금지
- 필요한 Quality Gate / Security Gate / Backtest 확인
- Actions 기본 권한 최소화
- 외부 Action은 검증된 commit SHA로 고정
- 실거래 배포 전 신규 BUY 안전화
- 배포는 `/auto start`, `/resume`, 자금변경을 대신하지 않음

## 5. Telegram 관리자 인증

- 정확히 허용된 관리자 Chat ID만 통제명령 사용
- private chat만 허용
- 명령과 callback 모두 `chat.id`와 `from_user.id` 확인
- callback payload만 신뢰하지 않고 DB 현재상태·revision 재검증
- stale·만료·재사용 token 거부
- Telegram에는 정제된 오류요약만 표시하고 traceback·secret은 서버 로그에서 확인

## 6. 최초 시작과 자금설정 경계

최초 시작·현재 허용원금·자금개방 단계·설정변경의 비즈니스 계약은 `JH_AUTO_SPEC.md`가 소유합니다.

보안경계에서는 다음만 강제합니다.

- 시작승인 전 신규 BUY 차단
- 최종 start callback에서 주문 0건
- 설정변경 callback에서 주문 0건
- 설정변경 중 BUY 잠금과 격리
- stale/재사용 callback 거부
- 시작·자금·성과상태의 원자적 저장
- 부분저장·상태증명 실패 시 fail-closed

## 7. 위험증가 BUY 최종 불변식

신규 BUY는 `OrderManager`에 도달하기 전과 broker POST 직전 안전경계를 통과해야 합니다.

최소 확인범위:

- JH AUTO 정상상태·최초 시작승인·halt/quarantine/SAFE_MODE
- 현재 자금·HWM75·목표수량 계약 정상
- 현재 보유 + committed BUY + 이번 주문이 목표를 초과하지 않음
- managed cash와 Toss buying power 범위 안
- 기존 활성 BUY·미체결 예약·멱등성 상태 정상
- 실제 제출시각 미국 정규장
- 실제 제출가격 freshness 정상

정확한 자금관계와 주문시도 회로차단 상한은 `JH_AUTO_SPEC.md`를 참조합니다. 보안코드가 해당 계약을 우회하거나 더 느슨하게 해석하면 안 됩니다.

## 8. 내부 2단계 검증과 자동실행

기존 review → execution approval 코드를 삭제하거나 우회하지 않습니다. JH AUTO는 안전주기 안에서 이를 내부 검증으로 소비합니다.

```text
BUY 필요
→ review
→ 최신 가격·수량 검증
→ execution
→ 최종 안전경계
→ OrderManager
→ Toss
```

정상 AUTO 운영에서 운영자가 개별 BUY마다 Telegram 최종승인을 누르는 것은 기본계약이 아닙니다.

## 9. SELL-first

목표 위험이 줄어들면 SELL을 먼저 처리합니다.

```text
SELL 제출
→ 주문상태 확정
→ 누적체결 delta 반영
→ Toss/원장 대조
→ 정상일 때만 다음 BUY 후보
```

부분체결·미완료·`UNKNOWN`·취소결과 불명확 상태에서는 신규 BUY를 차단합니다.

## 10. 주문 멱등성과 write retry 금지

- 결정적 `client_order_id`
- DB transaction에서 주문예약
- broker receipt의 client/broker ID, symbol, side, qty 검증
- cumulative filled qty 감소 거부
- 종료주문의 비종료상태 회귀 거부
- cumulative fill은 이전 적용분과의 delta만 원장반영
- POST timeout·응답유실·결과불명은 `UNKNOWN`
- **계좌를 바꾸는 POST/취소 요청의 결과가 애매하면 blind replay 금지**

조회전용 GET의 명확한 일시오류만 제한적으로 재시도할 수 있습니다.

## 11. 미체결·부분체결·취소

BUY가 허용 대기시간을 넘으면 취소를 요청하고 원주문 상태를 다시 확인합니다.

- 확실한 취소 → 실제 체결분 반영 → 다음 주기 목표 재계산
- 취소여부 불명 → SAFE_MODE / 신규 BUY 차단
- 부분체결 잔량 → 같은 요청 blind replay 금지

취소 API의 성공응답만으로 실제 주문이 취소됐다고 확정하지 않습니다.

## 12. 계좌·원장 대조와 provider 장애

Toss 실제 보유·미체결이 최종 외부 사실입니다. 내부 원장과 다르면 어느 한쪽을 임의 정답으로 덮어쓰지 않습니다.

**브로커 조회 실패와 브로커가 실제 불일치를 반환한 상태를 구분합니다.**

### 명확한 read-only 일시장애

보유수량, OPEN 주문, 이미 식별된 주문상태 같은 GET이 `token-revoked`, expired/invalid token, retryable 429/timeout/5xx 등으로 실패했지만 모순되는 broker 상태를 아직 관찰하지 못했다면:

1. bounded GET retry
2. 계속 실패하면 신규 BUY를 시스템 임시격리로 차단
3. 조회 정상화 후 holdings·OPEN orders·known order status를 다시 읽음
4. canonical 계좌·원장 대조를 독립 안전주기에서 2회 연속 통과
5. 다른 안전조건도 정상일 때만 시스템 임시격리 자동복귀 가능
6. 복귀가 증명된 같은 호출에서 바로 위험증가 주문을 이어 보내지 않고 다음 독립 안전주기에서 다시 검증

단순 GET 실패만으로 `0주`, `미체결 없음`, `정상`으로 해석하지 않으며, 반대로 **상반된 상태가 관찰되지 않은 provider 장애만으로 원장 손상형 sticky SAFE_MODE를 만들지도 않습니다.**

### 구조적 불일치

조회가 정상 응답한 뒤 실제로 다음이 확인되면 기존 강한 안전정지를 유지합니다.

1. 신규 BUY 차단
2. 활성 주문 상태 확인
3. 누락 체결 복구 가능성 확인
4. 보유·미체결·원장 재대조
5. 실제 수량불일치, `UNKNOWN`, 주문 identity/상태 불일치 등 자동으로 정상증명 불가하면 sticky SAFE_MODE 유지

## 13. 운영자 `/halt`, 시스템 임시격리, SAFE_MODE

세 상태의 의미를 섞지 않습니다.

- **운영자 `/halt`**: durable latch, 시스템 자동해제 금지
- **시스템 임시격리**: 재시작·설정변경·provider 복구점검 중 신규 BUY 차단. 조건 충족 시 시스템 복귀 가능
- **SAFE_MODE**: 실제 주문·원장·전략상태가 불일치하거나 execution state가 불확실해 정상증명이 필요한 강한 안전정지

provider 일시장애에서 시작된 시스템 임시격리의 자동복구가 운영자 `/halt`를 해제해서는 안 됩니다. `/resume`은 운영자 확인이 필요한 구조적 복구행위이며 주문 제출 버튼이 아닙니다.

## 14. 프로세스·재시작 안전

- 같은 live SQLite에 두 runtime이 동시에 붙지 못하도록 OS 실행잠금
- 프로세스 시작마다 startup quarantine
- 미반영 체결 복구 후 계좌·원장 대조
- `UNKNOWN`/open order 추정 성공처리 금지
- 저장 목표와 broker-confirmed actual/committed order 차이만 새 BUY gap으로 사용
- 운영자 `/halt` latch를 재시작·배포가 지우지 않음

## 15. SQLite 안전

- 주문예약·승인·자금변경·성과상태는 필요한 원자경계 안에서 transaction 처리
- WAL/foreign key/무결성 설정은 지원환경에서 검증
- schema 변경은 migration·호환성·rollback 계획 없이 실거래 DB에 적용하지 않음
- DB quick check 실패를 무시하고 서비스만 active로 만들지 않음
- snapshot 복구는 실제 주문상태와 충돌할 수 있으므로 서비스 시작 후 상태변경이 발생한 DB를 무조건 과거로 되감지 않음

## 16. Toss API·shared token 경계

- 공식 HTTPS endpoint만 사용
- 인증·HTTP status·JSON schema·숫자범위 검증
- read-only GET과 state-changing POST/DELETE의 재시도 정책 분리
- 주문 receipt의 식별자·종목·side·수량 검증
- broker time/market session과 실제 제출시각 검증
- 최신 가격 freshness를 사용자 표시 편의 때문에 완화하지 않음

동일 호스트에서 복수 runtime이 같은 Toss client credential을 사용할 수 있는 경우 token refresh를 각 process가 독립 발급하지 않습니다. file-lock 기반 shared token cache로 refresh를 직렬화하고, 다른 process가 이미 갱신한 최신 token이 있으면 이를 재사용합니다.

- token/cache/credential 실제 값은 로그·Markdown·Issue에 기록하지 않음
- `token-revoked` 등 명확한 인증 갱신은 read-only GET에서만 bounded replay 가능
- 주문/취소 write는 인증 갱신 뒤에도 blind replay 금지
- 외부 LIVE health checker는 실행 중 runtime token 보호를 위해 독립 token issuer가 되지 않음

## 17. GitHub Actions와 SSH

- 최소권한 token
- 검증된 main SHA만 운영배포
- 승인된 owner 경로만 실거래 배포
- `StrictHostKeyChecking=yes`
- 사전에 검증된 host public key 고정
- Actions 실행 중 새 host key를 즉석 신뢰하지 않음
- host key가 바뀌면 원인 확인 전 배포 중단

## 18. 알림 실패

Telegram 알림 실패를 주문 실패로 해석하지 않습니다.

- 주문·체결 상태는 계속 주문감시와 Toss 조회로 판단
- 알림 실패 때문에 주문 재전송 금지
- 실패시각·정제된 오류유형 기록
- 설정확인 결과 전송 실패를 성공으로 숨기지 않음

## 19. 보안 변경 Definition of Done

주문·DB·Telegram·배포 안전경계를 바꾸면 최소 다음을 확인합니다.

- 인증·권한
- stale/TTL/revision callback
- 중복주문·멱등성
- 부분체결·UNKNOWN
- write blind retry 금지
- read-only provider 일시장애와 구조적 SAFE_MODE 구분
- shared token refresh 단일화
- SELL-first
- restart/reconciliation
- 운영자 halt 보존
- startup quarantine
- SAFE_MODE
- broker POST 직전 세션·가격 재검사
- 공개 문서/로그 secret 및 운영식별자 미노출

자동운용 자금·단계·회로차단 숫자를 변경하려면 먼저 `JH_AUTO_SPEC.md`와 코드·테스트를 변경하고 이 문서는 안전경계가 그 계약을 강제하는지 확인합니다.
