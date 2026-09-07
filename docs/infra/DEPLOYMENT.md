# Oracle 배포·실계좌 연결·검증·복구 가이드

이 문서는 **Oracle 배포, 최초 실계좌 연결(commissioning), 재시작, 배포 후 검증, rollback·사고복구 절차의 단일 기준**입니다.

- 투자전략 계약: [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md)
- 자동운용 자금·최초 시작·정지 계약: [`../JH_AUTO_SPEC.md`](../JH_AUTO_SPEC.md)
- 보안·주문 안전불변식: [`SECURITY.md`](SECURITY.md)
- 현재 실제 배포상태: [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)

핵심 원칙:

> **배포는 코드를 바꾸는 행위이고, 실계좌 연결은 거래 인프라를 준비하는 행위이며, `/auto start`는 운영자가 위험증가 권한을 여는 별도 행위입니다.**

세 사건을 서로 대신하지 않습니다.

## 1. 운영 상태를 구분합니다

| 상태 | 의미 | 신규 AUTO BUY |
|---|---|---|
| DRY-RUN | 모의원장·모의주문 | 불가 |
| LIVE-ARMED | 실제 Toss 계좌·실거래 원장 연결, BUY 잠금 | 불가 |
| JH AUTO 시작 대기 | 코드·자금설정 준비, 최초 시작 미승인 | 불가 |
| JH AUTO RUNNING | 최초 시작승인 후 독립 안전주기 검증 완료 | 조건 충족 시 가능 |

`live_commissioned=1`과 `launch_authorized=1`은 같은 뜻이 아닙니다.

## 2. 운영 모드별 배포 경로

### DRY-RUN

실거래 commissioning 전 모의운용 서버 갱신은 표준 dry-run 배포 경로를 사용합니다.

### LIVE-ARMED / JH AUTO

실계좌와 실거래 DB가 이미 준비된 운영환경은 live-safe 배포경로만 사용합니다. 이 경로는 기존 실계좌 연결·실거래 DB·운영자 halt latch를 보존하고 서비스 교체 전에 신규 BUY 저수준 잠금을 먼저 겁니다.

배포 workflow가 `/auto start`, `/resume`, 운용 기준자금 변경, 자동운용비율 변경을 수행하면 안 됩니다.

## 3. 최초 실계좌 연결 원칙

최초 commissioning 시 관리종목과 실거래 원장을 청정한 상태에서 시작합니다.

필수 확인:

- 모의운용 DB를 실거래 DB로 재사용하지 않음
- 관리종목 QQQ/TQQQ/SOXL의 초기 보유상태를 명시적으로 확인
- 관리종목 미체결 주문 0
- 실제 USD buying power 정상
- Toss 실제 계좌와 새 live 원장 대조 정상
- `live_commissioned=1`
- 저수준/operator BUY halt ON
- JH AUTO 최초 시작승인 OFF
- 현재 허용원금 0

QQQ/TQQQ/SOXL 외 비관리 종목이 같은 계좌에 있을 수는 있지만 JH AUTO 목표·HWM·자동 SELL에 자동 포함하지 않습니다. 다만 계좌 공용 buying power를 사용하므로 비관리 거래가 현금을 소비하면 AUTO BUY 가능액이 줄거나 차단될 수 있습니다.

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

정확한 자금개방 단계와 시작승인 계약은 `JH_AUTO_SPEC.md`가 소유합니다. 이 배포문서는 해당 계약을 중복 정의하지 않습니다.

## 5. 배포 전 필수 게이트

하나라도 실패하면 배포하지 않습니다.

### Source / CI

- 원격 최신 `main` SHA 확인
- Quality Gate PASS
- Security Gate PASS
- 전략·백테스트 영향이 있으면 canonical Backtest PASS
- `jdss validate-config` PASS
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

## 6. SSH 신뢰경계

- `StrictHostKeyChecking=yes`
- 사전에 검증된 host public key만 `known_hosts`로 사용
- Actions 실행 중 `ssh-keyscan` 결과를 즉석 신뢰하지 않음
- `accept-new` 금지
- host key가 바뀌면 원인 확인 전 배포 중단

실제 서버 절대경로·OS 사용자명·서비스 실명·secret·실제 backup 파일명은 공개 Markdown에 기록하지 않습니다.

## 7. LIVE 배포 순서

논리적 순서는 다음과 같습니다.

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

## 8. 배포 후 상태 판정

### 최초 시작승인 전

배포 후에도 반드시:

```text
launch_authorized = 0
현재 허용원금     = 0
신규 BUY          = 차단
```

이어야 합니다.

### 이미 최초 시작승인 후

서비스 재시작 시 먼저 BUY 잠금과 startup quarantine을 적용합니다.

이후 시스템이 자동복귀하려면 최소 다음을 새로 증명해야 합니다.

