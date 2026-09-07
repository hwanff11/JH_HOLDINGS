# JH_HOLDINGS Agent Instructions

이 파일은 Codex, ChatGPT, IDE 에이전트가 저장소에서 작업할 때 반드시 지킬 **필수 실행규칙**만 소유합니다. 사람 중심의 상세 개발·PR·배포 절차는 [`docs/infra/DEVELOPMENT_WORKFLOW.md`](docs/infra/DEVELOPMENT_WORKFLOW.md)를 따릅니다.

## 1. 전략과 자동운용의 책임 분리

- **JDSS 3.2.2**는 시장판단·목표노출·QQQ/TQQQ/SOXL 목표비중·RS6M·HWM75 전략 수학을 소유합니다.
- **JH AUTO 1.0.0**은 운용 기준자금·자동운용비율·성과회계·자동승인·자동주문·최초 시작·긴급정지를 소유합니다.
- 전략 실행 숫자는 `strategy.yaml`, 전략 계약은 `docs/JDSS_FINAL_SPEC.md`, 자동운용 계약은 `docs/JH_AUTO_SPEC.md`가 기준입니다.
- JH AUTO가 배포되어도 `launch_authorized=1`이 운영자 확인으로 기록되기 전 실제 신규 BUY는 0건이어야 합니다.
- 배포·재시작·정상화는 최초 시작승인으로 해석하지 않습니다.
- 운영자 `/halt` durable latch는 시스템이 자동해제할 수 없습니다.
- 자동 BUY는 기존 `TradingService → OrderManager` 최종 안전경계를 우회하지 않습니다.

## 2. 기준 문서와 우선순위

동일한 내용이 여러 위치에 있을 때 다음 순서로 확인합니다.

1. 현재 릴리즈·배포·실거래 상태: `CURRENT_WORK.md`
2. JDSS 실행 수치: `strategy.yaml`
3. JDSS 전략 계약: `docs/JDSS_FINAL_SPEC.md`
4. JH AUTO 자금·주문·시작·정지 계약: `docs/JH_AUTO_SPEC.md`
5. 실제 구현: `src/jd_holdings/` + 테스트
6. Telegram 사용법·표현: `docs/TELEGRAM_BOT_GUIDE.md`
7. 보안 불변식: `docs/infra/SECURITY.md`
8. 배포·복구: `docs/infra/DEPLOYMENT.md`
9. 과거 결정: `docs/HISTORY.md`, Git tag, 병합 PR, Actions artifact

설정·공식계약·구현·테스트가 다르면 임의로 하나에 맞추지 말고 **불일치 자체를 결함으로 보고** 수정범위를 확정합니다.

## 3. 변경 영향별 필수 동기화

| 변경 유형 | 함께 확인·갱신할 기준 | 필수 검증 |
|---|---|---|
| 전략 조건·지표·비중·HWM75 | `strategy.yaml`, `JDSS_FINAL_SPEC.md`, `STRATEGY_GUIDE.md`, 관련 테스트 | 설정 검증, 단위 테스트, no-lookahead canonical backtest |
| JH AUTO 자금·단계·성과회계 | `JH_AUTO_SPEC.md`, `TELEGRAM_BOT_GUIDE.md`, `docs/infra/SECURITY.md` | 자금흐름·HWM·원자성·재시작 테스트 |
| 주문·승인·포지션·DB | `JH_AUTO_SPEC.md`, `TELEGRAM_BOT_GUIDE.md`, `docs/infra/SECURITY.md` | 멱등성·부분체결·UNKNOWN·reconciliation·SAFE_MODE |
| Telegram 명령·버튼·문구 | `TELEGRAM_BOT_GUIDE.md`, help/format/callback 테스트 | 관리자 인증, 4,096자 제한, TTL·stale·재사용 거부 |
| Toss API·인증·네트워크 | `docs/infra/SECURITY.md`, adapter 테스트 | timeout, HTTP/JSON 오류, read retry/write receipt 경계 |
| 배포·systemd·Actions | `docs/infra/DEPLOYMENT.md`, `docs/infra/SECURITY.md` | pinned SSH trust, BUY halt, snapshot, rollback, smoke |
| 개발환경·PR·인수인계 | `docs/infra/DEVELOPMENT_WORKFLOW.md`, `CURRENT_WORK.md` | branch/PR/CI 상태와 실제 원격 확인 |
| 현재 브랜치·배포·다음 작업 | `CURRENT_WORK.md`만 갱신 | GitHub/Oracle 실제 상태 대조 |
| 연구 방법·승격 기준 | `docs/research/RESEARCH_PROTOCOL.md` | baseline parity, OOS, 비용, 강건성 |
| 문서-only 설명 | 소유 문서 + 링크/계약 테스트 | 링크·중복·금지식별자 검사, runtime 재배포 생략 |

코드 변경으로 사용자 동작이나 운영 절차가 달라졌는데 관련 문서가 그대로라면 완료로 간주하지 않습니다. 반대로 동작 변화가 없는 리팩터링은 문서의 계약을 불필요하게 다시 쓰지 않습니다.

