# JH_HOLDINGS 개발 협업 워크플로

이 문서는 **사람·ChatGPT·Codex/IDE·GitHub Actions·Oracle이 어떤 역할로 변경을 이어가는지와 PR 완료기준**을 설명합니다.

에이전트가 반드시 지켜야 할 짧은 강제규칙은 [`../../AGENTS.md`](../../AGENTS.md), 현재 운영상태는 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)가 소유합니다. 이 문서는 두 내용을 반복하지 않습니다.

## 1. 작업 시작 시 읽는 순서

1. [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md) — 현재 릴리즈·Oracle·실거래 잠금·다음 행동
2. [`../../README.md`](../../README.md) — 문서 소유권/SSOT 확인
3. 변경 대상의 공식 계약·설정·실제 구현 확인
4. 필요한 경우 [`../HISTORY.md`](../HISTORY.md)와 연구 PR·artifact 확인

현재 상태와 과거 결정을 한 문서에서 찾으려 하지 않습니다.

## 2. 환경별 역할

| 환경·주체 | 주 책임 | 할 수 있는 일 | 하지 않는 일 |
|---|---|---|---|
| 운영자 | 우선순위·전략 채택·배포·실거래 승인 | 요구사항 확정, 후보 채택, 배포 승인, Telegram 최초 시작·자금변경·매수재개 승인 | 배포승인을 최초 BUY 승인으로 자동 해석하지 않음 |
| Codex·로컬 IDE | 구현·디버깅·로컬 검증 | 코드 수정, 테스트, 작업트리 관리, 재현 | 사용자 미커밋 변경 덮어쓰기, secret 추정 |
| ChatGPT·GitHub 연결 | 원격 변경·PR·CI 추적·작업 종결 | 최신 원격 확인, branch/PR, Actions 확인, 문서/코드 변경, 승인된 배포 후 상태 확인 | secret 조회·복제, 로컬 파일을 보았다고 가정 |
| GitHub Actions | 공통 CI·연구 artifact·승인된 배포 | Ruff, pytest, Security, Backtest, dry-run/live-safe deploy | 임의 branch·미검증 코드 운영배포 |
| Oracle | 검증된 runtime 운영 | Telegram, 일일분석, 주문·감시·계좌 대조 | 연구 후보탐색, source 직접편집 |

## 3. 가장 중요한 경계

- GitHub 원격이 공용 최종 기준 저장소입니다.
- 기능개발은 보호된 `main`에 직접 하지 않습니다.
- 동일 branch를 여러 환경이 동시에 수정하지 않습니다.
- 환경이 바뀌기 전 commit + push로 재현 가능한 인계점을 만듭니다.
- PR CI가 끝나지 않았으면 결과를 추정하지 않습니다.
- merge와 deploy는 별개입니다.
- deploy와 `/auto start`/`/resume`도 별개입니다.
- 연구와 production 구현은 별도 branch/PR로 분리합니다.

## 4. 표준 변경 흐름

```text
CURRENT_WORK + 최신 main 확인
→ 변경 유형 분류
→ 소유 문서·설정·구현·테스트 확인
→ 별도 branch
→ 최소 범위 변경
→ 로컬/정적 검증
→ PR
→ 필수 CI
→ 실패 수정·재검증
→ 문서·설정·구현·테스트 일치 확인
→ merge
→ runtime 영향이 있으면 승인된 Oracle 배포
→ smoke·계좌대조·외부 health 확인
→ CURRENT_WORK를 짧은 현재상태로 마감
```

## 5. 변경 유형별 필수 동기화

| 변경 유형 | 같이 확인할 것 | 핵심 검증 |
|---|---|---|
| JDSS 전략 조건·지표·비중 | `strategy.yaml`, `JDSS_FINAL_SPEC.md`, `STRATEGY_GUIDE.md` | no-lookahead canonical backtest, OOS·비용 |
| JH AUTO 자금·시작·성과 | `JH_AUTO_SPEC.md`, `TELEGRAM_BOT_GUIDE.md`, `SECURITY.md` | 상태원자성, HWM, 재시작, 계좌대조 |
| 주문·승인·포지션·DB | `JH_AUTO_SPEC.md`, `SECURITY.md`, 관련 테스트 | 멱등성, 부분체결, UNKNOWN, restart, SAFE_MODE |
| Telegram 버튼·문구 | `TELEGRAM_BOT_GUIDE.md` | 관리자 인증, TTL/revision, stale, 4,096자 |
| Toss API·네트워크 | `SECURITY.md`, adapter tests | timeout, read retry/write receipt 경계 |
| 배포·실계좌 연결·systemd | `DEPLOYMENT.md`, `SECURITY.md` | pinned host trust, BUY halt, snapshot, rollback, smoke |
| 연구 방법 | `../research/RESEARCH_PROTOCOL.md` | baseline parity, selection firewall, robustness |
| 현재 SHA·배포·다음 행동 | `CURRENT_WORK.md` | GitHub/Oracle 실제 상태 대조 |
| 문서-only 설명 | `../../README.md`의 SSOT 표 + 관련 소유문서 | 링크·중복·금지식별자 검사, runtime deploy 생략 |

