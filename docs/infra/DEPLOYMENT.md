# Oracle 배포·검증·롤백 가이드

이 문서는 **Oracle 배포 절차와 배포 후 안전검증**을 소유합니다.

- 투자전략 계약: [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md)
- 자동매매 계약: [`../JH_AUTO_SPEC.md`](../JH_AUTO_SPEC.md)
- 보안·주문 안전경계: [`SECURITY.md`](SECURITY.md)
- 현재 실제 배포 상태: [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)

핵심 원칙은 하나입니다.

> **배포는 코드를 바꾸는 행위이고, `/auto start`는 대표가 자동 위험증가 권한을 여는 별도 행위입니다.**

배포 workflow는 `/auto start`를 대신하지 않으며 `/resume`도 자동 실행하지 않습니다.

---

## 1. 운영 모드별 배포 경로

### 모의운용

모의운용 Oracle은 표준 `deploy.sh` 또는 owner-only dry-run ChatOps 경로를 사용합니다.

### 이미 실계좌가 연결된 LIVE-ARMED / JH AUTO

실계좌와 별도 실거래 원장이 이미 준비된 Oracle은 표준 dry-run 배포를 사용하지 않습니다.

반드시:

- `deploy_live_armed.sh`
- owner-only **Deploy Oracle Live Armed** workflow

경로만 사용합니다.

이 경로는 기존 실계좌 연결과 live DB를 보존하고, service 교체 전에 신규 BUY 저수준 잠금을 먼저 겁니다.

---

## 2. JH AUTO의 배포·시작·재시작 상태를 구분합니다

### 최초 JH AUTO 배포 전

```text
실계좌 연결         가능
JH AUTO 코드        미배포/구버전
대표 최초 시작승인 미승인
신규 BUY            잠금
```

### 최초 JH AUTO 배포 직후

```text
JH AUTO 코드        배포 완료
대표 최초 시작승인 미승인
현재 허용원금       $0
시스템 임시격리     ON
신규 BUY            차단
```

이 상태에서 코드·Telegram·계좌조회가 모두 정상이어도 실제 BUY는 발생하면 안 됩니다.

### 이미 `/auto start`가 완료된 이후의 정상 재배포/재시작

서비스가 다시 시작되면 먼저:

```text
저수준 BUY halt ON
→ JH AUTO startup quarantine ON
→ 주문 복구
→ 계좌·원장 재대조
→ SAFE_MODE·열린 주문·대표 halt latch 확인
```

순서로 **항상 fail-closed**로 시작합니다.

다만 이것은 대표가 건 `/halt`와 다른 **시스템 임시격리**입니다.

이미 최초 시작승인이 저장돼 있고 다음이 모두 증명되면 JH AUTO가 후속 독립 안전주기에서 자동으로 임시격리를 해제할 수 있습니다.

- `launch_authorized=1`
- 대표 `/halt` latch OFF
- SAFE_MODE 없음
- 미체결 주문 없음
- 계좌·원장 대조 정상
- 필요한 runtime 상태 정상

따라서 “모든 재시작 뒤 대표가 반드시 `/resume`해야 한다”는 계약이 아닙니다.

반대로 대표가 직접 `/halt`한 상태는 시스템이 절대 자동해제하지 않습니다.

---

## 3. `$50,000`은 배포 한도가 아닙니다

JDSS 3.2.2의 `$50,000`은 공식 연구·백테스트 비교 기준입니다.

LIVE에서는:

```text
운용 기준자금 × 자동운용비율 = 목표 자동원금
```

이며 두 값은 Telegram `/auto`에서 대표가 정합니다.

배포 workflow가 `$50,000`을 실제 운용 기준자금으로 자동 입력하거나 HWM75 실거래 한도로 강제하면 안 됩니다.

최초 시작승인 전에는 HWM75 현재 위험예산도 실거래 값으로 표시하지 않습니다.

---

## 4. 배포 전 필수 게이트

하나라도 실패하면 배포하지 않습니다.

### Source / CI

- 원격 최신 `main` SHA 확인
- Quality Gate PASS
- Security PASS
- 전략·백테스트 영향이 있으면 canonical JDSS V3 Backtest PASS
- `jdss validate-config` PASS
- 주문·DB·AUTO 변경이면 관련 집중테스트 PASS

### LIVE runtime

- 실거래 확인값 정상
- live DB 존재·무결성 정상
- 계좌·원장 대조 정상
- 미체결/UNKNOWN 상태 확인
- operator BUY halt를 배포 전에 ON으로 만들 수 있음
- representative `/halt` latch 상태 보존
- DB schema/config migration 필요 여부 확인

### 인프라

- 검증된 SSH host key 고정
- Environment secret 존재
- systemd/runtime 파일 권한 정상
- 서버 clock/disk/SQLite 상태 정상

config version이나 schema가 바뀌면 일반 live update로 처리하지 않고 별도 migration 계획·호환성·rollback 테스트를 먼저 수행합니다.

---

## 5. SSH 신뢰경계

- `StrictHostKeyChecking=yes`
- 검증된 host public key만 `known_hosts`로 사용
- Actions 실행 중 `ssh-keyscan` 결과를 즉석 신뢰하지 않음
- `accept-new` 금지
- host key가 바뀌면 원인 확인 전 배포 중단

실제 서버 절대경로·OS 사용자명·서비스 실명·secret 값은 공개 Markdown에 기록하지 않습니다.

