# JH_HOLDINGS 보안·주문 안전 기준

이 문서는 **비밀정보, Telegram 관리자 인증, 매수 승인, 중복주문 방지, SQLite 원장, Toss API, GitHub Actions, Oracle 배포와 실거래 잠금의 기술적 안전 원칙**을 소유합니다.

전략 숫자는 [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md)와 [`../../strategy.yaml`](../../strategy.yaml), 현재 배포·실거래 상태는 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)를 따릅니다.

운영자가 먼저 기억할 원칙은 세 가지입니다: **매수는 2단계 승인, 결과가 불명확하면 재주문 금지, 계좌와 원장이 다르면 안전정지**입니다. 아래 영문은 실제 코드·장애코드 확인을 위해 필요한 경우에만 유지합니다.

## 1. 신뢰 경계

| 경계 | 신뢰 가능한 것 | 항상 다시 검증할 것 |
|---|---|---|
| Telegram | 설정된 관리자 Chat ID | private chat, `from_user`, callback 단계, token, TTL, stale button |
| JDSS 내부 | 검증된 설정과 커밋된 DB transaction | 환경변수, 기존 DB, 외부 입력 |
| Dry-run broker | 현재 프로세스·SQLite로 증명되는 모의상태 | 재시작 복원, 부분체결, 열린 주문 |
| Toss OpenAPI | 고정된 공식 HTTPS endpoint | 인증, HTTP, JSON, 숫자 범위, 실제 계좌·주문 상태 |
| GitHub Actions | 보호된 main과 승인된 Environment | source SHA, 요청 주체, secret 존재, workflow 권한 |
| Oracle | 배포된 release 구조 | SSH host key, 환경파일 권한, DB·서비스·원장 상태 |

Dry-run broker와 Toss OpenAPI는 서로 다른 경계입니다. 한쪽 성공을 다른 쪽 성공으로 간주하지 않습니다.

## 2. 공개 저장소와 비밀정보

다음을 Git·로그·Markdown·Issue·테스트 fixture에 기록하지 않습니다.

- `.env` 실제 값
- Telegram Bot Token
- Toss 앱 key/secret과 인증 header
- SSH private key
- 전체 계좌번호
- approval raw token
- GitHub Environment secret

공개 Markdown에는 서버 절대경로·OS 사용자명·서비스 실명·host 식별자·실제 backup/snapshot 파일명·장기 보존할 필요가 없는 일회성 run ID를 적지 않습니다.

`.env.example`에는 secret 이름과 비밀이 아닌 안전한 기본값만 둡니다.

의심 노출이 있으면 live를 잠그고 자격증명을 폐기·재발급한 뒤 history까지 Gitleaks로 확인합니다.

## 3. `main` 변경 통제

- PR 없는 기능 변경 금지
- force push 금지
- branch delete 보호
- 안정적인 필수 check 이름 유지
- Quality Gate와 Security Gate 필수
- 전략·백테스트 민감 변경은 canonical Backtest 확인
- Actions 기본 권한은 `contents: read`, 필요한 workflow만 최소 추가 권한
- 외부 GitHub Action은 검증한 40자리 commit SHA로 고정하고 표시용 major 버전 주석만 병기
- 문서-only 변경은 안전한 fast path를 사용하고 runtime을 불필요하게 재배포하지 않음

branch protection이 비활성화되면 코드가 정상이어도 production 변경통제는 미완료로 봅니다.

## 4. Telegram 관리자 인증

- 정확히 1개의 관리자 Chat ID를 허용
- private chat만 허용
- `chat.id`와 `from_user.id`가 관리자와 일치하는지 명령·callback 모두 검사
- callback payload는 신뢰하지 않고 DB의 현재 상태와 대조
- stale onboarding 단계·만료 approval·이미 사용된 token은 거부
- 사용자 메시지에 secret·approval token을 노출하지 않음
- Telegram에 표시하는 예외·감사로그 문구는 credential, URL, 서버 절대경로, 장문 식별번호를 정제하고 길이를 제한
- 전체 traceback과 원본 예외는 Telegram이 아니라 접근 통제된 Oracle 로그에서만 확인

