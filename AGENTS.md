# JH_HOLDINGS Agent Instructions


## JDSS 전략과 JH AUTO 실행계층

- **JDSS 3.2.2**는 시장판단·목표비중·RS6M·HWM75 전략 수학을 소유합니다.
- **JH AUTO 1.0.0**은 운용 기준자금·자동운용비율·성과회계·자동승인·자동주문·최초 시작·긴급정지를 소유합니다.
- 전략 변경은 `strategy.yaml` + `docs/JDSS_FINAL_SPEC.md`를, 자동운용 변경은 `docs/JH_AUTO_SPEC.md`를 반드시 기준으로 확인합니다.
- JH AUTO가 설치돼도 `launch_authorized=1`이 운영자 확인으로 기록되기 전 실제 BUY는 0건이어야 합니다.
- 배포·재시작·정상화 작업은 최초 시작승인으로 해석하지 않습니다.
- 운영자 `/halt`의 durable latch는 시스템이 자동해제할 수 없습니다.
- 자동 BUY는 기존 `TradingService → OrderManager` 최종 안전경계를 우회해서 구현하지 않습니다.

이 파일은 Codex, ChatGPT, 그리고 Antigravity(안티그라비티)가 이 저장소에서 작업할 때 공통으로 따라야 할 작업 규칙을 정의한다.

GitHub 저장소명은 `JH_HOLDINGS`이다. Python 호환성을 위해 내부 패키지명 `jd_holdings`와 CLI `jdss` / `jdss-bot`은 유지한다. Oracle의 정확한 대상 디렉터리·서비스명·백업 식별자는 보호된 배포 설정과 비공개 운영 기록에서 관리하며 공개 Markdown에 적지 않는다. 완료된 구 runtime 식별자는 새 배포·문서·자동화에서 다시 사용하지 않는다.

세부 절차는 `docs/infra/DEVELOPMENT_WORKFLOW.md`를 따른다.
현재 작업 상태와 인수인계 정보는 `CURRENT_WORK.md`를 Source of Truth로 사용한다.
문서의 현재/과거 구분과 읽기 순서는 `docs/README.md`를 따른다.

## 기준 문서와 우선순위

동일한 내용이 여러 파일에 있을 때 다음 순서를 따른다.

1. 현재 작업·배포·검증 상태: `CURRENT_WORK.md`
2. 실행 수치: `strategy.yaml`
3. 전략·주문·자금관리 계약: `docs/JDSS_FINAL_SPEC.md`
4. 실제 구현: `src/jd_holdings/`
5. 사용자·운영 절차: 해당 가이드 문서
6. 과거 기록: `docs/HISTORY.md`, Git tag, 병합 PR 및 Actions artifact

`strategy.yaml`, 공식 계약, 구현이 서로 다르면 임의로 하나에 맞추지 말고 불일치로 보고한 뒤 수정 범위를 확정한다. 변동 가능한 브랜치, SHA, 테스트 개수, 서버 상태를 README나 전략 문서에 복제하지 않고 `CURRENT_WORK.md`에서만 관리한다.

## 변경 영향별 필수 동기화

| 변경 유형 | 함께 확인·갱신할 기준 | 필수 검증 |
|---|---|---|
| 전략 조건·점수·비중·익절 | `strategy.yaml`, `JDSS_FINAL_SPEC.md`, `ONE_PAGE_REPORT.md`, `STRATEGY_GUIDE.md`, 관련 테스트 | 설정 검증, 단위 테스트, 노룩어헤드 백테스트 |
| 주문·승인·포지션·SGOV | 공식 사양, `TELEGRAM_BOT_GUIDE.md`, `docs/infra/SECURITY.md`, 관련 테스트 | Dry Run, 멱등성·재시작·Reconciliation 테스트 |
| Telegram 명령·버튼·문구 | `TELEGRAM_BOT_GUIDE.md`, 도움말·포맷 테스트 | 권한 검사, 4,096자 제한, 콜백 만료·1회성·stale state |
| Toss API·인증·네트워크 | `docs/infra/SECURITY.md`, 배포 문서, 어댑터 테스트 | 오류 응답·타임아웃·입력 경계 테스트 |
| DB 스키마·상태 전이 | 공식 사양, `docs/infra/SECURITY.md`, 배포·마이그레이션 절차, 관련 테스트 | 기존 DB 호환성, WAL, 트랜잭션, 재시작 검증 |
| 배포·systemd·Actions | `DEPLOYMENT.md`, `SECURITY.md`, 배포 계약 테스트 | 셸 문법, 최소권한, host key 고정, 강제 dry-run, 백업·rollback, smoke test |
| 현재 브랜치·배포·다음 작업 | `CURRENT_WORK.md`만 갱신 | 원격 SHA와 실제 상태 대조 |
| 과거 자료·연구 기록 | `HISTORY.md`에 요약하고 상세 결과는 Git tag·PR·Actions artifact로 보존 | 현행 수치로 오인될 표현 점검 |

