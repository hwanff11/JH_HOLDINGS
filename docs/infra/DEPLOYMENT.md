# Oracle 배포·검증·롤백 가이드

이 문서는 **버전과 무관한 Oracle 배포 절차**만 소유합니다. 배포할 전략 계약은 [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md)와 [`../../strategy.yaml`](../../strategy.yaml), 현재 저장소·운영 서버 버전과 실거래 상태는 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)를 확인합니다.

핵심은 **검증된 최신 버전만 배포하고, 실패하면 코드와 DB를 함께 이전 정상상태로 복구하며, 배포를 실거래 승인으로 해석하지 않는 것**입니다.

완료된 일회성 migration, 오래된 SHA, Actions run ID는 이 절차 문서에 누적하지 않습니다.

## 1. 기본 원칙

1. Oracle에는 검증된 **최신 `main`**만 배포합니다.
2. 모의운용 배포, 최초 실거래 전환, 기존 실거래 운영 프로그램 갱신은 서로 다른 승인입니다.
3. 현재 계약이 forced dry-run이면 배포가 이를 완화할 수 없습니다.
4. 새 release를 먼저 준비·검증한 뒤 service downtime을 최소화합니다.
5. service stop 이후 실패하면 코드와 DB를 함께 이전 정상상태로 rollback할 수 있어야 합니다.
6. 문서-only commit처럼 runtime 영향이 없는 변경은 Oracle에 재배포하지 않습니다.

## 2. 서버 구조의 논리적 모델

실제 서버 절대경로·OS 사용자명·서비스 실명은 보호된 배포 설정에서 관리하고 공개 Markdown에 기록하지 않습니다.

논리적 구조:

```text
current                 → 현재 활성 release 심볼릭 링크
releases/<commit-sha>   → commit별 release
  .venv                 → release-local Python 환경
shared/
  data                   → SQLite·cache
  backups                → 배포 직전 DB snapshot
  logs                   → 운영 로그
  .env                   → secret·runtime environment
```

환경파일은 최소권한을 유지하고 systemd hardening과 `UMask=0077`을 유지합니다.

forced dry-run 계약에서는 최소한 다음을 확인합니다.

```dotenv
JDSS_TRADING_MODE=dry_run
JDSS_LIVE_CONFIRMATION=
```

그리고 `strategy.yaml`의 `portfolio.live_enabled=false`를 별도로 확인합니다.

## 3. SSH host trust

- `StrictHostKeyChecking=yes` 필수
- Oracle console 또는 기존 신뢰 관리자 PC 등 별도 경로로 host public key 확인
- 확인된 known_hosts 값을 승인된 GitHub Environment secret에 보관
- 새 runner가 `ssh-keyscan` 결과를 즉석 신뢰하지 않음
- `accept-new` 사용 금지
- host key가 바뀌었으면 원인 확인 전 배포 금지

## 4. 배포 전 게이트

배포 전에 다음을 확인합니다.

1. 원격 최신 `main`과 checkout SHA 일치
2. 병합된 변경의 필수 CI 성공
3. `jdss validate-config`
4. 필요한 Ruff / pytest / deployment contract test
5. strategy ID, config/package version 일치
6. HWM·onboarding·SGOV·live 잠금 등 핵심 설정 불변식
7. Environment secret·환경파일·DB·로그 권한
8. pinned SSH host trust
9. config version 변경 여부

**config version이 바뀌면 표준 deploy를 중단**하고 별도 migration plan·DB 호환성·rollback test가 필요합니다.

## 5. 배포 경로

### 모의운용 배포

로컬 관리 환경에서는 저장소의 `deploy.sh`를 사용합니다.

```bash
env -u GITHUB_TOKEN ./deploy.sh
```

GitHub 연결 환경에서는 owner-only **Deploy Oracle Dry Run** ChatOps를 사용합니다. 저장소 소유자가 제목을 `[deploy-oracle-dry-run]`으로 시작하는 Issue를 열면 workflow가 실행 시점의 최신 `main`을 checkout해 같은 `deploy.sh` 경로로 배포합니다.

임의 branch·임의 SHA를 입력받아 production runtime에 배포하지 않습니다.

### 기존 실거래 운영 프로그램 갱신