## 5. 위험증가 BUY 승인 불변식

BUY는 사람 승인 없는 자동 실행으로 확대하지 않습니다.

기본 사용자 경로는:

```text
오늘 주문 한번에 검토
  → preflight
  → N건 순차 실행 최종 승인
```

내부적으로는 각 signal의 review/execution approval 경계를 유지합니다.

- token은 암호학적 난수
- DB에는 SHA-256 hash만 저장
- 상수시간 비교
- approval stage 확인
- 짧은 TTL
- 1회사용
- 가격·수량·세션 변경 시 기존 approval 폐기

## 6. 일괄 BUY preflight 안전장치

batch approval을 만들기 전에 다음을 모두 증명해야 합니다.

1. 주문 허용 세션
2. Toss 08:50~08:59 KST 점검시간이 아님
3. 최신 완결 거래일의 production 계산 freshness
4. 새 목표의 다음 거래일·target_qty 준비 상태
5. 열린 위험축소 SELL 없음
6. 중복 BUY 미체결 없음
7. 즉시 reconciliation 성공
8. SAFE_MODE 없음
9. signal generation/version 유효
10. **전체 BUY 합계**가 HWM75·JDSS 현금·브로커 주문가능금액을 넘지 않음

active BUY signal이 없다는 이유만으로 `오늘 주문 없음`을 만들지 않습니다. target_qty와 보유수량을 대조해 계산지연·SELL 준비·BUY 생성대기·onboarding 단계대기와 진짜 주문없음을 구분합니다.

## 7. batch 동시성·중복방지

- batch 생성부터 실행까지 process-level lock으로 직렬화
- 이미 유효한 batch가 있으면 새 batch 생성 금지
- batch가 생성되는 동안 일부 execution approval만 만들어지고 오류가 나면 모두 cleanup
- 오래된 batch ID와 callback 재사용 금지
- 서버 재시작 뒤 메모리 batch를 복원해 주문권한으로 사용하지 않음
- DB 주문예약은 SQLite `BEGIN IMMEDIATE` 안에서 현금·위험예산·잔여 목표를 다시 검사

process lock은 사용자 편의용 1차 방어이고, **최종 안전경계는 DB transaction과 broker/order validation**입니다.

## 8. 최종 실행 직전 재검증

최종 버튼 직전에 다시 확인합니다.

- broker/DB reconciliation
- SAFE_MODE
- 새 열린 주문
- 전체 매수가능한도
- 각 종목 가격·수량·세션
- execution approval 유효성

검토와 실행 사이의 상태 차이를 자동으로 무시하지 않습니다.

## 9. 순차 제출과 부분실행

일괄 BUY는 원자적 basket 주문이 아닙니다.

- 각 종목은 독립 주문으로 제출
- 앞 주문이 성공하고 뒤 주문이 실패할 수 있음
- 가격변경, `UNKNOWN`, `REJECTED`, `CANCELED`, `REPLACED` 등 fail-closed 조건이 나오면 이후 BUY 중단
- 남은 approval 취소
- 이미 제출된 앞 주문을 자동 반대매매해 rollback하지 않음
- 앞 주문은 실제 broker 상태로 계속 감시

사용자 화면에서도 `전체 실행`보다 **`N건 순차 실행`** 표현을 사용해 원자적 주문으로 오해하지 않게 합니다.

## 10. 전략 자금 경계

- HWM 위험예산은 현재 평가액보다 클 수 없음
- 손실을 개인 현금으로 자동 보충하지 않음
- 기존 allocation 원가와 열린 BUY 잔여 notional·수수료를 위험예산에서 예약
- 아직 원장에 반영되지 않은 확정 체결도 잔여 목표 계산에 반영
- 신규 BUY는 HWM75 위험예산, JDSS 현금, 브로커 주문가능금액, 종목 잔여 target 중 가장 제한적인 경계를 넘지 않음
- batch 사전검사와 별개로 **각 실제 주문예약에서 다시 원자적으로 검사**