## 6. 기능·버그 수정

1. 실제 코드·로그·테스트에서 현상을 확인합니다.
2. 가장 작은 재현 테스트 또는 기존 회귀테스트로 재현합니다.
3. 원인 계층에서 수정합니다.
4. 사용자 동작이 바뀌면 운영문서도 같은 작업에서 갱신합니다.
5. 관련 정적검사·pytest·필요한 backtest를 통과합니다.
6. PR CI를 확인합니다.
7. runtime 영향이 있으면 승인된 배포 후 smoke·계좌대조를 확인합니다.

실패를 숨기기 위해 테스트 조건을 약화하거나 안전경계를 우회하지 않습니다.

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

세부 기준은 [`../research/RESEARCH_PROTOCOL.md`](../research/RESEARCH_PROTOCOL.md)를 따릅니다. ADOPT CANDIDATE도 자동으로 production이 되지 않으며 운영자가 채택한 뒤 별도 구현 PR에서 설정·코드·문서를 바꿉니다.

## 8. 문서-only 변경

### 설명·구조만 개선

- 코드·전략·runtime 동작 변경 없음
- 현행 수치·링크·용어를 실제 계약과 대조
- 문서 계약·링크 테스트 사용
- merge 후 Oracle runtime 재배포하지 않음

### 문서가 새 계약을 만들거나 변경

실제 코드와 다른 새 동작을 문서가 약속하면 문서-only가 아닙니다. 구현·테스트와 함께 변경해야 합니다.

### 문서 PR 핵심 원칙

- 한 규칙의 소유 문서 하나
- `CURRENT_WORK`에 완료 상세를 누적하지 않음
- 현재 상태와 역사 분리
- 운영자 가이드와 공식 사양의 책임 분리
- 연구 후보를 production처럼 표현하지 않음
- 삭제 문서의 고유 계약을 소유문서로 이관한 뒤 삭제
- 모든 상대링크와 문서 계약 테스트 통과

## 9. 배포 흐름

runtime 영향이 있는 merge에만 승인된 배포경로를 사용합니다.

### DRY-RUN

실거래 commissioning 전 또는 모의운용 runtime은 표준 dry-run 배포경로를 사용합니다.

### LIVE-ARMED / JH AUTO

실계좌가 연결된 뒤 운영코드 갱신은 [`DEPLOYMENT.md`](DEPLOYMENT.md)의 live-safe 경로를 따릅니다.

핵심:

- 최신 검증 `main`만 배포
- 배포 전 신규 BUY 안전화
- 기존 실거래 DB·실계좌 연결·운영자 halt 보존
- startup quarantine에서 주문복구와 계좌대조
- 배포 중 `/auto start`, `/resume`, 자금변경 금지
- 배포 성공을 BUY 재개로 해석하지 않음

## 10. 장애 수정 흐름

```text
운영 증상 확인
→ GitHub/Oracle 실제 상태 확인
→ 신규 BUY 위험 여부 판단
→ 필요하면 halt/quarantine/SAFE_MODE 유지
→ branch에서 재현
→ 수정 + regression test
→ PR CI
→ 승인된 복구 배포
→ runtime smoke
→ 계좌·원장 대조
→ 외부 health 재검증
```

`service active`만 보고 장애가 해결됐다고 판단하지 않습니다.

## 11. PR Definition of Done

모든 PR:

- [ ] 의도하지 않은 변경 없음
- [ ] secret·운영식별자 노출 없음
- [ ] 소유 문서·설정·구현·테스트 일치
- [ ] 필요한 CI 성공
- [ ] 실패·미검증 항목을 숨기지 않음

전략 PR 추가:

- [ ] production baseline 재현
- [ ] no-lookahead
- [ ] OOS·비용·강건성 검증
- [ ] 연구 프로토콜 준수

주문/DB PR 추가:

- [ ] 중복주문·멱등성
- [ ] 부분체결·UNKNOWN
- [ ] restart/reconciliation
- [ ] SELL-first·SAFE_MODE
- [ ] 자금·시작 상태 원자성

배포 PR 추가:

- [ ] pinned SSH trust
- [ ] pre-deploy BUY safety
- [ ] DB snapshot/rollback 경계
- [ ] Telegram·Toss read-only smoke
- [ ] post-deploy 계좌대조

문서 PR 추가:

- [ ] 삭제·병합된 문서의 고유 규칙 이관 확인
- [ ] 상대링크 전수검사
- [ ] SSOT 중복검사
- [ ] 문서-only면 Oracle 재배포 생략
