# JH_HOLDINGS 개발 협업 워크플로

## 목적


### 전략 버전과 자동매매 버전 분리

JH_HOLDINGS는 `JDSS 3.2.2`와 `JH AUTO 1.0.0`을 별도 버전으로 관리합니다. 전략 수학 변경이 없으면 자동매매 기능 개발만으로 JDSS 버전을 올리지 않습니다. 반대로 전략 변경만으로 JH AUTO 버전을 올리지 않습니다.

자동운용 변경 PR은 최소한 `JH_AUTO_SPEC`, Telegram, `infra/SECURITY`, `LIVE_COMMISSIONING`, 관련 테스트를 함께 검토합니다. **배포 성공은 자동운용 시작승인이 아니며**, 최초 JH AUTO 배포 후 실제 BUY는 운영자의 Telegram 시작확인 전까지 계속 차단되어야 합니다.

Codex, ChatGPT와 IDE 작업환경이 GitHub를 공용 **최종 기준 저장소**로 사용해 **전략·기능·문서 변경을 충돌 없이 이어가고, 검증된 `main`만 Oracle에 배포**하기 위한 사람 기준 절차입니다.

영문 명령·도구 이름은 정확성을 위해 유지하지만, 각 단계의 목적과 운영자 행동은 한글로 먼저 설명합니다.

에이전트의 `작업 시작`·`작업 종료` 단축명령과 강제 안전규칙은 루트 [`../../AGENTS.md`](../../AGENTS.md)가 소유합니다. 이 문서는 그 체크리스트를 복제하지 않고 **역할, 변경 흐름, 완료 기준**만 설명합니다.

## 1. 작업 시작 시 읽는 순서

1. [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md) — 현재 production·배포·live·다음 작업
2. [`../README.md`](../README.md) — 변경 내용의 소유 문서 확인
3. `strategy.yaml` / 공식 사양 / 실제 구현 비교
4. 필요한 경우 [`../HISTORY.md`](../HISTORY.md)와 연구 PR·artifact 확인

현재 상태와 과거 결정을 한 문서에서 찾으려 하지 않습니다.

## 2. 환경별 역할

| 환경·주체 | 주 책임 | 할 수 있는 일 | 하지 않는 일 |
|---|---|---|---|
| 사용자 | 우선순위·전략 채택·배포·live 승인 | 요구사항 확정, 후보 채택, 배포 승인, Telegram BUY 최종 승인 | 배포 승인을 live BUY 잠금 해제로 자동 해석하지 않음 |
| Codex·로컬 IDE | 구현·디버깅·로컬 검증 | 코드 수정, 테스트, 작업트리 관리, 재현 | 사용자 미커밋 변경 덮어쓰기, 원격 secret 추정 |
| ChatGPT·GitHub 연결 | 원격 변경·PR·Actions·작업 종결 | 최신 원격 확인, 브랜치/PR, Actions 추적, 승인된 ChatOps, 배포 후 상태 확인 | secret 조회·복제, 로컬 파일을 보았다고 가정 |
| GitHub Actions | 공통 CI·연구 artifact·승인된 배포 | Ruff, pytest, Security, Backtest, dry-run/live-safe deploy, 외부 health check | 임의 branch·미검증 코드 운영 배포 |
| Oracle | 검증된 runtime 운영 | Telegram, 일일 분석, 주문·감시·reconciliation | 연구 후보탐색, 소스 직접 편집 |

## 3. 가장 중요한 경계

- GitHub 원격이 Source of Truth입니다.
- 기능 개발은 `main`에서 직접 하지 않습니다.
- 동일 branch를 여러 환경이 동시에 수정하지 않습니다.
- 환경이 바뀌기 전에는 commit + push로 재현 가능한 인계점을 만듭니다.
- PR CI가 끝나지 않았으면 결과를 추정하지 않습니다.
- merge와 deploy는 별개입니다.
- deploy와 BUY 잠금 해제도 별개입니다.
- 연구와 production 구현은 별도 branch/PR로 분리합니다.
- 실거래 배포는 **주문을 만들어내는 작업이 아니라 검증된 코드를 안전하게 교체하는 작업**이어야 합니다.

## 4. 표준 변경 흐름

```text
CURRENT_WORK + 최신 main 확인
  → 변경 유형 분류
  → 소유 문서·설정·구현 확인
  → 별도 branch
  → 최소 범위 변경
  → PR
  → 변경 유형별 검증
  → 실패 수정·재검증
  → 코드·설정·문서 일치 확인
  → merge
  → runtime 영향이 있으면 승인된 Oracle 배포
  → smoke·reconciliation·외부 health 확인
  → CURRENT_WORK를 짧은 현재상태로 마감
```