이미 실계좌 연결·매수 잠금 상태로 전환된 Oracle은 표준 `deploy.sh`를 사용하지 않습니다. 표준 배포는 의도적으로 모의운용을 강제하므로 현재 실계좌 연결을 해제하기 때문입니다.

기존 실거래 원장을 유지한 코드 갱신은 `deploy_live_armed.sh`와 소유자 전용 **Deploy Oracle Live Armed** 절차만 사용합니다. GitHub에서는 저장소 소유자가 제목을 `[deploy-oracle-live-armed]`로 시작하는 Issue를 열어 최신 `main`을 배포합니다.

이 경로는 다음 조건을 모두 만족하지 않으면 중단합니다.

- 기존 운영 모드가 실거래이고 정확한 실거래 확인값이 설정됨
- 기존 별도 실거래 원장과 준비 완료 표시가 존재함
- 신규 매수 잠금이 이미 설정됨
- 실제 계좌·원장 대조 정상
- 로컬·토스 미체결 주문과 활성 승인이 없음
- 기존 버전과 새 버전의 `strategy.yaml`이 같음. 단, 2026-09-01 실거래 준비에서 명시 승인된 `regular: false → true`만 장전·정규장·장후 지정가 주문 계약으로 한 번 허용
- DB 스키마 코드 변경이 없음

배포 후에도 실계좌 연결은 유지하지만 신규 매수 잠금은 자동으로 다시 설정합니다. `/resume`은 별도의 운영자 판단이며 배포 과정에서 실행하지 않습니다.

## 6. rollback-safe 배포 순서

```text
최신 main 확인
  → release directory 준비
  → release-local .venv 설치
  → requirements.lock 전이 의존성 제약 적용
  → config·focused deployment gate 검증
  → 기존 service/current/unit 상태 기록
  → service 정지
  → SQLite backup() snapshot
  → 새 unit/current atomic switch
  → init-db / validate-config
  → service 시작
  → forced dry-run / service / Toss read-only smoke
  → 성공 시 완료
```

service stop 이후 실패하면 자동 rollback:

1. 새 service 정지
2. 이전 `current` 복원
3. 이전 systemd unit 복원
4. 배포 직전 DB snapshot 복원
5. daemon reload
6. 이전 service 재시작
7. service active와 config/reconciliation 재확인

rollback까지 실패하면 정상으로 추정하지 않고 **신규 BUY 금지 + 수동 복구** 대상으로 취급합니다.

실거래 운영 프로그램 갱신은 실패 시점에 따라 DB 복구 원칙이 다릅니다.

- 새 서비스를 시작하기 전 실패: 외부 주문 부작용이 없으므로 코드·서비스 설정·DB를 배포 직전으로 복구
- 새 서비스를 시작한 뒤 실패: 자동 위험축소 매도가 발생했을 가능성이 있으므로 DB를 과거 snapshot으로 되돌리지 않고 실제 상태를 보존한 채 이전 코드만 복구

## 7. DB migration

표준 배포는 config/schema/전략세대 변경을 자동 해결하지 않습니다.

별도 migration PR에서 최소한 다음을 증명합니다.

- 복구 가능한 SQLite snapshot
- 기존 schema·strategy generation 확인
- 열린 주문·부분체결·UNKNOWN 확인
- legacy state 변환 규칙
- 기존 DB 호환성 테스트
- 실패 시 코드와 DB 동시 rollback
- 실제 거래원장 자동 삭제·초기화 금지

완료된 일회성 migration script/workflow는 canonical 배포 표면에서 제거하고 대표 결정만 [`../HISTORY.md`](../HISTORY.md)에 요약합니다.

## 8. forced dry-run과 실제 Toss 경계

배포 smoke는 다음 두 경계를 분리합니다.

### forced dry-run

- SQLite JDSS 원장
- 모의 broker 보유·주문
- dry-run reconciliation

### 실제 Toss read-only

- 인증 확인
- 계좌·시세·시장상태 조회
- `toss-smoke`

read-only smoke가 성공해도 실제 Toss 주문이 검증되거나 dry-run 보유가 실제 계좌와 일치했다는 뜻이 아닙니다.

실거래에서는 토큰 발급 자체도 운영경계입니다.

