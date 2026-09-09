# 기존 팀 서비스에 WAF 자동 배포

실행 중인 `team-workspace-platform`과 **같은 Linux·Docker 호스트**에 WAF를 신규 설치하는 방법이다. WAF의 `.env.production`만 편집하면 배포 스크립트가 연결 설정을 만든다. 실제 운영서버에 적용한 기록은 아니다.

**기존 팀 프로젝트의 코드·Compose·환경 파일은 수정하지 않는다.** 다만 HTTPS 진입점을 공유하므로 기존 Gateway의 실행 이미지·네트워크를 교체하며, 그동안 포털·Jupyter 연결이 잠시 끊길 수 있다. 점검 시간을 잡고 진행한다.

## 1. 준비할 것

- 같은 서버에서 정상 실행 중인 팀 프로젝트와 해당 소스 디렉터리.
- Docker Engine **27.1.2 이상**, Docker Compose **2.24.4 이상**, Bash, Python **3.9 이상**, Git, `ip`, `curl`, `openssl`. 별도 Python 패키지 설치는 필요 없다. Docker Engine 27 환경도 검사 대상이다.
- Docker를 실행하고 WAF 디렉터리에 쓸 수 있는 운영 계정. Docker 권한은 호스트 관리 권한에 해당하므로 승인된 계정만 사용한다.
- `waf.cyberailabs.team`을 기존 HTTPS 진입 주소로 연결하는 DNS와 해당 이름에 유효한 인증서. 기존 와일드카드 DNS·인증서가 처리하면 재사용할 수 있다.
- 기존 사용자/서비스와 `waf` 이름이 겹치지 않는지 확인. 기존 Jupyter 사용자가 `waf`를 쓰고 있다면 먼저 이름 충돌을 해결한다. 신규 사용자 등록에서도 이 이름을 예약한다.
- 다른 Docker·회사·VPN·vLLM 네트워크와 겹치지 않는 전용 Docker 대역.

DNS 등록, 인증서 발급·신뢰 설정, 회사 방화벽 변경은 스크립트가 하지 않는다. 기존 방화벽과 Gateway 접근 제한은 유지한다. vLLM 서버와 GPU도 별도 운영하며 이 배포에 포함하지 않는다.

현재 검토한 팀 소스는 `6cee73318daad3442b5c6ad5c496a982dcc161f0`의 운영 Gateway 구조다. 실행 중인 이미지·원본 설정·보안 점검 스크립트가 검토 범위와 다르면 자동화를 중단한다. 이를 통과시키기 위해 운영 소스를 임의로 업데이트하거나 점검 코드를 우회하지 않는다.

## 2. 소스 받기

```bash
git clone --branch main https://github.com/nagix999/waf-ai-console.git
cd waf-ai-console
git rev-parse HEAD
```

이 자동화가 포함된 검토한 커밋을 사용한다. 최초 `v0.2.0` 태그만 선택하면 이후 보안·자동화 변경이 빠질 수 있다. 현재 공개 저장소지만 접근 정책상 인증이 필요하면 기존 승인된 인증을 사용하고 토큰을 URL이나 명령에 넣지 않는다.

빌드 모드의 신규 설치에는 이미지 레지스트리·PyPI·npm 접근이 필요하다. 폐쇄망은 아래 기존 이미지 모드의 소유·소스 일치 조건에 맞게 빌드·반입을 별도로 준비한다. 기존 팀 프로젝트를 `git pull`하거나 수정할 필요는 없다.

## 3. 환경 파일 한 번 편집

`.env.production`이 없는 새 설치에서만 복사한다. 이미 있으면 기존 파일을 보존하고 편집한다.

```bash
cp -n .env.production.example .env.production
chmod 600 .env.production
vi .env.production
```

우선 다음 값을 입력한다.

```dotenv
WAF_TEAM_PROJECT_DIR=/실제/경로/team-workspace-platform
WAF_TEAM_PROJECT_NAME=기존_Compose_프로젝트명
WAF_ADMIN_USERNAME=admin
WAF_ADMIN_PASSWORD=본인만_사용하는_강한_새_비밀번호
WAF_PUBLIC_ORIGIN=https://waf.cyberailabs.team
```

프로젝트 경로·이름·비밀번호 예시는 반드시 실제 값으로 바꾼다. 비밀번호는 16자 이상으로 만들고 영문 소문자·대문자·숫자·기호 중 세 종류 이상을 섞는다. 팀 프로젝트명은 디렉터리 이름과 다를 수 있으며, 아래 조회에서 기존 Gateway 행의 프로젝트명 값을 사용한다.

```bash
docker ps --filter label=com.docker.compose.service=gateway \
  --format 'table {{.Names}}\t{{.Label "com.docker.compose.project"}}'
```