## 5. 변경 유형별 필수 동기화

| 변경 유형 | 같이 확인할 것 | 핵심 검증 |
|---|---|---|
| 전략 조건·지표·비중·자금공식 | `strategy.yaml`, FINAL_SPEC, STRATEGY_GUIDE, ONE_PAGE_REPORT | no-lookahead canonical backtest, OOS·비용 |
| 주문·승인·포지션·DB | FINAL_SPEC, TELEGRAM_BOT_GUIDE, SECURITY | 멱등성, 부분체결, restart, reconciliation, SAFE_MODE |
| Telegram 버튼·문구 | TELEGRAM_BOT_GUIDE, help/format/callback tests | 관리자 인증, TTL, stale, 4096자 |
| Toss API·네트워크 | SECURITY, adapter tests | timeout, HTTP/JSON, read retry와 write receipt 경계 |
| 배포·systemd | DEPLOYMENT, SECURITY | pinned host trust, BUY halt, snapshot, rollback, smoke |
| 연구 방법 | RESEARCH_PROTOCOL | baseline parity, selection firewall, robustness |
| 현재 SHA·배포·next step | CURRENT_WORK | 실제 GitHub/Oracle 상태 대조 |
| 문서-only 설명 | 소유 문서와 link map | 문서 계약·링크·중복 검사, runtime deploy 생략 |

## 6. 기능·버그 수정

1. 현상을 실제 코드·로그·테스트에서 확인
2. 가장 작은 재현 테스트 작성 또는 기존 테스트로 재현
3. 원인 계층에서 수정
4. 사용자 보이는 동작이 바뀌면 운영문서도 같은 작업 묶음에서 수정
5. CI 통과
6. runtime 영향이 있으면 승인된 배포
7. 배포 후 runtime smoke + 외부 health 확인

실패를 숨기기 위해 테스트 조건을 약화하거나 안전장치를 우회하지 않습니다.

### Toss 통신잡음 수정 원칙

- **GET/조회 전용** 요청은 네트워크·timeout·429·5xx 등 일시 오류에 한해 제한적 자동 재시도를 허용할 수 있습니다.
- 주문 제출·취소처럼 계좌 상태를 바꾸는 **쓰기 요청은 결과가 불명확하다고 자동 재시도하지 않습니다.**
- 읽기 화면의 표시 편의 때문에 실제 주문 가격 신선도·정합성 경계를 약화하지 않습니다.
- 예: 휴장 `/dashboard`는 최신 가용 가격을 표시할 수 있지만 실제 주문 계산의 300초 시세 신선도 제한은 유지합니다.

## 7. 전략 연구

연구는 production 변경이 아닙니다.

```text
production baseline 재현
  → 연구 질문·후보범위 고정
  → train/validation 선택
  → OOS 공개
  → 비용·neighborhood·rolling·bootstrap
  → KEEP / SHADOW / ADOPT CANDIDATE
```

세부 기준은 [`../research/RESEARCH_PROTOCOL.md`](../research/RESEARCH_PROTOCOL.md)를 따릅니다.

ADOPT CANDIDATE도 바로 production이 아닙니다. 사용자가 채택하면 **별도 구현 PR**에서 production 코드·설정·문서를 바꿉니다.

## 8. 문서-only 변경

문서 정리는 단순 맞춤법 수정과 계약 변경을 구분합니다.

### 설명만 개선

- 코드·전략 동작 변경 없음
- 현행 수치·링크·용어를 실제 계약과 대조
- 문서 fast path CI 사용 가능
- merge 후 Oracle 재배포하지 않음

### 계약을 새로 명시하거나 바꾸는 문서 변경

문서만 바꿨더라도 실제 코드와 다른 새 동작을 약속하면 문서-only가 아닙니다. 구현·테스트가 같이 필요합니다.

### 문서 PR의 핵심 원칙

- 한 내용의 소유 문서 하나
- `CURRENT_WORK`에 완료 상세를 무한 누적하지 않음
- 현재 상태와 역사 분리
- 내부 용어와 사용자 표현 구분
- 연구 후보를 production처럼 표현하지 않음
- 일회성 run ID는 현재 운영 확인에 필요한 최소 범위만 유지

## 9. 배포 흐름

runtime 영향이 있는 merge에 한해 승인된 배포 경로를 사용합니다.

### dry-run 운영 상태

