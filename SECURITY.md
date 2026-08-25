# Security Policy

JH_HOLDINGS의 상세 보안·주문 안전 기준은 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md)가 소유합니다. 이 파일은 GitHub에서 보안 기준을 빠르게 찾기 위한 진입점입니다.

## 핵심 원칙

- API key, Telegram token, SSH private key, 전체 계좌번호, 인증 header, approval raw token을 Git·로그·Markdown·Issue에 기록하지 않습니다.
- 공개 Markdown에는 서버 절대경로·OS 사용자명·서비스 실명·host 식별자·실제 backup 파일명을 기록하지 않습니다.
- `main`은 branch protection, PR, Quality/Security gate를 통과한 변경만 받습니다.
- Oracle SSH는 검증된 host key를 `known_hosts`에 고정하고 `accept-new`나 즉석 신뢰를 사용하지 않습니다.
- 기본 운영은 forced dry-run이며 배포 성공과 live 주문 활성화는 별개의 승인입니다.
- dry-run의 SQLite/모의 broker 원장과 실제 Toss read-only 계좌조회는 서로 다른 신뢰경계입니다.
- 위험축소 SELL은 BUY보다 먼저 처리하고, 미완료·부분체결·`UNKNOWN` 상태에서는 신규 BUY를 막습니다.
- 위험증가 BUY는 Telegram 운영자 승인을 요구합니다. 일괄 BUY는 최신 계산·세션·정합성·합계 HWM75 한도를 검사한 뒤 종목별로 순차 제출합니다.
- 일괄 순차 실행은 원자적 basket 주문이 아니며 중간 실패 시 이후 BUY를 fail-closed로 중단합니다.
- 주문 멱등성, 부분체결 delta, broker receipt 검증, reconciliation과 SAFE_MODE를 우회하지 않습니다.
- 최초진입 50→75→100 단계와 approval callback은 현재 DB 상태·TTL·단계를 다시 확인합니다.

## 보안 문제가 의심될 때

1. 실제 주문 위험이 있으면 live/BUY를 우선 차단합니다.
2. 노출 가능성이 있는 자격증명을 폐기·재발급합니다.
3. Gitleaks와 관련 로그로 범위를 확인합니다.
4. 불명확한 주문을 성공으로 추정하거나 재주문하지 않습니다.
5. 저장소 소유자에게 민감정보를 포함하지 않는 안전한 경로로 알립니다.

세부 체크리스트는 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md)를 따릅니다.