한 줄에 `WAF_이름=값` 하나를 쓰고 설명은 별도의 `#` 주석 줄에 쓴다. 값 뒤에 주석을 붙이거나 셸 명령·환경변수 치환을 넣지 않는다. 단순 따옴표 감싸기는 허용하지만 값 자체의 작은따옴표·역슬래시는 지원하지 않는다. 환경 파일·팀 프로젝트 경로의 심볼릭 링크도 허용하지 않는다.

파일을 `source`하거나 비밀값을 터미널·채팅에 출력하지 않는다. 관리자는 이 파일의 계정으로 WAF에 로그인한다. 기존 포털의 로그인과 SSO로 연결되거나 비밀번호를 공유하지 않는다.

### 나머지 설정

| 설정 | 자동 배포에서의 의미 |
| --- | --- |
| `WAF_SESSION_SECRET`, `WAF_DATA_ENCRYPTION_KEY` | 빈값이면 최초 `deploy`에서만 생성·보관한다. 직접 지정한 값은 새 값으로 바꾸지 않는다. |
| `WAF_ENCRYPTION_KEY_VERSION` | 기본 `prod-v1`. 이름만 바꾸는 것은 저장 데이터의 키 교체가 아니다. |
| `WAF_AGENT_MODE` | 기본 `moduagent`. 모델 등록·검증·용도 지정은 설치 후 별도로 한다. |
| `WAF_PRIVATE_SUBNET`, `WAF_PRIVATE_WEB_IP` | 빈값 기본은 `172.30.251.0/24`, `172.30.251.10`. WAF 웹과 API 사이의 내부망이다. |
| `WAF_EDGE_NETWORK`, `WAF_EDGE_SUBNET`, `WAF_EDGE_IP_RANGE` | 기본 `waf-console-edge`, `172.30.250.0/24`, `172.30.250.128/25`. Gateway와 WAF 웹만 연결하는 전용 내부 bridge다. |
| `WAF_TRUSTED_PROXY_IP` | 빈값 기본은 `172.30.250.2`. 공유망에서 Gateway가 사용할 고정 IP이며 자동 할당 범위 밖이어야 한다. |
| `WAF_BACKEND_IMAGE`, `WAF_FRONTEND_IMAGE` | `--build-mode local`에서 사용할 승인된 기존 이미지. 자동화 소유·소스 라벨이 현재 설치와 같아야 한다. 기본 빌드 모드는 소스 내용으로 고유 태그를 생성한다. |

Docker 주소의 기본값은 회사·VPN 접근 허용망이 아니다. 최초 설치 전 충돌이 있으면 위 대역·고정 IP·자동 할당 범위를 함께 바꾸고 다시 점검한다. 이미 존재하는 동명 네트워크나 DB 볼륨을 이 설치 소유라고 추정해 인수하지 않는다.

최초 설치 후에는 팀 프로젝트 경로·이름, Docker 대역·IP·네트워크명·웹 포트를 설치 식별값으로 고정한다. 이 값이나 저장 데이터의 암호화 키를 바꾸는 네트워크 이관·키 교체 작업은 자동 재배포 범위가 아니므로 별도로 검토한다.

**자동 배포가 채운 기본값·키는 원본 `.env.production`에 다시 쓰지 않는다.** 생성된 실행 설정을 사용해야 하므로 이 문서의 배포 명령과 수동 `docker compose ...` 명령을 섞어 쓰지 않는다.

## 4. 점검 후 배포

```bash
./scripts/deploy.sh check
```

`check`는 파일과 실행 상태를 조회하는 사전 점검이다. 키 생성·네트워크 생성·컨테이너 생성/재시작을 하지 않는다. 팀 프로젝트·Gateway 식별, TLS와 기존 사이트의 HTTPS 응답, 설정·주소·기존 배포와의 충돌 등을 확인한다. 포트 충돌 검사는 loopback에 잠시 bind한 뒤 해제하며 수신 서버를 띄우지 않는다. 오류가 있으면 안내된 원인을 먼저 해결한다. 사내 DNS·VIP를 포함한 외부 접속 전체나 실제 사용자별 Jupyter 연결의 정상을 보장하지는 않는다.

오류가 없고 점검 시간이 확보되었으면 실행한다.

```bash
./scripts/deploy.sh deploy
./scripts/deploy.sh status
```

`deploy`는 다음 범위에서 작업한다.

1. 사전 조건을 다시 확인하고 최초 설치의 미지정 키를 생성한다.
2. 기존 Gateway의 이미지·실행 설정을 복구 자료로 보관한다.
3. 현재 Gateway 이미지를 기반으로 원래 템플릿에 WAF 주소만 추가한 파생 이미지를 만든다.
4. WAF 네트워크·프록시 정책·전용 Compose 실행 설정을 준비하고 이미지를 준비한다.
5. Nginx 사전 검사를 수행하고 WAF를 기동한다.
6. **같은 팀 Compose 프로젝트의 Gateway만** 새 설정으로 교체하고 접속 상태를 확인한다.
7. 교체 이후 오류가 나면 저장한 Gateway 설정으로 복구를 시도하고 성공·실패를 구분해 알린다.