코드 변경으로 사용자 동작이나 운영 절차가 달라졌는데 관련 Markdown이 그대로라면 작업 완료로 간주하지 않는다. 반대로 구현 변화가 없는 단순 리팩터링은 문서의 동작 설명을 불필요하게 다시 쓰지 않고 `CURRENT_WORK.md`에 검증 결과만 남긴다.

## 문서 수명주기

- 현행 Markdown은 [`docs/README.md`](docs/README.md)에 정의된 고정 파일을 제자리에서 갱신한다. 버전·날짜를 붙인 새 전략 문서, 백테스트 보고서, 인수인계 문서를 `main`에 추가하지 않는다.
- `CURRENT_WORK.md`는 누적 일지가 아니라 **롤링 상태판**이다. 현재 릴리즈·배포·검증·활성 목표와 바로 다음 작업만 남기고 지난 상태는 Git 이력과 PR에서 확인한다.
- `docs/HISTORY.md`만 과거 요약을 append-only로 관리한다. 릴리즈당 한 항목, 대표 미채택 연구당 한 항목을 추가하고 과거 전체 문서는 Git tag에서 복구한다.
- 일회성 연구 스크립트와 상세 결과는 연구 PR 및 Actions artifact에 보관한다. 채택된 계약만 현행 문서·설정·코드에 반영한다.
- 같은 내용을 여러 문서가 자세히 소유하지 않는다. 정확한 수치·계약·절차의 소유 문서를 링크하고, 한 장 요약과 안전 경고처럼 필요한 파생 설명만 제한적으로 반복한다.

## 한글 중심 작성 원칙

- 운영자·대표·비개발자가 읽는 Markdown과 Telegram 문구는 한글을 우선한다.
- 영문 기술용어는 꼭 필요할 때만 첫 등장에 `한글 설명(영문)`으로 병기한다.
- `BUY`, `SELL`, `SAFE_MODE`, `reconciliation`, `preflight`, `runtime`, `dry-run`, `commissioning`, `batch`를 설명 없이 반복하지 않는다.
- 사용자 화면에서는 각각 `매수`, `매도`, `안전정지`, `계좌·원장 대조`, `사전점검`, `운영 서버 프로그램`, `모의운용`, `실거래 준비 전환`, `주문 묶음`을 우선한다.
- 코드 식별자, 설정 키, 실제 명령, 장애코드는 정확성을 위해 원문을 유지하되 바로 옆에 쉬운 한글 뜻을 붙인다.
- 개발자용 세부 문서도 각 절의 결론과 운영자 행동은 한글로 먼저 설명한다.

## 코드 품질·보안 규칙