## 11. SELL-first 안전장치

위험축소 SELL은 BUY보다 먼저 처리합니다.

- 목표 변경 전 기존 allocation 주문 상태 최신화
- 필요한 SELL 제출
- 종료 확인
- 체결 원장 반영
- reconciliation
- 이후에만 BUY 허용

SELL 부분체결·`UNKNOWN`·취소확인 실패·불완전 정산이 있으면 신규 BUY를 차단합니다.

## 12. 주문 멱등성과 broker receipt 검증

- 결정적 client order ID 사용
- 동일 client order ID 재시도는 새 주문을 임의 생성하지 않음
- broker 최신 receipt를 DB에 먼저 저장
- client order ID, broker order ID, symbol, side, ordered qty, filled qty를 예약값과 대조
- 불일치 receipt는 `UNKNOWN`
- 누적 filled qty 감소 거부
- 종료 주문의 비종료 상태 복귀 거부
- 누적 체결수량과 누적 체결금액은 이전 적용값과의 delta만 원장 반영
- `PENDING_CANCEL`, `PENDING_REPLACE` 포함 비종료 상태는 열린 주문으로 예약·감시

## 13. 재시작 안전성

- 시작 시 DB strategy generation·schema와 설정 호환성 확인
- 증명 가능한 dry-run 체결·수수료로만 수량·현금 복원
- 재시작 시 `UNKNOWN`과 `PARTIAL_FILLED`를 추정 성공처리하지 않음
- broker order ID가 없거나 열린 주문을 현재 broker에서 찾을 수 없으면 SAFE_MODE
- 같은 generation의 저장 target_qty와 현재 보유·열린 주문 차이만 BUY gap 복구 후보로 사용

## 14. SAFE_MODE

대표 진입 조건:

- 주문 결과 `UNKNOWN`
- broker/DB 보유 불일치
- 열린 주문 불일치
- 위험축소 SELL 미완료
- 복구 상태를 증명할 수 없음
- 전략 generation/version 불일치

SAFE_MODE는 단 한 번의 정상 조회만으로 자동 해제하지 않습니다.

## 15. 실제 Toss와 dry-run 분리

- forced dry-run 주문·보유·미체결은 SQLite + 모의 broker 기준
- `/account`와 `toss-smoke`는 실제 Toss를 read-only 조회
- Toss read-only 결과를 dry-run 보유에 자동 채택하지 않음
- dry-run 주문을 실주문으로 자동 변환하지 않음
- 실제 계좌 조회 실패를 0주·정상으로 해석하지 않음
- **실거래 운영 프로그램이 실행 중일 때 외부 health process는 `toss-smoke`로 별도 access token을 발급하지 않음**
- 실거래 authenticated smoke가 필요하면 서비스 정지·기동과 직렬화된 배포 구간 또는 운영 프로그램 자체 조회경계에서 수행

같은 Toss 계좌에서 개인 QQQ/TQQQ/SOXL을 JDSS 관리물량과 혼합하지 않습니다. JDSS 주문과 Toss 앱의 동일티커 수동 주문도 동시에 수행하지 않는 것을 운영 원칙으로 합니다.

## 16. Toss API·네트워크

- 공식 HTTPS base URL 고정
- 연결·응답 timeout
- 401 token refresh 재시도 횟수 제한
- 동일 client credentials의 독립 프로세스 토큰 재발급은 기존 운영 토큰을 무효화할 수 있으므로 LIVE 중 독립 token issuer를 만들지 않음
- GET 계열 read-only 요청은 내부 token refresh 후에도 `invalid-token`·`expired-token`이 남는 동시경합에 한해 짧고 제한적으로 다시 조회할 수 있음
- 주문 생성·취소 등 쓰기 요청은 401이나 결과 불명 상태에서 자동 재제출하지 않음
- 성공 응답도 JSON 구조·필수값·수치 범위·주문 상태 검증
- 현재가 응답의 종목·가격·시각·시간대 검증
- 응답 시각이 5분보다 오래되거나 2분 이상 미래면 주문 계산 차단
- API 오류문자열을 shell 명령으로 사용하지 않음
- 유지보수·장애를 주문성공 또는 미보유로 변환하지 않음

