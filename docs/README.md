# JH_HOLDINGS 문서 체계

이 파일은 저장소 문서의 **지도, 소유권, 우선순위, 갱신 규칙**을 정의합니다. 전략 내용 자체나 현재 배포 상태를 여기 복제하지 않습니다.

## 1. 독자별 빠른 경로

### 운영자

1. [`../CURRENT_WORK.md`](../CURRENT_WORK.md) — 지금 어떤 버전이 어디에 배포됐는지 확인
2. [`ONE_PAGE_REPORT.md`](ONE_PAGE_REPORT.md) — 전략을 짧게 복습
3. [`TELEGRAM_BOT_GUIDE.md`](TELEGRAM_BOT_GUIDE.md) — 실제 버튼·명령·오류 대응
4. 필요할 때 [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md) — 정확한 주문·자금 계약 확인

### 개발자·에이전트

1. [`../AGENTS.md`](../AGENTS.md)
2. [`../CURRENT_WORK.md`](../CURRENT_WORK.md)
3. [`infra/DEVELOPMENT_WORKFLOW.md`](infra/DEVELOPMENT_WORKFLOW.md)
4. 변경 대상에 따라 공식 사양·보안·배포 문서 확인

### 전략 연구자

1. [`../CURRENT_WORK.md`](../CURRENT_WORK.md)에서 production 기준 확인
2. [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md)와 `strategy.yaml`로 baseline 계약 확인
3. [`research/RESEARCH_PROTOCOL.md`](research/RESEARCH_PROTOCOL.md)로 연구 설계
4. [`HISTORY.md`](HISTORY.md)에서 이미 기각·보류한 방향 확인

## 2. 문서별 단일 책임

| 문서 | 이 문서가 소유하는 것 | 이 문서에 두지 않는 것 |
|---|---|---|
| [`../CURRENT_WORK.md`](../CURRENT_WORK.md) | 현재 릴리즈, source/runtime 동기화, live 상태, 최근 완료, 다음 작업 | 긴 설계 설명, 과거 실행 일지, 반복되는 안전 규칙 |
| [`ONE_PAGE_REPORT.md`](ONE_PAGE_REPORT.md) | 비전문가용 한 장 요약, 핵심 지표의 쉬운 해석 | 세부 예외조건, API·DB 계약 |
| [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md) | 전략의 쉬운 상세 설명, 예시, 흐름, 승인된 기준 백테스트 | 구현 세부 예외의 규범 문구 |
| [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md) | production이 따라야 하는 전략·자금·주문·백테스트 규범 계약 | 현재 SHA·서버 상태, 연구 중간결과 |
| [`TELEGRAM_BOT_GUIDE.md`](TELEGRAM_BOT_GUIDE.md) | 운영자가 보는 명령, 버튼, 상태, 주문 흐름, 장애 대응 | 전략 수학의 중복 설명 |
| [`HISTORY.md`](HISTORY.md) | 대표 릴리즈와 채택·기각·SHADOW 결정의 역사 색인 | 현재판 상세 계약, 일회성 로그 전체 |
| [`infra/DEVELOPMENT_WORKFLOW.md`](infra/DEVELOPMENT_WORKFLOW.md) | 사람 기준 브랜치·PR·CI·인수인계 흐름 | 에이전트 단축명령의 세부 체크리스트 |
| [`infra/DEPLOYMENT.md`](infra/DEPLOYMENT.md) | Oracle 배포·검증·rollback 절차 | 현재 배포 SHA, 일회성 run ID |
| [`infra/SECURITY.md`](infra/SECURITY.md) | 인증·주문·DB·네트워크·배포의 안전 불변식 | 현재 시장 판단·백테스트 성과 |
| [`research/RESEARCH_PROTOCOL.md`](research/RESEARCH_PROTOCOL.md) | 연구 설계·선택·OOS·강건성·승격 규칙 | 개별 후보의 긴 결과표 |
| [`../AGENTS.md`](../AGENTS.md) | Codex·ChatGPT·IDE가 반드시 지킬 실행 규칙 | 전략 설명의 중복 |
| [`../SECURITY.md`](../SECURITY.md) | GitHub에서 찾기 쉬운 보안 진입점 | 상세 보안 구현 계약 |

## 3. 서로 내용이 다를 때

문서가 서로 충돌하면 **내용의 종류에 따라** 판정합니다. 하나의 전역 우선순위를 모든 문제에 기계적으로 적용하지 않습니다.

