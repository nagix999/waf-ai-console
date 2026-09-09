# 기존 Gateway에 WAF 서브도메인 추가 — 수동 대안

기준: 2026-09-09. `team-workspace-platform`과 WAF를 **같은 Docker 호스트**에 신규 병행 설치할 때의 안내다. 실제 운영 서버에 적용한 기록은 아니다. 기본 [신규 설치 가이드](Production_Docker_Compose_Guide.md)의 비밀값 생성·빈 DB·모델 검증 절차도 따른다.

**권장 경로는 [환경 파일 기반 자동 배포](Automated_Team_Deployment.md)다.** WAF의 `.env.production`만 편집하고 스크립트를 실행하며, 팀 프로젝트의 코드·Compose·환경 파일은 수정하지 않는다. 아래 절차는 팀 프로젝트의 Gateway 설정을 직접 관리하기로 선택한 경우만 사용하는 수동 대안이다. 두 배포 방식을 섞지 않는다.

## 유지할 경계

```text
기존 방화벽/VIP :443 → 기존 Gateway :3030 (TLS)
                       ├─ 기존 포털·Jupyter → 변경하지 않음
                       └─ waf.cyberailabs.team → waf-web :80 → WAF API :8000
                              전용 공유 네트워크      WAF 전용 네트워크
```

- 회사/VPN 접근 제한은 기존 방화벽에서 관리한다. Gateway의 `$platform_ingress_allowed` 검사도 WAF 주소에 유지한다.
- DNS가 기존 VIP로 해석되고 인증서가 `*.cyberailabs.team`을 포함하면 재사용할 수 있다. 실제 DNS·인증서·만료와 SNI를 확인하며 새 인증서 발급을 자동 실행하지 않는다.
- WAF 로그인·DB·세션/암호화 키는 별개다. 기존 포털과 SSO를 연결하거나 키·DB를 공유하지 않는다.
- 기존 `*.cyberailabs.team`은 사용자 Jupyter 주소다. `waf` 사용자/호스트 충돌을 먼저 확인하고 새 사용자 등록에서도 서비스 이름으로 예약한다. 다른 사용자 데이터를 임의 변경하지 않는다.
- 기존 Compose의 `edge`·`control`·`jupyter`에 WAF를 연결하지 않는다. 공유 네트워크에는 기존 Gateway와 WAF 웹만 연결한다. WAF 웹 서비스명은 `waf-web`이므로 기존 `frontend` 서비스 DNS와 충돌하지 않는다.