실거래 commissioning 전 또는 dry-run 서버 갱신은 owner-only `[deploy-oracle-dry-run]` ChatOps를 사용합니다.

### LIVE-ARMED 운영 상태

실계좌가 commissioning된 뒤의 운영 코드 갱신은 owner-only **`[deploy-oracle-live-armed]`** ChatOps를 사용합니다.

LIVE-ARMED 배포는 다음 순서를 고정합니다.

```text
최신 main 재확인
  → 배포 전 CI/안전 테스트
  → pinned SSH trust
  → 신규 BUY 잠금 자동 ON
  → 활성 BUY 승인/대기 현금해제 의도 취소
  → 기존 live-release-check
  → 미체결·계좌/원장 정합성 확인
  → 새 release 설치·검증
  → 일관된 live DB snapshot
  → current 전환
  → service 재시작
  → Telegram 메뉴 / Toss read-only smoke
  → 외부 Oracle health check
```

핵심 규칙:

- 배포 시점에 `/resume`으로 BUY 잠금이 풀려 있어도 배포 전에 **BUY halt를 먼저 ON**으로 만듭니다.
- 이 안전화 단계는 **주문을 제출하지 않고 주문취소도 자동 실행하지 않습니다.**
- 이미 경계에 들어간 주문이나 미체결이 있으면 뒤의 release gate/reconciliation이 배포를 막아야 합니다.
- 배포 중 오류가 나면 BUY 잠금을 유지합니다.
- 배포가 성공해도 `/resume`을 자동 실행하지 않습니다.
- 실거래 원장·계좌 연결·환경설정을 보존합니다.
- 서비스 시작 이후 실제 주문 상태가 바뀔 수 있는 구간에서는 DB를 과거 snapshot으로 자동 되감지 않습니다.

배포 workflow는 실행 시점의 최신 `main`을 다시 확인하고 임의 ref를 받지 않습니다.

배포 완료 판정:

- 기대 기능 revision/SHA
- config/package/strategy 일치
- service active
- live commissioning 유지
- BUY halt 상태 확인
- DB quick check / reconciliation
- Toss read-only smoke
- 필요한 Telegram smoke
- 외부 Oracle health check
- rollback 가능성 및 실제 상태 보존 규칙

세부 절차는 [`DEPLOYMENT.md`](DEPLOYMENT.md)를 따릅니다.

## 10. 장애 수정 흐름

```text
운영 증상 확인
  → GitHub/Oracle 실제 상태 확인
  → 신규 BUY 위험 여부 판단
  → 필요하면 BUY halt / SAFE_MODE 유지
  → branch 재현
  → 수정 + regression test
  → PR CI
  → 승인된 복구 배포
  → runtime smoke
  → 외부 health 재검증
```

`service active`만 보고 장애가 해결됐다고 판단하지 않습니다.

## 11. PR Definition of Done

모든 PR:

- [ ] 의도하지 않은 변경 없음
- [ ] secret 없음
- [ ] 소유 문서·코드·설정이 일치
- [ ] 필요한 CI 성공
- [ ] 실패·미검증 항목을 숨기지 않음

전략 PR 추가:

- [ ] production baseline 재현
- [ ] OOS·비용·강건성 검증
- [ ] 연구 프로토콜 준수

주문/DB PR 추가:

- [ ] 중복주문·멱등성
- [ ] 부분체결·UNKNOWN
- [ ] restart/reconciliation
- [ ] SELL-first·SAFE_MODE
- [ ] batch/approval stale·TTL·동시성

배포 PR 추가:

- [ ] pinned SSH trust
- [ ] LIVE면 배포 전 BUY halt
- [ ] DB snapshot
- [ ] rollback / 실제상태 보존 경계
- [ ] dry-run 또는 live-safe smoke
- [ ] 외부 health check

문서 PR 추가:

- [ ] 단일 책임
- [ ] 중복 수치 최소화
- [ ] 최신 canonical 값 확인
- [ ] 링크 검사
- [ ] `CURRENT_WORK`가 짧은 상태판인지 확인

## 12. 인수인계 최소정보

다음 환경에 넘길 때 긴 대화요약보다 아래 사실을 남깁니다.

- branch와 마지막 commit
- 목적과 실제 변경
- 테스트·Actions 상태
- merge 여부
- runtime 배포 여부와 운영 SHA
- BUY halt / SAFE_MODE 상태
- 남은 오류·다음 작업
- 전략/config/live 계약 변경 여부

원본 결과는 PR·Actions·artifact에서 확인하고 Markdown에 실행로그를 복사하지 않습니다.