## 4. 문서 수명주기

- 현행 Markdown은 고정 파일을 **제자리 갱신**합니다. 날짜·버전이 붙은 전략문서·백테스트 보고서·인수인계 사본을 `main`에 누적하지 않습니다.
- `CURRENT_WORK.md`는 append-only 일지가 아니라 **짧은 롤링 상태판**입니다. 현재 릴리즈·배포·검증·활성 목표와 바로 다음 행동만 남깁니다.
- `docs/HISTORY.md`는 과거 채택·기각·중요 운영결정의 append-only 요약 색인입니다. 상세 원본은 Git tag·PR·artifact에서 복구합니다.
- 같은 규칙의 숫자·계약을 여러 문서가 각각 소유하지 않습니다. 파생 설명은 소유 문서를 링크합니다.
- 미채택 연구의 상세 결과는 연구 PR·Actions artifact에 보존하고, 채택된 계약만 현재 설정·코드·문서에 반영합니다.

## 5. 코드 품질·보안 규칙

- 동작 보존형 리팩터링과 전략 변경을 같은 커밋에 섞지 않습니다.
- 광범위한 `except Exception`은 프로세스 경계처럼 필요한 곳으로 제한하고 내부 계층에서는 구체적 예외를 우선합니다.
- 비밀값, 승인토큰, 전체 계좌번호, 인증 header, SSH private key를 소스·테스트·로그·문서·이슈에 기록하지 않습니다.
- 로컬 `.env`와 SSH key를 GitHub로 이동하지 않습니다.
- SSH는 검증된 host public key를 고정하고 `StrictHostKeyChecking=yes`를 사용합니다. `ssh-keyscan` 결과를 즉석 신뢰하거나 `accept-new`로 우회하지 않습니다.
- 외부 입력은 Telegram 명령·callback·API 응답·설정·주문 경계에서 검증합니다.
- 주문 멱등성, 부분체결 delta, UNKNOWN 처리, reconciliation, SAFE_MODE, 최초 시작잠금을 약화하지 않습니다.
- 문서-only 변경은 runtime 영향이 없으면 Oracle에 재배포하지 않습니다.

## 6. Git 안전 규칙

- 기능 개발은 보호된 `main`에 직접 push하지 않습니다. 별도 branch + PR을 사용합니다.
- 동일 branch를 여러 작업환경이 동시에 수정하지 않습니다.
- 로컬 미커밋 변경을 임의로 덮어쓰지 않습니다.
- `git reset --hard`, 강제 push, 대량삭제는 사용자의 명시적 의도 없이 수행하지 않습니다.
- CI가 끝나기 전에 PASS로 추정하지 않습니다.
- merge와 deploy, deploy와 BUY 잠금해제는 각각 별개 사건입니다.

## 7. 사용자 단축 명령

사용자가 `작업 시작`이라고 하면:

1. `CURRENT_WORK.md`를 읽어 현재 운영상태·활성목표·다음 행동을 확인합니다.
2. 최신 원격 `main`과 현재 작업 branch를 확인합니다.
3. 관련 소유 문서·설정·구현·테스트를 확인합니다.
4. 로컬 환경에서는 안전할 때 `git fetch`와 fast-forward 동기화를 수행하고 미커밋 변경이 있으면 먼저 보고합니다.
5. 작업 목표와 안전경계를 짧게 보고한 뒤 실제 수정에 들어갑니다.

사용자가 `작업 종료`라고 하면:

1. 변경사항과 의도하지 않은 diff를 검토합니다.
2. 변경 유형에 맞는 pytest/Ruff/설정/백테스트/보안검증을 실행합니다.
3. 실패 결과를 숨기지 않습니다.
4. 변경을 branch에 commit/push하고 PR CI를 확인합니다.
5. 필요한 문서와 `CURRENT_WORK.md`를 같은 PR에서 마감합니다.
6. runtime 영향이 있고 배포가 승인된 경우 `docs/infra/DEPLOYMENT.md`의 표준 경로로 최신 검증 `main`만 배포합니다.
7. 문서-only 변경은 merge로 완료하고 Oracle runtime은 재배포하지 않습니다.
8. 최종 commit SHA, 테스트, 배포 여부, 남은 작업을 보고합니다.

## 8. 공개 Markdown 작성 원칙

운영자·비개발자가 읽는 설명은 한글을 우선합니다. 코드 식별자·설정키·명령은 정확성을 위해 원문을 유지합니다.

공개 Markdown에는 다음을 기록하지 않습니다.

- API key·token·계좌번호·인증 header
- 서버 절대경로·OS 사용자명·서비스 실명·host 식별자
- 실제 backup/snapshot 파일명
- 일회성 Actions run ID의 장기 누적

문서의 목적은 설명을 늘리는 것이 아니라 **한 규칙의 소유 위치를 명확히 해 코드·운영·문서가 같은 계약을 보게 하는 것**입니다.