기존 구성 근거: [검토한 운영 Compose](https://github.com/nagix999/team-workspace-platform/blob/6cee73318daad3442b5c6ad5c496a982dcc161f0/compose.production.yaml), [Gateway 설정](https://github.com/nagix999/team-workspace-platform/blob/6cee73318daad3442b5c6ad5c496a982dcc161f0/gateway/production.conf). 실제 배포 버전이 다르면 그 버전을 먼저 확인한다.

## 1. 네트워크 계획

아래 값은 **설명용 Docker 주소**다. 회사/VPN 허용망이 아니며 운영에서 사용 가능하다고 확인한 값도 아니다. 기존 Docker·호스트·VPN·vLLM 대역과 겹치지 않는 주소로 교체한다.

| 용도 | 예시 |
| --- | --- |
| Gateway↔WAF 공유 네트워크 | `waf-console-edge`, `172.30.250.0/24` |
| Gateway의 공유 네트워크 IP | `172.30.250.2` |
| WAF 내부 네트워크 | `172.30.251.0/24` |
| WAF 웹의 내부 고정 IP | `172.30.251.10` |

검토 후 전용 공유 네트워크를 한 번 생성한다. 동명 네트워크가 있다면 소유·설정·참여 컨테이너를 먼저 확인하며 삭제하거나 무조건 재사용하지 않는다.

```bash
docker network create --internal \
  --subnet 172.30.250.0/24 --gateway 172.30.250.1 \
  --ip-range 172.30.250.128/25 waf-console-edge
```

WAF의 비공개 `.env.production`에 검토한 값을 넣는다.

```dotenv
WAF_PUBLIC_ORIGIN=https://waf.cyberailabs.team
WAF_PRIVATE_SUBNET=172.30.251.0/24
WAF_PRIVATE_WEB_IP=172.30.251.10
WAF_TRUSTED_PROXY_IP=172.30.250.2
WAF_EDGE_NETWORK=waf-console-edge
```

`WAF_TRUSTED_PROXY_IP`는 **Gateway가 공유 네트워크에서 사용하는 IP**, `WAF_PRIVATE_WEB_IP`는 **WAF 웹이 API에 연결할 때 사용하는 IP**다. 두 값을 같은 값으로 지정하지 않는다.

공유망의 자동 할당 범위를 `/25`로 제한해 Gateway의 고정 주소 `.2`와 WAF 웹의 자동 주소가 경합하지 않게 한다. 대역을 바꾸면 고정 IP와 자동 할당 범위도 함께 검토한다.

## 2. WAF Compose와 정책

기본 가이드의 `waf_compose` 함수를 다음처럼 바꾼다. 기본 개발 Compose를 합치지 않는다.

```bash
waf_compose() {
  docker compose --env-file .env.production -p waf-ai-console-prod \
    -f docker-compose.production.yml \
    -f deploy/security/docker-compose.gateway.yml "$@"
}
python3 deploy/security/render_proxy.py --env-file .env.production
python3 deploy/security/render_proxy.py --env-file .env.production --check
waf_compose config --quiet
```

운영 웹 설정과 생성 정책은 읽기 전용 마운트다. 미설정·잘못된 peer 또는 HTTPS/IP 헤더는 거부한다. 공유 네트워크 방식에서도 기본 loopback 게시 포트는 남지만 Gateway는 이를 사용하지 않고, 승인 peer가 아닌 직접 접근은 거부된다. API host port는 없다. 더 넓은 주소에 웹/API 포트를 게시하지 않는다.

## 3. 기존 Gateway의 검토 가능한 변경

다음은 기존 Gateway Compose에 반영할 **네트워크 부분 예시**다. 기존 `edge`의 고정 IP와 `ingress` 등은 그대로 보존한다. 파일 전체를 이 예시로 덮어쓰지 않는다.

```yaml
services:
  gateway:
    networks:
      edge:
        ipv4_address: 172.29.3.10  # 검토한 기존 값; 실제 배포 값 보존
      ingress: {}
      waf-edge:
        ipv4_address: 172.30.250.2
networks:
  waf-edge:
    external: true
    name: waf-console-edge
```

[WAF 전용 server 예제](../deploy/security/gateway-waf.server.conf.example)를 기존 `gateway/production.conf`의 server들과 나란히 추가한다. 기존 TLS 기본 거부·포털·Jupyter·접근망 검사를 지우지 않는다. 이 Gateway는 `/etc/nginx/conf.d`를 읽지 않고 빌드한 템플릿에서 설정을 생성하므로, 컨테이너 내부에 임시 파일을 추가하는 방식으로 배포하지 않는다.

WAF 주소의 `/` 전체를 `waf-web:80`으로 전달한다. `/api/`만 기존 포털 API로 보내면 안 된다. Gateway는 클라이언트가 준 전달 헤더를 이어 붙이지 않고 관측한 IP·HTTPS로 덮어쓴다. Docker 내장 DNS를 요청 시 재조회하므로 WAF 이름이 아직 없다는 이유만으로 Gateway 기동이 실패하지 않게 한다. 이 설정은 Docker Gateway용이며 호스트 Nginx에 `127.0.0.11`을 그대로 쓰지 않는다.

새 Gateway 이미지를 기존 프로젝트의 승인된 빌드·검증 절차로 준비하고, 기존 운영 스크립트의 Gateway 전용 재생성 절차를 사용한다. `scripts/production.sh recreate-gateway`는 빌드를 대신하지 않는다. 검토하지 않은 전체 서비스 재배포로 확장하지 않으며, Gateway 교체 중 기존 HTTPS/Jupyter WebSocket이 끊길 수 있으므로 점검 시간을 확보한다. **이 저장소의 예제 추가는 기존 프로젝트를 자동 수정하거나 재배포하지 않는다.**

## 4. 공개 전 확인

1. 기본 가이드대로 빈 DB로 WAF API·웹을 시작하고 내부 DB readiness를 확인한다. 아직 worker를 통한 모델 테스트는 접수하지 않는다.
2. 승인된 경로에서 WAF HTTPS·로그인·로그아웃·PDF 다운로드를 확인한다. 잘못된 Origin·다른 Host·위조 XFF는 거부되어야 한다.
3. Gateway의 원본 client IP 보존 여부와 WAF 로그의 IP를 대조한다. VIP가 SNAT하는 경우 임의 헤더로 원본 IP를 추정 복원하거나 광범위한 프록시 신뢰를 추가하지 않는다.
4. 방화벽의 허용/비허용 출발지에서 실제 접근 제한을 확인한다. 로그인 과다 요청은 승인된 테스트에서만 429를 확인한다.
5. 기존 포털 로그인·Jupyter 연결을 확인하고, WAF만 중단/재생성했을 때 기존 사이트 및 Gateway 기동에 영향이 없는지 점검 환경에서 확인한다.
6. 쿠키 없는 수집기의 API Key 호출·조회·리뷰를 가상 입력으로 확인한다. 실제 모델·사내 원문 전송은 별도 승인 후 수행한다.

서로 다른 호스트에 설치한다면 이 Docker bridge 예제를 사용하지 않는다. 서버 간 비공개 경로·방화벽·TLS를 별도로 설계해야 한다.
