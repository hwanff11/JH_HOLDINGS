# Security Policy

이 파일은 공개 저장소의 **보안정책 안내와 신고 원칙**만 소유합니다. 실거래 주문·DB·Telegram·SAFE_MODE의 상세 기술계약은 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md), 자동운용 자금·시작·정지 계약은 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md)가 단일 기준입니다.

## 공개 저장소 원칙

- API key, Telegram token, Toss key/secret, SSH private key, 전체 계좌번호, 인증 header, approval raw token을 Git·로그·Markdown·Issue·테스트 fixture에 기록하지 않습니다.
- 공개 Markdown에는 서버 절대경로, OS 사용자명, 서비스 실명, host 식별자, 실제 backup/snapshot 파일명, 일회성 Actions run ID를 장기 기록하지 않습니다.
- `main`은 보호된 브랜치이며 필요한 Quality/Security/Backtest gate를 통과한 변경만 병합합니다.
- 비밀정보 노출이 의심되면 관련 자격증명을 폐기·재발급하고 Gitleaks와 접근통제된 로그로 범위를 확인합니다.

## 실거래 안전의 최상위 원칙

- **배포와 최초 자동운용 시작은 별개**입니다. `/auto start` 전 신규 BUY는 차단되어야 합니다.
- 운영자 `/halt`는 시스템이 자동해제하지 않습니다.
- 주문 결과가 `UNKNOWN`이거나 계좌·원장 상태를 확정할 수 없으면 재주문보다 신규 BUY 차단과 실제 상태 확인을 우선합니다.
- 위험축소 SELL, 주문 멱등성, 부분체결 delta, broker receipt 검증, 계좌·원장 대조, SAFE_MODE를 우회하지 않습니다.

상세 체크리스트·주문 안전경계·복구조건은 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md)를 따릅니다.