| 확인하려는 사실 | 기준 |
|---|---|
| 현재 릴리즈·배포·live 상태 | `CURRENT_WORK.md` + 실제 GitHub/Oracle 상태 |
| 실행 숫자·파라미터 | `strategy.yaml` |
| 전략·자금·주문 규범 | `JDSS_FINAL_SPEC.md` |
| 실제 프로그램 동작 | `src/jd_holdings/` + 테스트 |
| Telegram 사용법 | `TELEGRAM_BOT_GUIDE.md` + 실제 callback/help 테스트 |
| 보안 불변식 | `infra/SECURITY.md` + 코드·테스트 |
| 과거 채택·기각 이유 | `HISTORY.md` + 당시 PR/artifact |

`strategy.yaml`, 공식 사양, 구현 또는 테스트가 서로 다르면 임의로 하나를 정답으로 만들어 맞추지 않습니다. **불일치 자체를 결함으로 보고 변경 범위를 먼저 확정**합니다.

## 4. 문서 갱신 트리거

| 변경 | 반드시 함께 확인할 문서 |
|---|---|
| 전략 규칙·비중·지표·자금공식 | `JDSS_FINAL_SPEC`, `STRATEGY_GUIDE`, `ONE_PAGE_REPORT`, `strategy.yaml` |
| BUY/SELL·승인·부분체결·reconciliation | `JDSS_FINAL_SPEC`, `TELEGRAM_BOT_GUIDE`, `infra/SECURITY` |
| Telegram 버튼·명령·문구 | `TELEGRAM_BOT_GUIDE`, 도움말·포맷 테스트 |
| 배포·systemd·rollback | `infra/DEPLOYMENT`, `infra/SECURITY`, 배포 계약 테스트 |
| 연구 방법·승격 기준 | `research/RESEARCH_PROTOCOL` |
| 현재 브랜치·배포·다음 작업 | `CURRENT_WORK.md`만 갱신 |
| 대표 릴리즈·기각·SHADOW 결론 | `HISTORY.md`에 짧게 추가 |

## 5. 수명주기

1. 현행 문서의 파일명은 고정합니다. `FINAL_v4`, `2026-08`, `NEW_STRATEGY` 같은 복사본을 만들지 않습니다.
2. 새 릴리즈에서는 `strategy.yaml`, 공식 사양, 전략 가이드, 한 장 보고서를 **같은 구현 PR에서 제자리 갱신**합니다.
3. `CURRENT_WORK.md`는 append-only 일지가 아니라 **짧은 롤링 상태판**입니다. 완료된 상세 설계는 소유 문서로 이동하고 현재 필요한 상태만 남깁니다.
4. `HISTORY.md`만 과거 결정을 append-only로 보존합니다. 상세 원본은 Git tag, 병합 PR, 연구 PR, Actions artifact에서 복구합니다.
5. 미채택 연구의 일회성 스크립트·대형 결과표·차트는 `main`에 누적하지 않습니다.
6. 완료된 일회성 migration·배포 식별자는 현행 절차 문서에 남기지 않습니다.
7. 같은 숫자나 계약을 여러 문서가 소유하지 않습니다. 요약 문서에 반복해야 한다면 **출처 링크와 ‘요약값’임을 명확히 표시**합니다.

## 6. 공개 Markdown 안전 규칙

공개 문서에는 다음을 기록하지 않습니다.

- API 키·토큰·계좌번호·인증 헤더
- 서버 절대경로·OS 사용자명·서비스 실명·host 식별자
- backup/snapshot의 실제 파일명
- 일회성 Actions run ID를 상태판에 장기 보존

현재 source/runtime revision처럼 운영에 필요한 공개 상태는 `CURRENT_WORK.md`에 최소한으로 둘 수 있지만, 오래된 값은 계속 누적하지 않습니다.

## 7. 문서 품질 체크리스트

문서 PR을 마감하기 전에 다음을 확인합니다.

- [ ] 이 내용의 **소유 문서가 하나**인지
- [ ] 사용자용 표현과 내부 구현용 용어가 구분되는지
- [ ] 현재 수치가 `strategy.yaml`·canonical backtest와 맞는지
- [ ] live/dry-run 경계를 오해할 표현이 없는지
- [ ] BUY/SELL 자동화 범위를 실제 코드보다 넓게 표현하지 않았는지
- [ ] 연구 후보를 production처럼 표현하지 않았는지
- [ ] 오래된 SHA·run ID·완료된 작업을 `CURRENT_WORK`에 누적하지 않았는지
- [ ] 링크가 현행 파일을 가리키는지
- [ ] 문서-only 변경이면 runtime을 불필요하게 재배포하지 않는지

이 규칙은 문서 수를 늘리는 것이 아니라 **읽을 문서를 줄이고, 각 문서의 신뢰도를 높이기 위한 것**입니다.