## 17. SSH·Oracle

- `StrictHostKeyChecking=yes`
- 신뢰된 경로로 확인한 Oracle public host key만 `known_hosts`에 고정
- Actions에서 즉석 `ssh-keyscan` 결과를 자동 신뢰하지 않음
- `accept-new` 사용 금지
- host trust secret이 없으면 배포·runtime verifier 중단
- 서비스는 최소권한·비루트·private tmp/device·`UMask=0077` 등 hardening 유지
- DB·로그·cache만 승인된 shared write 경계 사용

## 18. rollback-safe 배포

- 최신 보호 `main`만 배포
- release-local venv에서 미리 설치·검증
- 서비스 정지 직후 SQLite `backup()` snapshot
- release atomic switch
- config/init-db/service/read-only smoke
- 실패 시 previous release + unit + DB snapshot 자동 복원
- rollback도 실패하면 신규 BUY 금지 및 수동 복구
- config version 변경은 표준 deploy로 임의 처리하지 않고 별도 migration PR 필요

## 19. Security workflow

- `pip-audit`: Python dependency 취약점
- `bandit`: Python 일반 보안 패턴
- CodeQL: code scanning
- Gitleaks: 전체 Git history secret scan
- Dependabot: Python·Actions 의존성 갱신
- 사용하지 않는 runtime 의존성은 제거하고 `pyproject.toml`과 `requirements.txt`의 직접 의존성을 테스트로 동기화
- Oracle과 CI 설치는 검토된 `requirements.lock` 전이 의존성 제약을 적용해 같은 commit의 설치 결과가 임의로 변하지 않게 함
- coverage 하한: 안전경계 테스트가 조용히 사라지는 것 방지

## 20. 실거래 오작동 방지 잠금

현재 정확한 상태는 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)를 확인합니다.

일반 설정 경로에서는 다음 잠금을 계속 유지합니다.

- `portfolio.live_enabled=false`
- 일반 프로그램 경로의 실거래 차단

승인된 별도 실거래 준비 전환 경로에서는 다음을 추가로 요구합니다.

- 별도 새 실거래 원장
- 실거래 준비 완료 표시
- 시작·재시작 시 신규 매수 잠금
- 계좌·원장 대조
- 신규 매수 2단계 승인

최종 주문 경계인 `OrderManager`도 실거래 준비 완료 표시와 신규 매수 잠금 값을 재검증합니다. 잠금 값이 `0` 또는 `1`이 아니거나 누락되면 잠금 해제로 간주하지 않고 주문을 차단합니다.

배포 성공, 조회 기능 확인, 문서 체크리스트 존재만으로 실거래 준비 완료라고 판단하지 않습니다.

실거래 준비 전환은 실제 계좌 사전점검·주문 연결부·회계·DB 이전·복구 연습과 별도 명시 승인을 요구합니다.

## 21. 변경 전 체크리스트

- [ ] secret이나 계좌정보를 새로 노출하지 않는가
- [ ] 관리자 private/chat/from_user 검증이 유지되는가
- [ ] BUY approval TTL·1회성·stale 차단이 유지되는가
- [ ] batch preflight가 계산 freshness·SELL·정합성·합계한도를 확인하는가
- [ ] 동시 클릭·중복 batch가 차단되는가
- [ ] 최종 클릭 직전 reconciliation과 한도 재검사가 있는가
- [ ] 순차 제출 중 실패 시 이후 BUY가 중단되는가
- [ ] 위험축소 SELL 미완료 뒤 BUY가 차단되는가
- [ ] 주문 멱등성·부분체결 delta·receipt 검증이 유지되는가
- [ ] dry-run과 Toss read-only가 분리되는가
- [ ] SAFE_MODE를 쉽게 우회하지 않는가
- [ ] SSH host key가 고정되어 있는가
- [ ] DB snapshot과 rollback이 가능한가
- [ ] live hard lock을 사용자 명시 승인 없이 약화하지 않는가
