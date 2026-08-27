# JH_HOLDINGS 개발 협업 워크플로

## 목적

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
| 사용자 | 우선순위·전략 채택·배포·live 승인 | 요구사항 확정, 후보 채택, 배포 승인, Telegram BUY 최종 승인 | 배포 승인을 live 승인으로 자동 해석하지 않음 |
| Codex·로컬 IDE | 구현·디버깅·로컬 검증 | 코드 수정, 테스트, 작업트리 관리, 재현 | 사용자 미커밋 변경 덮어쓰기, 원격 secret 추정 |
| ChatGPT·GitHub 연결 | 원격 변경·PR·Actions·작업 종결 | 최신 원격 확인, 브랜치/PR, Actions 추적, 승인된 ChatOps | secret 조회·복제, 로컬 파일을 보았다고 가정 |
| GitHub Actions | 공통 CI·연구 artifact·승인된 배포 | Ruff, pytest, Security, Backtest, forced dry-run deploy | 임의 branch·미검증 코드 운영 배포 |
| Oracle | 검증된 runtime 운영 | Telegram, 일일 분석, 주문·감시·reconciliation | 연구 후보탐색, 소스 직접 편집 |

## 3. 가장 중요한 경계

- GitHub 원격이 Source of Truth입니다.
- 기능 개발은 `main`에서 직접 하지 않습니다.
- 동일 branch를 여러 환경이 동시에 수정하지 않습니다.
- 환경이 바뀌기 전에는 commit + push로 재현 가능한 인계점을 만듭니다.
- PR CI가 끝나지 않았으면 결과를 추정하지 않습니다.
- merge와 deploy는 별개입니다.
- deploy와 live 활성화도 별개입니다.
- 연구와 production 구현은 별도 branch/PR로 분리합니다.

## 4. 표준 변경 흐름

```text
CURRENT_WORK + 최신 main 확인
  → 변경 유형 분류
  → 소유 문서·설정·구현 확인
  → 별도 branch
  → 최소 범위 변경
  → Draft PR
  → 변경 유형별 검증
  → 실패 수정·재검증
  → 코드·설정·문서 일치 확인
  → merge
  → runtime 영향이 있으면 승인된 Oracle forced dry-run 배포
  → smoke·reconciliation 확인
  → CURRENT_WORK를 짧은 현재상태로 마감
```

## 5. 변경 유형별 필수 동기화

| 변경 유형 | 같이 확인할 것 | 핵심 검증 |
|---|---|---|
| 전략 조건·지표·비중·자금공식 | `strategy.yaml`, FINAL_SPEC, STRATEGY_GUIDE, ONE_PAGE_REPORT | no-lookahead canonical backtest, OOS·비용 |
| 주문·승인·포지션·DB | FINAL_SPEC, TELEGRAM_BOT_GUIDE, SECURITY | 멱등성, 부분체결, restart, reconciliation, SAFE_MODE |
| Telegram 버튼·문구 | TELEGRAM_BOT_GUIDE, help/format/callback tests | 관리자 인증, TTL, stale, 4096자 |
| Toss API·네트워크 | SECURITY, adapter tests | timeout, HTTP/JSON, receipt boundary |
| 배포·systemd | DEPLOYMENT, SECURITY | pinned host trust, snapshot, rollback, smoke |
| 연구 방법 | RESEARCH_PROTOCOL | baseline parity, selection firewall, robustness |
| 현재 SHA·배포·next step | CURRENT_WORK | 실제 GitHub/Oracle 상태 대조 |
| 문서-only 설명 | 소유 문서와 link map | 문서 계약·링크·중복 검사, runtime deploy 생략 |

## 6. 기능·버그 수정

1. 현상을 실제 코드·로그·테스트에서 확인
2. 가장 작은 재현 테스트 작성 또는 기존 테스트로 재현
3. 원인 계층에서 수정
4. 사용자 보이는 동작이 바뀌면 운영문서도 같은 PR에서 수정
5. CI 통과
6. 필요 시 배포
7. 배포 후 runtime smoke

실패를 숨기기 위해 테스트 조건을 약화하거나 안전장치를 우회하지 않습니다.

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
- `CURRENT_WORK`에 완료 상세를 누적하지 않음
- 현재 상태와 역사 분리
- 내부 용어와 사용자 표현 구분
- 연구 후보를 production처럼 표현하지 않음
- 일회성 run ID를 장기 문서에 쌓지 않음

## 9. 배포 흐름

runtime 영향이 있는 merge에 한해 승인된 배포 경로를 사용합니다.

ChatGPT·GitHub 연결 환경에서는 owner-only `[deploy-oracle-dry-run]` ChatOps가 표준입니다.

배포 workflow는 실행 시점의 최신 `main`을 다시 확인하고 임의 ref를 받지 않습니다.

배포 완료 판정:

- 기대 기능 revision
- config/package/strategy 일치
- service active
- forced dry-run/live lock
- DB/reconciliation
- Toss read-only smoke
- 필요한 Telegram smoke
- rollback 가능성

세부 절차는 [`DEPLOYMENT.md`](DEPLOYMENT.md)를 따릅니다.

## 10. 장애 수정 흐름

```text
운영 증상 확인
  → GitHub/Oracle 실제 상태 확인
  → 신규 BUY 위험 여부 판단
  → 필요하면 SAFE_MODE 유지
  → 로컬/branch 재현
  → 수정 + regression test
  → PR CI
  → 승인된 복구 배포
  → runtime 재검증
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
- [ ] DB snapshot
- [ ] automatic rollback
- [ ] forced dry-run smoke

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
- runtime 배포 여부와 기능 revision
- 남은 오류·다음 작업
- 전략/config/live 계약 변경 여부

원본 결과는 PR·Actions·artifact에서 확인하고 Markdown에 실행로그를 복사하지 않습니다.