- 기존 `launch_authorized=1` 보존
- 운영자 `/halt` latch OFF
- SAFE_MODE 없음
- 미체결/UNKNOWN 없음
- 계좌·원장 대조 정상
- runtime/config/strategy generation 정상

배포 workflow가 halt를 미리 풀어주는 것이 아닙니다.

### 운영자 `/halt` 상태

배포 전 운영자 `/halt`가 ON이었다면 새 release에서도 그대로 ON이어야 하며 시스템이 자동복귀하면 안 됩니다.

## 9. 프로세스 재시작

실거래 service 시작·재시작 시:

1. 동일 live 원장에 두 runtime이 붙지 못하도록 실행잠금
2. JH AUTO bootstrap
3. 신규 BUY 안전화
4. 미반영 체결 복구
5. startup quarantine
6. 계좌·원장 대조
7. 이미 최초 시작승인이 있고 모든 자동복귀 조건이 정상일 때만 임시격리 해제

재시작은 최초 사용자 시작승인을 만들지 않고 운영자 `/halt` latch를 해제하지 않습니다.

## 10. DB snapshot과 rollback

### 새 service 시작 전 실패

새 runtime이 실제 주문·상태변경을 시작하기 전에 실패하면 검증된 이전 release와 일관된 DB snapshot으로 rollback할 수 있습니다.

### 새 service 시작 후 상태변경 가능성이 생긴 뒤 실패

실제 주문·체결·자금상태가 변할 수 있는 구간에서는 DB를 과거 snapshot으로 무조건 되감지 않습니다. 먼저 실제 Toss 주문·보유와 현재 원장을 대조하고, 데이터 손실 없는 복구경로를 선택합니다.

원칙:

- 소스 rollback과 DB rollback을 같은 의미로 보지 않음
- broker 실제 상태보다 과거 DB snapshot을 우선하지 않음
- UNKNOWN/open order가 있으면 rollback 전에 실제 상태 확인
- 복구 후에도 startup quarantine과 계좌·원장 대조를 다시 수행

## 11. 주문 write-path 장애

계좌상태를 바꾸는 POST·취소 요청에는 blind retry를 하지 않습니다.

- POST timeout
- broker receipt 불명확
- cancel 결과 불명확
- broker/DB 주문 불일치

에서는 성공/실패를 추정하지 않고 신규 BUY를 막은 뒤 실제 주문상태를 확인합니다.

조회전용 GET의 명확한 일시오류만 제한적으로 재시도할 수 있습니다.

## 12. 오래된 미체결·부분체결

AUTO BUY가 허용 대기시간을 넘으면 취소를 요청하고 **원주문 상태를 다시 확인**합니다.

- 확실한 취소 → 실제 체결분 반영 → 다음 주기 목표 재계산
- 취소여부 불명 → SAFE_MODE / 신규 BUY 차단
- 부분체결 잔량 → 동일 요청 blind replay 금지

한 안전주기 신규 BUY 최대건수와 일일 회로차단 상한은 `JH_AUTO_SPEC.md`가 소유합니다.

## 13. 장애 대응 우선순위

1. 추가 BUY를 막습니다.
2. 기존 주문의 실제 상태를 확인합니다.
3. Toss 보유와 내부 원장을 대조합니다.
4. 자동으로 증명할 수 없는 상태는 SAFE_MODE로 유지합니다.
5. 필요하면 운영자가 `/halt`합니다.
6. 원인이 제거되고 정합성이 증명된 뒤 `/resume` 또는 시스템 자동복귀 가능성을 판단합니다.

수익기회보다 **상태를 정확히 아는 것**을 우선합니다.

## 14. 배포 후 검증 체크리스트

- [ ] 기대한 main SHA / package / config / strategy 일치
- [ ] service active
- [ ] live commissioning 상태 보존
- [ ] 운영자 `/halt` latch 보존
- [ ] startup quarantine 의도대로 적용
- [ ] DB quick check 정상
- [ ] 미체결·UNKNOWN 확인
- [ ] Toss read-only 조회 정상
- [ ] 계좌·원장 대조 정상
- [ ] Telegram 메뉴/smoke 정상
- [ ] 외부 health/recent activity 확인
- [ ] 최초 시작 미승인이면 현재 허용원금 0·신규 BUY 차단
- [ ] 이미 시작된 계정이면 자동복귀 조건을 runtime이 독립 재검증

## 15. 문서-only 변경

Markdown 설명·링크·문서 구조만 바뀌고 코드·설정·runtime 동작이 바뀌지 않았다면:

- 문서 계약·링크·금지식별자 테스트를 실행합니다.
- PR 필수 check를 통과시킵니다.
- `main`에 병합합니다.
- **Oracle runtime은 재배포하지 않습니다.**

문서가 실제 동작과 다른 새 계약을 약속한다면 문서-only 변경이 아니므로 코드·테스트와 함께 변경해야 합니다.
