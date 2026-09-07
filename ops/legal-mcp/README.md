# Legal MCP 운영

`chrisryugj/korean-law-mcp`를 JH_HOLDINGS와 독립된 Oracle 런타임으로 배포하기 위한 최소 운영 래퍼입니다.

## 운영 기준

- upstream: `chrisryugj/korean-law-mcp`
- pinned commit: `ba10add7c6359ca1587b420153b7cd66eba28d30` (v4.12.3)
- Oracle path: `/opt/jh_legal_mcp`
- container: `jh-legal-mcp`
- endpoint: `http://127.0.0.1:3100` (loopback only)
- runtime limit: 512 MiB RAM, 1 CPU, 256 PIDs
- authentication token: 최초 배포 시 Oracle에서 랜덤 생성, `/opt/jh_legal_mcp/.env`에 mode 600으로 저장
- law API key: GitHub environment secret `LEGAL_MCP_LAW_OC`가 비어 있지 않을 때만 Oracle `.env`에 주입

법제처 키나 MCP 인증 토큰을 GitHub 코드, issue, Actions 로그에 출력하지 않습니다.

## 배포

`.github/workflows/deploy-oracle-legal-mcp.yml`을 수동 실행하거나 저장소 소유자가 제목이 `[deploy-legal-mcp]`로 시작하는 issue를 생성합니다. 기존 `oracle-dry-run` environment의 Oracle SSH 설정을 재사용하지만, JDSS 배포 디렉터리·프로세스·포트는 사용하지 않습니다.

법제처 Open API 키가 준비되면 `oracle-dry-run` environment에 `LEGAL_MCP_LAW_OC` secret을 추가한 뒤 재배포합니다. 키가 없는 상태에서도 서버와 health endpoint는 기동하지만 실제 법제처 조회는 키를 제공하는 호출 외에는 사용할 수 없습니다.

## 외부 연결 정책

현재는 외부에 포트를 개방하지 않습니다. ChatGPT 또는 다른 원격 MCP 클라이언트를 연결할 때만 HTTPS reverse proxy 또는 지원되는 secure tunnel을 별도 구성하고, 그 시점에 Oracle에 저장된 `MCP_AUTH_TOKEN`을 인증에 사용합니다. 3100 포트를 Oracle Security List/NSG에 직접 개방하지 않습니다.

## 확인

```bash
curl -fsS http://127.0.0.1:3100/health
sudo docker ps --filter name=^/jh-legal-mcp$
sudo docker logs --tail 100 jh-legal-mcp
sudo grep '^LAW_OC=' /opt/jh_legal_mcp/.env | sed 's/=.*/=<redacted>/'
```

업스트림 업데이트는 자동 추종하지 않습니다. 새 버전을 검토한 뒤 `deploy.sh`의 pinned commit만 명시적으로 변경하고 재배포합니다.