- 동작 보존형 리팩터링과 전략 변경을 같은 커밋에 섞지 않는다.
- 성능 최적화는 측정 근거가 있는 병목에만 적용하고 결과를 재현 가능하게 남긴다.
- 광범위한 `except Exception`은 프로세스 경계나 스케줄러 격리처럼 필요한 곳에만 두고, 내부 계층에서는 구체적 예외를 우선한다.
- 비밀값, 승인 토큰, 전체 계좌번호, 인증 헤더, SSH 키를 소스·테스트·로그·문서·이슈·채팅에 기록하지 않는다.
- 로컬 `.env`와 SSH 키를 GitHub로 이동하지 않는다. Actions 배포가 필요하면 승인된 GitHub Environment secret을 별도로 등록한다.
- SSH는 검증된 Oracle host public key를 `known_hosts`로 고정하고 `StrictHostKeyChecking=yes`를 사용한다. Actions 실행 중 `ssh-keyscan` 결과를 즉석 신뢰하거나 `accept-new`로 우회하지 않는다.
- 외부 입력은 Telegram 명령, 콜백, API 응답, 설정, 주문 경계에서 검증한다.
- 실거래 잠금, 사용자 2단계 승인, 주문 멱등성, SAFE_MODE를 약화하는 변경은 대표의 명시적 승인 없이 수행하지 않는다.
- 의존성·Actions 업데이트 PR은 기능 변경과 분리하고 CI·Dry Run 통과 후 반영한다.
- 완료 상태 문서와 구현은 가능한 한 **한 PR에서 함께 마감**한다. Actions run ID만 기록하려고 후속 문서 PR을 만들지 않으며, 정확한 실행 링크는 PR·Actions·최종 보고에서 확인한다.
- 문서-only PR은 안정적인 필수 check 이름을 유지한 fast path를 사용하고, 전략·코드·의존성에 영향이 없으면 전체 pytest·CodeQL·canonical backtest를 반복하지 않는다.
- 새 commit이 이전 실행을 대체하면 concurrency로 오래된 PR 검증을 취소한다. 같은 SHA의 배포를 반복하거나 runtime 영향이 없는 문서-only commit을 Oracle에 재배포하지 않는다.
- 공개 저장소에는 API 키·토큰·계좌번호·서버 비밀값을 두지 않으며, 공개가 필요 없는 전략/운영 정보는 별도 승인 없이 새로 노출하지 않는다.
- 공개 Markdown에는 서버 절대경로, OS 사용자명, 서비스 실명, backup/snapshot 파일명, host 식별자와 일회성 실행 ID를 기록하지 않는다. 상태판에는 성공 여부와 검증 범위만 남기고 정확한 값은 보호된 설정·비공개 운영 기록에서 확인한다.

## 사용자 단축 명령

사용자가 `작업 시작`이라고 말하면:

1. 가장 먼저 `CURRENT_WORK.md`를 읽어 현재 활성 개발 브랜치, 전략 버전, 현재 목표, 마지막 완료 작업, 다음 작업을 확인한다.
2. 현재 브랜치와 저장소 상태를 확인한다.
3. 원격 GitHub 최신 상태를 확인한다.
4. 로컬/IDE 환경(Codex, Antigravity)에서는 안전할 때 `git fetch origin` 및 `git pull --ff-only`로 활성 개발 브랜치를 최신화한다.
5. 로컬 미커밋 변경이 있으면 임의로 덮어쓰지 않고 먼저 보고한다.
6. ChatGPT 환경에서는 GitHub의 활성 개발 브랜치 최신 파일과 커밋을 다시 읽고 `main`과 필요한 차이를 확인한다.
7. 동기화 상태와 이번 작업 목표를 간단히 보고한 뒤 실제 작업을 시작한다.

사용자가 `작업 종료`라고 말하면:

1. 변경사항을 검토한다.
2. 가능한 환경에서는 `pytest`와 `ruff check .`를 실행한다.
3. 테스트 결과를 숨기지 않는다.
4. 변경사항을 명확한 커밋 메시지로 commit한다.
5. 현재 활성 개발 브랜치에 push한다.
6. `CURRENT_WORK.md`의 현재 상태와 다음 작업을 구현 PR 안에서 함께 갱신한다. Actions run ID만 추가하기 위한 후속 문서 PR은 만들지 않는다.
7. 실제 배포 반영이 승인된 경우 환경에 맞는 표준 경로를 사용한다. 로컬/IDE는 신뢰된 `SSH_KNOWN_HOSTS_PATH`를 지정해 `env -u GITHUB_TOKEN ./deploy.sh`를 실행하고, ChatGPT는 GitHub의 owner-only **Deploy Oracle Dry Run** ChatOps를 시작해 최신 `main`을 배포한다. GitHub Actions 화면의 수동 버튼 클릭은 필수 조건이 아니다.
8. 마지막 커밋 SHA, 변경 요약, 테스트 결과, 남은 작업을 보고한다.