기존 팀 API·포털·Jupyter·DB를 재배포하거나 팀 전체에 `down`을 실행하지 않는다. 데이터 볼륨도 삭제하지 않는다. 재실행 시 성공 상태와 입력·소스가 같으면 불필요한 재배포를 피하며, 변경이 있으면 별도 배포 기록을 만든다.

### 환경 파일을 다른 곳에 두는 경우

```bash
./scripts/deploy.sh check --env-file /안전한/경로/waf.env
./scripts/deploy.sh deploy --env-file /안전한/경로/waf.env
./scripts/deploy.sh status --env-file /안전한/경로/waf.env
```

같은 설치를 관리할 때는 항상 같은 WAF 저장소와 환경 파일을 사용한다. 실행 옵션은 `./scripts/deploy.sh --help`로 확인한다.

### 승인된 기존 이미지 재사용

`local` 모드는 **현재 설치와 같은 소유·소스 라벨을 가진 WAF 자동 빌드 이미지**만 받는다. 해당 API·웹 이미지의 고정 태그를 `.env.production`에 넣는다.

```bash
./scripts/deploy.sh check --build-mode local
./scripts/deploy.sh deploy --build-mode local
```

누락된 이미지를 자동 pull하지 않는다. `io.waf.deploy.owner`와 `io.waf.deploy.source` 라벨이 현재 설치의 소유값·소스 지문과 다르면 거부한다. 소유값은 WAF 설치 절대경로를 기준으로 하므로 다른 경로에서 만든 일반 수동 이미지나 예전 소스의 이미지에 태그만 바꿔 붙여서는 사용할 수 없다.