---

## 6. LIVE-ARMED 배포 순서

논리적 순서는 다음과 같습니다.

```text
최신 main 확인
→ 필수 CI/설정 검증
→ 배포 전 operator BUY halt ON
→ 기존 주문·원장·서비스 상태 기록
→ 새 release 준비
→ service 정지
→ 안전한 SQLite snapshot
→ 새 release/current 전환
→ DB/schema/config 검증
→ service 시작
→ JH AUTO startup quarantine
→ 주문 복구
→ 계좌·원장 대조
→ service/Telegram/read-only smoke
→ 상태 확인
```

배포 과정에서는:

- `/auto start` 실행 금지
- `/resume` 실행 금지
- 임의 BUY 주문 금지
- 기존 operator `/halt` latch 삭제 금지
- UNKNOWN 주문을 성공/실패로 추정 금지

입니다.

---

## 7. 배포 후 BUY 상태 판정

### 아직 최초 시작승인 전

배포 후에도 반드시:

```text
launch_authorized = 0
현재 허용원금     = 0
실제 신규 BUY     = 차단
```

이어야 합니다.

### 이미 최초 시작승인 후

배포 직후에는 저수준 BUY halt와 startup quarantine이 먼저 켜집니다.

이후 자동복귀 여부는 **운영 프로그램이 fresh reconciliation과 안전검사를 통과한 뒤** 결정합니다. 배포 workflow가 halt를 미리 풀어주는 것이 아닙니다.

### 대표 `/halt` 상태

배포 전 `/halt` latch가 ON이었다면 새 release에서도 그대로 ON이어야 하며 자동복귀하면 안 됩니다.

---

## 8. DB와 rollback 원칙

### 새 service 시작 전 실패

외부 주문 부작용이 없음을 증명할 수 있으면 코드·service 설정·DB를 배포 직전 상태로 복구할 수 있습니다.

### 새 service 시작 후 실패

실제 주문 또는 위험축소 SELL이 발생했을 가능성을 먼저 고려합니다.

이 경우 과거 DB snapshot을 맹목적으로 덮어쓰지 않습니다.

```text
신규 BUY 차단
→ 실제 broker 상태 확인
→ 현재 DB 보존
→ reconciliation
→ 필요 시 이전 코드만 복구
```

순서를 우선합니다.

불명확한 주문이 하나라도 있으면 rollback 과정에서도 재주문하지 않습니다.

---

## 9. Toss 인증과 health-check

실거래 서비스가 실행 중인 동안 외부 health process가 별도 access token을 발급해 현재 runtime token을 방해하면 안 됩니다.

LIVE에서는:

- 일반 health: systemd / DB / config / clock / disk 중심
- authenticated Toss 확인: 서비스와 직렬화된 배포 smoke 또는 운영 프로그램 자체 조회경계

를 사용합니다.

Dry-run Toss smoke와 LIVE runtime의 인증경계를 같은 것으로 취급하지 않습니다.

---

## 10. 배포 후 Telegram smoke

JH AUTO 정상 runtime에서 다음은 조회성 운영 화면입니다.

- `/dashboard`
- `/today`
- `/auto`
- `/portfolio`
- `/account`
- `/order`
- `/errors`
- `/help`

특히 **JH AUTO의 `/today`는 수동 BUY approval을 생성하는 화면이 아니라 읽기 전용 자동운용 관찰화면**입니다.

배포 smoke에서는 다음을 확인합니다.

### 최초 시작 전

```text
최초 시작승인         미승인
현재 허용원금         $0
최고 평가액           시작 전
HWM75 현재 위험예산   시작 전
실제 신규 BUY         차단
```

그리고 `$50,000` 연구 기준값이 실거래 고정한도로 보이지 않아야 합니다.

### 시작 후 runtime

- 실제 base/ratio/target/effective principal 표시
- 실제 자동운용자산·수익률 표시
- HWM75 현재 위험예산 표시
- operator halt / quarantine / SAFE_MODE / open order 상태 표시

Telegram 화면 확인 자체를 `/auto start`나 자금변경 승인으로 대신하지 않습니다.

---

## 11. 재시작 검증

안전한 시점에 다음을 확인합니다.

- 두 번째 동일 live runtime 실행이 lock으로 거부됨
- service active
- startup quarantine 진입
- 주문/부분체결 복구
- 계좌·원장 대조
- SAFE_MODE 상태
- launch 미승인이면 BUY 계속 차단
- launch 승인 상태라면 clean proof 후 시스템 임시격리만 자동복귀 가능
- operator `/halt` latch는 자동복귀 금지

시장 세션 때문에 실제 restart 검증을 생략하면 `CURRENT_WORK.md`에 미검증 항목으로 남깁니다.

---

## 12. 완료 기준

배포 성공은 단순히 `service active`가 아닙니다.

다음을 함께 확인해야 합니다.

- 기대한 최신 `main` runtime 배포
- Quality/Security 및 필요한 Backtest 성공
- config/package/schema 일치
- DB 무결성·rollback 가능성 확인
- 계좌·원장 reconciliation 정상
- 주문 결과불명 상태 없음
- JH AUTO의 launch/halt/quarantine 상태가 배포 전 의도와 일치
- Telegram HWM/자금/성과 표시가 실제 AUTO 계약과 일치

그리고 마지막으로:

> **배포 성공은 `/auto start` 승인이 아닙니다.**