- client credentials로 새 access token을 발급하면 같은 credentials의 기존 운영 토큰이 무효화될 수 있으므로 **실거래 서비스가 실행 중인 동안 외부 health process가 `toss-smoke`를 실행하지 않습니다.**
- LIVE authenticated smoke는 서비스 정지·기동과 직렬화된 배포 구간 또는 운영 프로그램 자체의 조회경계에서만 수행합니다.
- 정기 외부 health는 LIVE에서 SSH/systemd/SQLite/clock/disk/config를 확인하고 Toss 토큰을 새로 발급하지 않습니다.
- dry-run은 별도 live runtime token을 보호할 필요가 없으므로 기존 `toss-smoke`를 유지합니다.

## 9. 배포 후 검증

### source/runtime

- 활성 `current`가 기대 기능 SHA인지
- release-local `jdss` / `jdss-bot` 실행 가능
- strategy/config/package 일치
- config validation 성공

### 안전 잠금

- forced dry-run 또는 승인된 live commissioning 상태
- live면 신규 BUY 잠금 상태 확인
- `portfolio.live_enabled=false`
- application live hard lock

### service·원장

- service active
- startup error 없음
- SQLite schema/init 호환성
- 현재 모드에 맞는 broker/SQLite reconciliation
- SAFE_MODE 상태 확인

### 외부 read-only

- dry-run: Toss 인증·시세·시장상태 smoke
- live: 외부 health는 독립 Toss token을 발급하지 않고 service/DB/config 상태를 확인
- live authenticated Toss 확인이 필요하면 배포의 직렬화된 smoke 또는 운영 프로그램의 조회 결과를 사용
- Telegram bot identity/outbound smoke

### Telegram 운영 화면

배포 직후 자동 smoke는 **상태를 바꾸지 않는 read-only 확인을 우선**합니다.

- `/ping`
- `/help`
- `/portfolio`
- `/account`
- `/order`
- `/errors`

`/today`는 BUY 후보가 준비돼 있으면 review/execution approval을 생성할 수 있으므로 단순 read-only smoke 명령으로 취급하지 않습니다. `주문 없음 / 대기 / SELL 진행 / 다건 BUY` 화면을 실제로 검증할 필요가 있을 때만 **운영자가 의도적으로 forced dry-run 승인 흐름을 점검하는 별도 시나리오**에서 사용하고, 생성된 batch는 실행·취소·만료 상태까지 확인합니다.

`/onboarding` 역시 단계 개방 callback을 누르지 않는 조회 범위에서만 배포 후 화면을 확인합니다.

## 10. 재시작 검증

시장 세션이 안전할 때만 systemd restart/recovery를 실제 확인합니다.

- restart 후 service active
- config validation
- 저장 target_qty·열린 주문 복구
- 현재 모드에 맞는 reconciliation
- SAFE_MODE 확인

시장 세션 때문에 restart를 생략했다면 실패를 숨기지 않고 `CURRENT_WORK.md`의 다음 검증 항목으로 남깁니다.

## 11. 롤백 후 운영 확인

1. previous release 활성 확인
2. previous unit/service active
3. 복원 DB와 config/schema 호환성
4. 불명확한 주문을 수동 성공처리하지 않음
5. reconciliation
6. dry-run은 Toss read-only smoke, live는 별도 token issuer를 만들지 않는 직렬화된 Toss 확인
7. Telegram 상태 확인

## 12. GitHub Actions와 변경통제

- `main` direct push/force push/delete 보호
- 안정적인 required check 이름 유지
- 문서-only PR은 runtime 영향이 없으면 fast path
- 전략·주문·DB 변경은 관련 full gate 수행
- 배포 workflow는 승인된 Environment secret만 사용
- 오래된 버전 전용 workflow를 남겨 재실행 표면을 늘리지 않음

## 13. 완료 기준

배포는 `service active` 하나만으로 성공이 아닙니다.

다음이 함께 맞아야 합니다.

- 기대 기능 source가 배포됨
- 설정·package·전략 계약 일치
- DB와 rollback 가능성 확인
- forced dry-run/live lock 유지
- 원장 정합성 확인
- 현재 모드에 맞는 Toss read-only 경계 확인
- 필요한 Telegram smoke 확인

그리고 **배포 성공은 live 활성화 승인이 아닙니다.**