폐쇄망 반입 자체는 [이미지 전달 안내](Production_Docker_Compose_Guide.md#7-폐쇄망-신규-설치용-이미지-전달)를 따르되, 먼저 **대상 설치 경로·동일 소스와 실행 권한까지 맞춘 승인된 자동화 빌드**를 준비해야 한다. 이 명령은 임의 반입 이미지를 자동 승인하거나 라벨을 보정하지 않는다. Gateway 파생 이미지는 현재 서버에 있는 Gateway 이미지를 기반으로 로컬 생성한다. 키·DB·로그가 포함된 컨테이너를 `docker commit`해서 반입하지 않는다.

## 5. 배포 후 확인

브라우저에서 WAF HTTPS 주소와 기존 포털·Jupyter를 확인한다.

- WAF 관리자 로그인·로그아웃, 설정 조회, 운영 API 정의서와 PDF 다운로드.
- 기존 포털 로그인과 Jupyter 연결·재연결.
- 사내 허용/비허용 네트워크에서 접근 제한, 인증서 신뢰, 실제 접속 IP 전달.

이 자동화는 신규 분석이나 150건 검증을 접수하지 않는다. **다만 이미 작업이 있는 재배포에서는 worker 시작으로 대기 작업과 모델 호출이 재개될 수 있다.** 모델 테스트를 자동 실행하지 않는다는 뜻과 대기 작업을 중단해 둔다는 뜻은 다르다.

이후 [모델·운영 연동 설정](Production_Docker_Compose_Guide.md#6-모델운영-연동-설정)을 따라 vLLM 내부 IP/포트 등록 → 프로필 전체 검증 → Test·Production 지정 → 서비스 API Key 발급을 진행한다. 실제 모델 호출·외부 전송·비용은 별도 승인 후 확인한다.

## 6. 상태 확인과 복구

```bash
./scripts/deploy.sh status
./scripts/deploy.sh rollback
```

`status`는 읽기 전용이며 마지막 관리 상태와 현재 Gateway가 달라졌는지도 확인한다. 자동 감시·상시 복구 서비스는 설치하지 않는다.

`rollback`은 저장한 **직전 Gateway 실행 이미지·설정**으로 돌아가는 명령이다. WAF DB를 삭제하거나 스키마를 되돌리는 전체 애플리케이션 롤백이 아니다. 기존 사이트 복구를 우선하며 WAF 컨테이너·데이터·전용 네트워크는 보존한다. 복구 명령도 Gateway를 재생성하므로 짧은 접속 중단이 발생할 수 있다.

자동 복구는 Docker 장애·디스크 부족·인증서 변경 등으로 실패할 수 있다. 실패 안내가 나오면 반복 배포보다 `status`와 저장된 배포 기록을 확인하고 운영 담당자가 복구한다. `.local-deploy`를 지우거나 `down -v`를 실행해 해결하려고 하지 않는다.

이미지·후보 설정 검사에 실패하면 Gateway를 교체하지 않는다. WAF 기동 단계에서 실패한 경우 Gateway는 그대로지만, 재배포 중이던 WAF API·worker는 변경되거나 멈춰 있을 수 있다. Gateway 복구가 성공해도 WAF 애플리케이션 상태까지 원복됐다는 의미는 아니다.

### 자주 만나는 중단 안내

| 오류 코드 | 확인할 내용 |
| --- | --- |
| `env_regular_file_mode_0600_required` | 환경 파일이 일반 파일인지 확인하고 권한을 0600으로 맞춘다. |
| `team_helper_version_requires_review` | 현재 팀 배포의 보안 점검 스크립트가 지원 범위와 다르다. 버전 호환 검토가 필요하다. |
| `existing_waf_resource_not_owned` | 기존 수동 설치나 다른 배포의 네트워크·볼륨이 있다. 삭제해서 우회하지 말고 이관 계획을 세운다. |
| `existing_encryption_state_missing` | 기존 암호화 키 상태가 없다. 새 키를 만들지 말고 백업을 확인한다. |
| `waf_subnet_overlaps_host_route`, `waf_subnet_overlaps_docker_network` | 신규 설치의 Docker 대역을 겹치지 않게 다시 정한다. |
| `https_health_or_certificate_check_failed` | 운영 계정의 인증서 신뢰, 기존 Gateway 바인딩 주소 및 회사/VPN 접근 정책을 확인한다. |
| `interrupted_gateway_switch_run_rollback` | 직전 전환이 끝나지 않았다. `rollback`으로 저장된 Gateway 복구를 시도한다. |
| `gateway_recovery_failed_manual_review_required` | 자동 복구도 확인에 실패했다. Gateway가 중지됐을 수 있으므로 운영 담당자의 복구가 필요하다. |

### 나중에 팀 프로젝트를 업데이트한다면

기존 팀 스크립트만으로 Gateway를 새로 만들면 WAF 연결이 빠질 수 있다. 원본 파일을 수정하지 않는 방식의 운영상 제약이다.

1. 승인된 절차로 팀 프로젝트를 업데이트한다.
2. WAF 저장소에서 `./scripts/deploy.sh status`로 상태 차이를 확인한다.
3. `./scripts/deploy.sh check` → `./scripts/deploy.sh deploy`로 현재 Gateway에 WAF 연결을 다시 적용한다.
4. 기존 사이트와 WAF를 모두 확인한다.

현재 Gateway 구조가 지원하는 템플릿·실행 방식과 달라졌으면 자동 적용을 멈추고 검토한다. 이전 이미지로 새 팀 배포를 덮어쓰거나 기존 팀 환경 파일을 WAF 환경 파일로 바꾸지 않는다.

## 7. 비밀값·복구 자료 보관

자동화 자료는 WAF 저장소의 `.local-deploy/automation` 아래에 보관한다. 입력 환경 파일과 팀 원본 파일을 덮어쓰지 않는다.

- `secrets.json`: 최초 생성한 세션·데이터 암호화 키. 권한 0600.
- `generations/`: 배포별 실행 설정·Gateway 복구 자료. 민감한 설정 파일은 0600, 디렉터리는 0700으로 제한한다.
- 상태 파일: 현재 관리 중인 배포와 단계·복구에 필요한 정보.

입력 `.env.production`, 자동화 자료, WAF DB를 안전한 별도 저장소에 백업하고 키·DB를 함께 복구할 수 있어야 한다. 생성 Compose에도 비밀값이 포함될 수 있으므로 GitHub·채팅·공유 로그에 올리지 않는다. `docker compose config`나 전체 `docker inspect` 출력을 그대로 공유하지 않는다.

기존 WAF 볼륨이 있는데 이 자동화의 상태·키가 없다면 신규 설치로 진행하지 않는다. 키를 새로 생성해 덮어 연결하면 기존 원문을 복호화할 수 없다. 최초 도입은 빈 DB 신규 설치용이며 기존 수동 배포를 자동 인수하거나 데이터 이관하는 기능은 아니다.

## 검증 범위

코드·가상 설정·격리 테스트와 실제 운영 배포를 구분한다. 실제 팀 서비스의 배포 버전, DNS·TLS·VIP 경로, 회사 방화벽, 사용자별 Jupyter 연결과 운영 GPU의 모델 동작은 해당 서버에서 확인해야 한다. 이 가이드 작성과 자동화 개발만으로 운영 배포·보안 승인·성능 검증이 완료된 것은 아니다.

Gateway의 `/healthz`는 Nginx 응답 확인이며 포털 로그인·API·Jupyter 전체 동작 검증이 아니다. Jupyter 격리는 기존 검사 코드의 정적 조건만 조회하고, 통신 차단을 시험하는 컨테이너는 만들지 않는다.
