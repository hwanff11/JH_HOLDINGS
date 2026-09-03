# JH_HOLDINGS 문서 체계

이 파일은 저장소 문서의 **지도, 소유권, 우선순위, 갱신 규칙**을 정의합니다. 현재 JH_HOLDINGS는 투자판단과 자동실행을 분리합니다.

- **투자전략:** JDSS 3.2.2
- **자동매매 시스템:** JH AUTO 1.0.0

전략 수학을 바꾸는 일과 실제 자금·자동주문 시스템을 바꾸는 일을 같은 버전이나 같은 문서에서 섞지 않습니다.

## 문서 표현 원칙

- 운영자와 비개발자가 읽는 설명은 **한글을 먼저** 씁니다.
- 영문 기술용어가 꼭 필요하면 처음 한 번만 `한글 설명(영문)` 형태로 씁니다.
- 코드명, 설정 키, 데이터베이스 값과 실제 명령은 오작동을 막기 위해 원문을 유지합니다.
- 같은 영문 약어를 여러 문서에서 반복 설명하지 않고 아래 표현으로 통일합니다.

| 내부 용어 | 문서에서 우선하는 표현 |
|---|---|
| production | 실제 운영판 |
| runtime | 운영 서버 프로그램 |
| live | 실거래 |
| dry-run | 모의운용 |
| BUY / SELL | 매수 / 매도 |
| SAFE_MODE | 안전정지 |
| reconciliation | 계좌·원장 대조 |
| preflight | 사전점검 |
| fail-closed | 확인 불가 시 차단 |
| broker | 증권사 또는 토스 |
| deployment / rollback | 배포 / 이전 버전 복구 |
| snapshot / smoke test | 백업 사본 / 기본 작동 확인 |
| batch / callback | 주문 묶음 / 버튼 응답 |
| onboarding | 첫 자금투입 단계 |
| delegated capital | 자동운용 위임원금 |
| quarantine | 시스템 임시격리 |

## 1. 가장 먼저 구분할 두 사양

### 투자전략