## Git 안전 규칙

- GitHub 원격 저장소를 Source of Truth로 사용한다.
- `main`에서 직접 기능 개발하지 않는다. 별도 작업 브랜치를 사용한다.
- `main`은 GitHub branch protection/ruleset으로 직접 push·force push·삭제를 막고, PR과 필수 CI를 통과한 변경만 병합한다.
- Codex, ChatGPT, Antigravity 간 동일 브랜치를 동시에 수정하여 충돌을 일으키지 않는다.
- 환경 전환 전에는 먼저 commit + push 하고, 다음 환경에서 최신 상태를 동기화한다.
- 사용자의 명시적 승인 없이 `git push --force`, `git reset --hard`, 위험한 rebase, 대량 삭제를 수행하지 않는다.
- 충돌 발생 시 임의 해결하지 말고 사용자에게 충돌 내용을 알린다.

## 실행 환경 역할 분리

- **Codex·Antigravity(로컬/IDE)**: 구현, 디버깅, 로컬 통합 테스트와 작업트리 관리가 주 역할이다. 사용자의 미커밋 변경을 보존하고, 원격 비밀값이나 로그인 세션이 있다고 가정하지 않는다.
- **ChatGPT·GitHub 연결 환경**: 원격 저장소 확인, 브랜치·PR 작성, Actions 상태 점검과 승인된 owner-only ChatOps 실행이 주 역할이다. 연결된 GitHub 권한과 Environment secret을 사용해 배포 workflow를 시작할 수 있지만 비밀값 자체를 조회·복제하지 않는다.
- **GitHub Actions**: `pytest`, Ruff, 설정 검증, Security Gate, Backtest와 Oracle forced dry-run 배포를 수행하는 반복 가능한 표준 실행 환경이다. 배포는 검증된 최신 `main`과 `oracle-dry-run` Environment secret만 사용한다.
- **Oracle Cloud**: Telegram Bot, 정규장 종료 후 분석, 승인/주문/포지션 감시 등 **JDSS 24시간 운영 서비스 전용 환경**이다. 연구용 대규모 백테스트와 후보 탐색을 운영 프로세스에 섞지 않는다.
- 어떤 에이전트도 배포 권한을 live 활성화 권한으로 해석하지 않는다. `portfolio.live_enabled=false`, forced dry-run과 빈 live confirmation은 별도 명시적 승인 전까지 유지한다.
- 전략 변경은 별도 연구/개발 브랜치에서 GitHub Actions 검증을 거치고, 채택된 변경만 PR을 통해 `main`에 반영한 뒤 필요 시 Oracle Cloud에 배포한다.

## JDSS 전략 프로젝트 규칙

- 전략 변경 시 문서, 설정, 테스트, 코드가 서로 일치해야 한다.
- **백테스트 및 실제 운용의 핵심 목표는 위험 대비 수익률을 지속적으로 높이는 것이다.** 수익률 개선 자체를 회피하지 않는다.
- 전략 후보를 비교할 때 Total Return, CAGR 등 수익성 지표를 적극적으로 개선하되 MDD, MAE, 변동성, 손실 지속기간, 거래비용, 자금 활용률 등 위험과 비용을 함께 평가한다.
- 동일하거나 유사한 위험 수준이라면 더 높은 수익률을 내는 전략을 우선한다. 동일하거나 유사한 수익률이라면 더 낮은 위험의 전략을 우선한다.
- 높은 백테스트 수익률은 중요하지만 미래 데이터 사용, 데이터 누수, 비현실적 체결가, 과도한 파라미터 탐색, 특정 기간에만 맞춘 튜닝으로 만든 성과는 인정하지 않는다.
- 개발구간 성과뿐 아니라 검증구간(OOS)과 기간별/종목별 안정성을 확인하여 높은 수익률이 재현 가능한지 검증한다.
- 전략은 가능한 한 설명 가능하고 재현 가능하게 유지하되, 이것이 합리적인 수익률 개선 실험을 막는 이유가 되어서는 안 된다.
- 주문 실행, 실거래 활성화, 리스크 한도 변경은 특별히 보수적으로 검토한다.
