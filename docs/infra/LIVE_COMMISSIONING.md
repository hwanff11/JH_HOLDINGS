# JH AUTO 실거래 준비·최초 시작·사고 대응

이 문서는 **실계좌 연결, JH AUTO 최초 시작, 긴급정지와 복구의 경계**를 정의합니다. 투자전략 수학은 [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md), 자동운용 계약은 [`../JH_AUTO_SPEC.md`](../JH_AUTO_SPEC.md), 보안 불변식은 [`SECURITY.md`](SECURITY.md)를 따릅니다.

## 1. 네 가지 상태를 구분합니다

| 상태 | 의미 | 신규 AUTO BUY |
|---|---|---|
| DRY-RUN | 모의원장·모의주문 | 불가 |
| LIVE-ARMED | 실제 Toss 계좌·실거래 원장 연결, BUY 잠금 | 불가 |
| JH AUTO 시작 대기 | JH AUTO 배포·자금설정 가능, 최초 시작 미승인 | 불가 |
| JH AUTO RUNNING | 최초 시작승인 후 독립 안전주기 검증 완료 | 조건 충족 시 자동 가능 |

**LIVE commissioning과 `/auto start`는 같은 사건이 아닙니다.** 실계좌가 연결됐다는 이유만으로 자동매매를 시작하지 않습니다.

## 2. 최초 실계좌 연결 원칙

- 모의운용 DB를 실거래 DB로 재사용하지 않음
- 관리종목 QQQ/TQQQ/SOXL은 최초 commissioning 시 청정한 상태여야 함
- 관리종목 미체결 주문 0
- 실제 USD buying power 정상
- 실제 Toss와 새 live 원장 reconciliation 정상
- `live_commissioned=1`
- `operator_buy_halt=1`
- JH AUTO 최초 시작승인 OFF

QQQ/TQQQ/SOXL 외 비관리 종목은 같은 Toss 계좌에 있을 수 있지만 JH AUTO managed equity/HWM/자동 SELL에 포함하지 않습니다. 단, 계좌 공용 USD buying power를 사용하므로 개인 매매가 현금을 소비하면 JH AUTO BUY 가능액이 줄거나 차단될 수 있습니다.

commissioning 이후 같은 Toss 계좌의 QQQ/TQQQ/SOXL을 JH AUTO와 동시에 수동매매하지 않는 것이 운영 원칙입니다.

## 3. `$50,000` 의미

JDSS 3.2.2의 `$50,000`은 **공식 연구·백테스트 비교 기준**입니다. LIVE commissioning의 고정 투자한도가 아닙니다.

실제 자동운용 자금은 commissioning 이후 Telegram에서 대표가 정합니다.

```text
운용 기준자금 × 자동운용비율 = 목표 자동원금
```

JH AUTO 최초 시작 시에는 과거 V3.2.2 원장에 남아 있을 수 있는 `$50,000` HWM/risk state를 실거래 HWM으로 이어받지 않고, **1단계 현재 허용원금**에서 HWM75를 새로 시작합니다.

## 4. 최초 JH AUTO 시작 순서

```text
LIVE-ARMED 배포 완료
→ BUY halt ON 확인
→ 계좌·원장 reconciliation
→ /auto에서 운용 기준자금 설정
→ /auto에서 자동운용비율 설정
→ /auto start
→ 2단계 대표 확인
→ callback에서는 주문 0건
→ startup quarantine 유지
→ 다음 독립 안전주기
→ reconciliation / 미체결 / SAFE_MODE / 정규장 / 최신가격 / 목표수량 재검증
→ 모두 정상일 때만 첫 AUTO BUY 가능
```

최초 시작은 관리종목 보유·원장이 청정하고 목표 자동원금만큼 USD buying power가 확인되는 경우에만 허용합니다.

## 5. 최초·증액 자금투입

목표 자동원금 전체를 첫날 열지 않습니다.

```text
1단계 50%
→ 실제 AUTO 체결 + 목표충족 + 정합성 + 최소 3 미국 거래세션
2단계 75%
→ 같은 조건 재검증
3단계 100%
```

각 단계는 최소 1건 실제 AUTO 체결 증거가 있어야 승격합니다. 단순히 정수주 목표가 0이거나 시간이 지났다는 이유로 다음 자금을 열지 않습니다.

운용 기준자금·자동운용비율은 나중에도 변경할 수 있습니다. 증액은 **추가된 원금만** 단계개방하며 감액은 위험축소를 우선합니다.

## 6. 프로세스 재시작

실거래 서비스가 시작·재시작되면:

1. 동일 live 원장에 두 runtime이 붙지 못하도록 OS 파일잠금
2. JH AUTO bootstrap
3. BUY halt arm
4. 미반영 체결 복구
5. startup quarantine
6. reconciliation
7. 이미 최초 시작승인이 있었고 모든 안전조건이 정상인 경우에만 시스템 임시격리를 자동해제

재시작은 최초 사용자 시작승인을 새로 만들지 않습니다. 대표 `/halt` latch도 자동해제하지 않습니다.

## 7. 대표 긴급정지 `/halt`

`/halt`는 일반 시스템 임시격리보다 강한 운영자 회로차단기입니다.

- 신규 BUY final boundary 즉시 차단
- durable operator latch ON
- 활성 BUY approval 취소
- 미체결 BUY 취소 시도
- 취소요청 성공만으로 취소 완료로 간주하지 않음
- 원주문이 실제 `CANCELED`인지 재확인
- SELL·OrderMonitor·reconciliation은 가능한 범위에서 계속
- **시스템 자동해제 금지**

취소결과가 불명확하면 `UNKNOWN`/SAFE_MODE 원칙으로 다루고 재주문하지 않습니다.

## 8. `/resume`의 의미

`/resume`은 **최초 JH AUTO 시작 명령이 아닙니다.** 대표가 `/halt`를 걸었거나 복구 가능한 안전정지 상태를 명시적으로 해제할 때 사용하는 운영자 복구명령입니다.

```text
/resume
→ BUY 잠금 해제 검토
→ 최종 확인
→ broker/DB reconciliation
→ 미체결 BUY 0
→ SAFE_MODE 복구 가능여부 확인
→ operator latch 해제
```

`/resume` 자체도 주문을 제출하지 않습니다. 정상 JH AUTO라면 이후 다음 독립 안전주기에서 자동실행 조건을 다시 판단합니다. **개별 BUY마다 별도의 사람 승인을 요구하지 않습니다.**

## 9. 자동 주문 실행시간

현재 JH AUTO 자동 allocation BUY/SELL은 **미국 정규장에 맞춰 실행**합니다. 실제 BUY는 broker POST 직전 현재시각으로 정규장을 다시 확인합니다.

전략설정에 남아 있는 pre-market/after-hours 허용값은 과거/호환 실행경로의 설정이며 JH AUTO 자동 allocation의 실제 주문시간을 넓히지 않습니다.

## 10. 실제 주문 write-path

계좌상태를 바꾸는 요청에는 blind retry를 하지 않습니다. POST 응답 timeout, broker receipt 불명확, cancel 결과 불명확, broker/DB 주문 불일치는 성공/실패를 추정하지 않고 신규 BUY를 막은 뒤 실제 상태를 확인합니다.

조회전용 GET의 명확한 일시오류만 제한적으로 재시도할 수 있습니다.

## 11. 주문감시와 오래된 미체결

AUTO BUY가 체결대기 한도를 넘으면 취소를 요청하고 **원주문 상태를 다시 확인**합니다.

- 확실한 취소 → 실제 체결분 반영 → 다음 주기 목표 재계산
- 취소여부 불명 → SAFE_MODE / 신규 BUY 차단
- 부분체결 잔량 → 같은 주문 blind replay 금지

한 안전주기에서 신규 BUY는 최대 1건만 실행합니다.

## 12. LIVE 배포 계약

실거래 코드 업데이트는 보호된 `main`의 정확한 SHA만 배포합니다.

배포 전:

- Quality Gate PASS
- Security PASS
- 필요 시 canonical Backtest PASS
- JH AUTO/live 집중 회귀테스트 PASS
- BUY halt ON

배포 중:

- 기존 live DB·환경 보존
- `/auto start` 금지
- `/resume` 금지
- 운용 기준자금·자동운용비율 임의 변경 금지

배포 후:

- 서비스 active
- DB quick check
- 실제 계좌 read-only 확인
- reconciliation
- Telegram 메뉴/smoke
- BUY halt 유지

배포 실패는 fail-closed로 끝냅니다.

## 13. 장애시 우선순위

1. 추가 BUY를 막는다.
2. 기존 주문의 실제 상태를 확인한다.
3. Toss 보유와 내부 원장을 대조한다.
4. 자동으로 증명할 수 없는 상태는 SAFE_MODE로 유지한다.
5. 필요하면 대표가 `/halt`한다.
6. 원인이 제거되고 정합성이 증명된 뒤에만 `/resume`을 검토한다.

수익기회보다 **상태를 정확히 아는 것**을 우선합니다.