[`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md)

- 시장판단
- QQQ/TQQQ/SOXL 목표비중
- RS6M
- 0.5/1.0/1.25/1.5배 노출
- 5% 추가 레버리지 판단
- HWM75 투자규칙
- 백테스트 기준

### 자동매매

[`JH_AUTO_SPEC.md`](JH_AUTO_SPEC.md)

- 운용 기준자금
- 자동운용비율
- 현재 허용원금
- 자금 증액·감액
- 자동승인·자동주문
- 최초 시작승인
- 대표 긴급정지 / 시스템 임시격리
- 자동운용 성과·수익률
- Telegram 자동운용 통제

**JDSS가 무엇을 살지 결정하고, JH AUTO가 얼마를 어떤 안전조건으로 실제 실행할지 결정합니다.**

## 2. 독자별 빠른 경로

### 운영자

1. [`../CURRENT_WORK.md`](../CURRENT_WORK.md) — 지금 실제 운영 상태
2. [`ONE_PAGE_REPORT.md`](ONE_PAGE_REPORT.md) — JDSS 전략 한 장 요약
3. [`JH_AUTO_SPEC.md`](JH_AUTO_SPEC.md) — 자동운용 자금·시작·안전계약
4. [`TELEGRAM_BOT_GUIDE.md`](TELEGRAM_BOT_GUIDE.md) — 실제 Telegram 사용법
5. 필요할 때 [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md) — 정확한 전략 규칙

### 개발자·에이전트

1. [`../AGENTS.md`](../AGENTS.md)
2. [`../CURRENT_WORK.md`](../CURRENT_WORK.md)
3. [`infra/DEVELOPMENT_WORKFLOW.md`](infra/DEVELOPMENT_WORKFLOW.md)
4. 전략 변경이면 `JDSS_FINAL_SPEC`, 자동운용 변경이면 `JH_AUTO_SPEC`
5. 주문·DB·배포 변경이면 `infra/SECURITY`, `infra/DEPLOYMENT`

### 전략 연구자

1. [`../CURRENT_WORK.md`](../CURRENT_WORK.md)에서 실제 운영판 기준 확인
2. [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md)와 `strategy.yaml`로 기준 전략 확인
3. [`research/RESEARCH_PROTOCOL.md`](research/RESEARCH_PROTOCOL.md)로 연구 설계
4. [`HISTORY.md`](HISTORY.md)에서 이미 기각·보류한 방향 확인

자동운용비율이나 Telegram 자금설정은 전략 연구변수가 아닙니다.

## 3. 문서별 단일 책임

| 문서 | 이 문서가 소유하는 것 | 이 문서에 두지 않는 것 |
|---|---|---|
| [`../CURRENT_WORK.md`](../CURRENT_WORK.md) | 현재 릴리즈, source/runtime 동기화, live 상태, 바로 다음 작업 | 긴 설계 설명, 과거 실행 일지 |
| [`ONE_PAGE_REPORT.md`](ONE_PAGE_REPORT.md) | JDSS 비전문가용 한 장 요약 | 자동주문 구현 상세 |
| [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md) | JDSS 전략의 쉬운 상세 설명 | JH AUTO 상태머신 |
| [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md) | 투자전략·목표비중·HWM75 공식·백테스트 규범 | Telegram 자동화 UX, 배포상태 |
| [`JH_AUTO_SPEC.md`](JH_AUTO_SPEC.md) | 자동운용 자금·성과·주문·시작·정지 계약 | 시장판단 수학의 중복 설명 |
| [`TELEGRAM_BOT_GUIDE.md`](TELEGRAM_BOT_GUIDE.md) | `/dashboard` `/today` `/auto` 등 운영자 사용법 | 전략 수학의 중복 설명 |
| [`TELEGRAM_LIVE_MESSAGE_STANDARD.md`](TELEGRAM_LIVE_MESSAGE_STANDARD.md) | 실거래 메시지 표현·우선순위·성과 표시 기준 | 자동매매 내부 구현 |
| [`HISTORY.md`](HISTORY.md) | 대표 릴리즈와 채택·기각 결정의 역사 색인 | 현재판 상세 계약 |
| [`infra/LIVE_COMMISSIONING.md`](infra/LIVE_COMMISSIONING.md) | 실계좌 연결·최초 AUTO 준비·사고 대응 | 전략 연구 |
| [`infra/DEVELOPMENT_WORKFLOW.md`](infra/DEVELOPMENT_WORKFLOW.md) | 브랜치·PR·CI·배포 흐름 | 개별 전략 설명 |
| [`infra/DEPLOYMENT.md`](infra/DEPLOYMENT.md) | Oracle 배포·검증·복구 절차 | 현재 배포 SHA |
| [`infra/SECURITY.md`](infra/SECURITY.md) | 인증·주문·DB·자동운용 안전 불변식 | 시장판단·수익률 연구 |
| [`research/RESEARCH_PROTOCOL.md`](research/RESEARCH_PROTOCOL.md) | 전략 연구 설계·OOS·강건성·승격 규칙 | AUTO 운영 설정 |
| [`../AGENTS.md`](../AGENTS.md) | Codex·ChatGPT·IDE가 반드시 지킬 실행 규칙 | 전략 설명 중복 |

## 4. 서로 내용이 다를 때

| 확인하려는 사실 | 기준 |
|---|---|
| 현재 릴리즈·배포·실거래 상태 | `CURRENT_WORK.md` + 실제 GitHub/Oracle |
| JDSS 실행 숫자·파라미터 | `strategy.yaml` |
| JDSS 전략 규범 | `JDSS_FINAL_SPEC.md` |
| JH AUTO 자금·자동실행 규범 | `JH_AUTO_SPEC.md` |
| 실제 프로그램 동작 | `src/jd_holdings/` + 테스트 |
| Telegram 사용법 | `TELEGRAM_BOT_GUIDE.md` + 실제 callback/help 테스트 |
| 보안 불변식 | `infra/SECURITY.md` + 코드·테스트 |
| 과거 채택·기각 이유 | `HISTORY.md` + 당시 PR/artifact |

문서·설정·구현·테스트가 서로 다르면 임의로 하나에 맞추지 않고 **불일치 자체를 결함으로 처리**합니다.

## 5. 변경 유형별 필수 동기화

| 변경 | 반드시 함께 확인할 문서 |
|---|---|
| JDSS 전략 규칙·비중·지표 | `JDSS_FINAL_SPEC`, `STRATEGY_GUIDE`, `ONE_PAGE_REPORT`, `strategy.yaml` |
| JH AUTO 기준자금·비율·HWM회계 | `JH_AUTO_SPEC`, `TELEGRAM_BOT_GUIDE`, `infra/SECURITY` |
| 자동승인·자동주문·부분체결·UNKNOWN | `JH_AUTO_SPEC`, `TELEGRAM_BOT_GUIDE`, `infra/SECURITY`, `LIVE_COMMISSIONING` |
| Telegram 버튼·명령·문구·수익률 | `TELEGRAM_BOT_GUIDE`, `TELEGRAM_LIVE_MESSAGE_STANDARD`, 포맷 테스트 |
| 최초 시작승인·대표 긴급정지·임시격리 | `JH_AUTO_SPEC`, `LIVE_COMMISSIONING`, `infra/SECURITY` |
| 배포·systemd·rollback | `infra/DEPLOYMENT`, `LIVE_COMMISSIONING`, 배포 계약 테스트 |
| 연구 방법·승격 기준 | `research/RESEARCH_PROTOCOL` |
| 현재 브랜치·배포·다음 작업 | `CURRENT_WORK.md`만 갱신 |
| 대표 릴리즈·기각·SHADOW 결론 | `HISTORY.md`에 짧게 추가 |

## 6. 수명주기

1. 현행 문서 파일명은 고정합니다. 날짜·버전을 붙인 복사본을 `main`에 계속 만들지 않습니다.
2. 전략 변경과 자동운용 변경은 구분하지만, 한 기능이 두 영역에 영향을 주면 같은 구현 PR에서 관련 문서를 함께 갱신합니다.
3. `CURRENT_WORK.md`는 append-only 일지가 아니라 **짧은 롤링 상태판**입니다.
4. `HISTORY.md`만 과거 결정을 append-only로 보존합니다.
5. 미채택 연구의 대형 결과는 연구 PR·Actions artifact에 보관합니다.
6. 같은 숫자나 계약을 여러 문서가 소유하지 않습니다.
7. JH AUTO 버전은 자동매매 실행계층 변화에만 올리고, JDSS 전략 버전은 전략 수학 변화에만 올립니다.

## 7. 공개 Markdown 안전 규칙

공개 문서에는 다음을 기록하지 않습니다.

- API 키·토큰·계좌번호·인증 헤더
- 서버 절대경로·OS 사용자명·서비스 실명·host 식별자
- backup/snapshot 실제 파일명
- 일회성 Actions run ID의 장기 누적

## 8. 문서 품질 체크리스트

- [ ] 이 내용의 소유 문서가 하나인지
- [ ] JDSS 전략과 JH AUTO 실행계층을 섞어 설명하지 않았는지
- [ ] 자금변경을 투자수익으로 잘못 설명하지 않았는지
- [ ] `자동운용비율`을 실제 시장투자비율로 오해하게 쓰지 않았는지
- [ ] 최초 시작승인 전 BUY가 가능하다고 읽힐 표현이 없는지
- [ ] 대표 `/halt`를 시스템이 자동해제할 수 있다고 쓰지 않았는지
- [ ] UNKNOWN 주문을 실패로 단정해 재주문하도록 쓰지 않았는지
- [ ] 현재 수치가 `strategy.yaml`·테스트와 맞는지
- [ ] 오래된 SHA·run ID를 `CURRENT_WORK`에 누적하지 않았는지
- [ ] 문서-only 변경이면 runtime을 불필요하게 재배포하지 않는지

이 문서체계의 목적은 문서 수를 늘리는 것이 아니라 **전략과 자동운용을 명확히 분리해 각각 신뢰할 기준을 만드는 것**입니다.

## 현행 문서 갱신 원칙

현행 사양과 운영 가이드는 새 버전마다 복사본을 만들지 않고 **제자리 갱신**합니다. 현행 파일명은 고정하고, 과거 상태는 Git tag·병합 PR·`HISTORY.md`에서 복구합니다. 전략과 자동운용은 문서를 분리하되 같은 기능 변경이 양쪽에 영향을 주면 한 PR에서 함께 동기화합니다.
