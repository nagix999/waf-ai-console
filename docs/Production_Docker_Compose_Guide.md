# 운영서버 Docker Compose 배포 가이드

기준: 2026-09-08 최신 소스, 애플리케이션 v0.2.0, DB `0013_analysis_retries_keys`.

이 가이드는 **빈 DB로 시작하는 신규 설치**, 단일 Linux 서버·사내 접근망·HTTPS 프록시를 기준으로 한다. 설치 절차이며 보안 승인이나 운영 성능 검증을 대신하지 않는다. [기존 보안 점검](Security_Assessment_2026-09-07.md)의 미조치 항목과 실제 배포 이미지의 취약점을 검토한 뒤 운영 트래픽을 연결한다. 인터넷에 관리 화면을 직접 공개하지 않는다.

## 1. 신규 설치 구성

```text
관리자 / 운영 수집기 → 사내 HTTPS 프록시 :443
                         → 127.0.0.1:18080 → frontend → api:8000
                                                        ↕
                                                같은 호스트의 SQLite
                                                        ↕
                                              worker / model-tester
                                                        → 별도 vLLM
```

이 저장소는 vLLM/GPU 서버를 설치하거나 시작하지 않는다. GPU는 별도 vLLM 추론 서버에서 사용하며, 애플리케이션 컨테이너에는 GPU 장치를 연결하지 않는다.

새 DB에는 분석·테스트·모델 프로필·서비스 API 키가 없다. 프롬프트와 입력 스키마는 첫 조회 시 기본 v1로 초기화된다. 개발 환경의 지침 v2·모델 지정·분석 이력은 가져오지 않는다. 신규 설치에서 사용할 비밀번호와 암호화 키를 새로 생성한다.

## 2. 서버 준비와 소스 받기

- Docker Engine과 Compose 플러그인, Git. 신규 비밀값 생성 예시는 Python 3 표준 라이브러리만 사용한다.
- SQLite 데이터는 이 서버의 로컬 디스크에 보관한다. NFS/SMB 또는 여러 호스트가 같은 파일을 공유하는 구성은 사용하지 않는다.
- 유효한 사내 도메인·TLS 인증서와 HTTPS 프록시, 허용할 관리자/수집기 네트워크를 준비한다.
- 빌드 서버에서는 GitHub·이미지 레지스트리·PyPI·npm 접근이 필요하다. 폐쇄망 운영서버는 7절의 이미지 전달 방식을 사용한다.

```bash
docker version
docker compose version
git clone --branch main https://github.com/nagix999/waf-ai-console.git
cd waf-ai-console
git rev-parse HEAD
```

비공개 저장소이므로 승인된 GitHub 인증이나 읽기 전용 배포 키를 사용한다. 토큰을 clone URL에 넣지 않는다. 검토한 커밋으로 배포하고 커밋 SHA를 배포 기록에 남긴다. **최초 `v0.2.0` 태그가 아니라 이 가이드가 포함된 최신 검증 커밋**을 사용한다. 태그를 이동하거나 기존 이력을 덮어쓰지 않는다.

이후 명령을 간단히 쓰기 위한 함수다. 저장소 루트의 같은 Bash 세션에서 사용하며 새 터미널에서는 다시 정의한다.

```bash
waf_compose() {
  docker compose --env-file .env.production -p waf-ai-console-prod -f docker-compose.production.yml "$@"
}
```

**`docker-compose.production.yml`은 단독 파일이다.** 기본 `docker-compose.yml`과 합치지 않는다. 기본 파일의 개발 포트를 남기는 실수를 피하기 위한 구성이다. Compose의 운영별 설정 원칙은 [Docker 운영 안내](https://docs.docker.com/compose/how-tos/production/)를 참고한다.

## 3. 신규 설치의 비밀 설정

다음 코드는 새 설치에서만 한 번 실행한다. 관리자 비밀번호·세션 비밀키·Fernet 암호화 키를 생성해 권한 0600의 `.env.production`에 기록한다. 값은 출력하지 않으며 이미 파일이 있으면 덮어쓰지 않는다.

```bash
python3 - <<'PY'
import base64, os, re, secrets, subprocess
from pathlib import Path

template = Path('.env.production.example').read_text(encoding='utf-8')
tag = subprocess.check_output(['git', 'rev-parse', '--short=12', 'HEAD'], text=True).strip()
assert re.fullmatch(r'[0-9a-f]{12,40}', tag)
values = {
    'WAF_ADMIN_PASSWORD': secrets.token_urlsafe(32),
    'WAF_SESSION_SECRET': secrets.token_urlsafe(48),
    'WAF_DATA_ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    'WAF_BACKEND_IMAGE': 'waf-ai-console-api:' + tag,
    'WAF_FRONTEND_IMAGE': 'waf-ai-console-frontend:' + tag,
}
for key, value in values.items():
    template, count = re.subn(r'(?m)^' + key + r'=.*$', key + '=' + value, template)
    assert count == 1
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
with os.fdopen(os.open('.env.production', flags, 0o600), 'w', encoding='utf-8') as stream:
    stream.write(template)
print('Created .env.production (0600); secret values were not printed.')
PY
```

보안 편집기/비밀 저장소로 `.env.production`을 관리한다. 관리자 로그인에는 이 파일의 계정·비밀번호를 사용한다. 운영 비밀값을 채팅·티켓·GitHub·일반 로그에 붙여넣지 않는다.

| 설정 | 운영 기준 |
| --- | --- |
| `WAF_ADMIN_USERNAME` / `WAF_ADMIN_PASSWORD` | 단일 관리자 계정·고유한 강한 비밀번호 |
| `WAF_SESSION_SECRET` | 세션 서명용. 바꾸면 기존 로그인 세션 무효화 |
| `WAF_SESSION_HTTPS_ONLY` | `true`. 브라우저는 HTTPS 주소로 접속 |
| `WAF_DATA_ENCRYPTION_KEY` / `WAF_ENCRYPTION_KEY_VERSION` | DB와 함께 별도 안전한 저장소에 백업. 키 버전 이름만 바꾸는 것은 키 교체가 아님 |
| `WAF_AGENT_MODE` | 실제 분석은 `moduagent`. API·두 worker에 공통 적용 |
| `WAF_BACKEND_IMAGE` / `WAF_FRONTEND_IMAGE` | 같은 소스 커밋으로 만든 고유 이미지 태그 |
| `WAF_WEB_PORT` | 기본 18080, 호스트 loopback에만 연결 |

관리자 비밀번호·LLM 공급자 키·운영 수집기용 서비스 API Key는 서로 다른 인증정보다. 서비스 키와 vLLM 허용 대상은 환경변수로 자동 발급/등록하지 않는다.

```bash
waf_compose config --quiet
waf_compose build api frontend
```

검증에는 `config --quiet`를 사용한다. 일반 `config`, `config --environment`, 전체 `docker inspect`는 비밀값이 출력될 수 있다. 빌드는 API 이미지 하나를 세 backend 서비스가 공유한다. `.dockerignore`는 백엔드와 프런트엔드 모두 필요한 소스만 빌드 컨텍스트에 포함한다.

폐쇄망 대상 서버에서는 위 `build`를 실행하지 않는다. 7절에서 승인한 이미지의 반입과 정확한 태그 설정을 끝낸 뒤 4–6절 순서로 진행한다.

현재 Dockerfile은 베이스 이미지의 가변 태그 및 일부 전이 의존성을 사용한다. 같은 Git 커밋을 다른 날짜에 다시 빌드한 이미지가 동일하다고 보장하지 않는다. 승인한 이미지 ID/digest를 기록하고, 동일 태그를 덮어쓰거나 재빌드하면서 배포하지 않는다.

## 4. HTTPS·네트워크

운영 예시는 API의 host port를 열지 않는다. 웹은 `127.0.0.1:18080`에만 게시한다. **호스트의 HTTPS 프록시**가 여기에 연결한다. 외부 프록시가 별도 서버/컨테이너라면 그 환경에서 접근 가능한 비공개 연결 경로를 별도로 설계해야 한다. 프록시에서 `127.0.0.1`은 프록시 자신의 호스트/컨테이너다.

Docker 공식 문서에는 localhost 게시의 구버전 예외와 direct routing 설정에 따른 차이가 명시되어 있다. 최신 보안 패치가 적용된 Engine을 사용하고 방화벽에서도 접근을 확인한다. [Docker 포트 게시 안내](https://docs.docker.com/engine/network/port-publishing/)

프록시의 필수 설정:

- 실제 사내 도메인과 유효한 TLS 인증서, 허용 Host 외 요청 거부.
- 관리자·수집기 접근망 제한. 로그인 rate limit 등 인증 보호 정책.
- 업스트림 `http://127.0.0.1:18080`, 읽기 제한 시간 75초 이상, 본문 한도 11 MiB 수준. 앱의 파일 한도 10 MiB·payload 한도 2 MiB가 별도로 적용된다.
- UI·`/api/`·`/health/`·허용할 `/docs`·`/openapi.json`을 같은 HTTPS origin에서 전달.
- 요청 본문·Cookie·Authorization·X-API-Key를 프록시 로그에 기록하지 않기.

현재 앱에 `WAF_ALLOWED_HOSTS`나 `WAF_CORS_*` 같은 설정은 없다. 임의로 추가해도 보호되지 않는다. 내부 nginx는 전달받은 프로토콜 헤더를 HTTP 기준으로 다시 설정하므로 end-to-end HTTPS 인식을 보장하지 않는다. Secure 쿠키는 `WAF_SESSION_HTTPS_ONLY=true`로 별도 강제한다. 외부 TLS 프록시 구성 자체는 이 저장소가 자동 설치하지 않는다.

`/docs`·`/openapi.json`에는 입력 필드 설명이 공개되므로 접근망을 제한하고 비밀값을 스키마 설명에 쓰지 않는다. Swagger는 외부 CDN을 사용하므로 완전 폐쇄망에서는 열리지 않을 수 있지만 자체 정의서 화면·PDF는 외부 리소스를 사용하지 않는다.

컨테이너 outbound도 방화벽에서 vLLM 사설 IP/포트 등 승인 대상만 허용한다. vLLM의 앱 내 허용 목록은 방화벽을 대신하지 않는다. OpenAI를 사용한다면 별도 사내 반출 승인 및 공식 HTTPS 목적지 접근이 필요하다.

## 5. 신규 기동과 비모델 확인

**이 시점 이전에 HTTPS와 비밀 설정을 준비한다. 데이터 볼륨 이름은 `waf-ai-console-production-data`다.** 아래 조회에 동명 볼륨이 있으면 신규 설치를 중단하고 확인한다. 기존 볼륨을 임의 삭제하거나 새 암호화 키로 재사용하지 않는다.

```bash
docker volume ls --filter name=waf-ai-console-production-data --format '{{.Name}}'
```

```bash
waf_compose run --rm --no-deps -T api alembic upgrade head
waf_compose run --rm --no-deps -T api alembic current
waf_compose up -d --no-build --pull never api frontend
waf_compose ps
curl --fail --silent --show-error http://127.0.0.1:18080/health/ready
```

Alembic 현재 버전은 `0013_analysis_retries_keys`, readiness 응답은 `{"status":"ok"}`여야 한다. API 기본 기동 명령도 `upgrade head`를 수행하지만 worker 시작 전에 명시적으로 버전을 확인한다. readiness는 DB 접속 수준이며 GPU·worker 처리·판정 품질을 보장하지 않는다.

HTTPS 브라우저에서 로그인, Secure 쿠키, 설정의 프롬프트·입력 스키마 조회, `Production API` 화면과 PDF 다운로드를 확인한다. 신규 DB에서는 기본 스키마·지침 조회가 초기화·감사 기록을 만들 수 있다. 아직 테스트/분석 버튼은 누르지 않는다.

```bash
waf_compose up -d --no-build --pull never worker model-tester
waf_compose ps
```

빈 신규 DB에서 위 명령 자체는 분석을 만들거나 모델을 호출하지 않는다. 운영용 데이터 볼륨 이름은 **`waf-ai-console-production-data`**이며 세 backend가 공유한다. 같은 호스트에서 이 고정 이름으로 두 배포를 동시에 기동하지 않는다.

## 6. 모델·운영 연동 설정

1. **설정 → 프롬프트 / 입력 스키마**: 기본 활성 버전을 확인한다. 변경할 필요가 없다면 다시 저장·적용하지 않는다. Test와 Production은 공통 활성 프롬프트를 사용한다.
2. **설정 → 내부 연결 허용**: vLLM의 사설 IP·포트를 등록한다. 예: `10.0.0.10`과 `8000`. 호스트명, `vllm.internal`, `localhost`, `127.0.0.1`, CIDR은 사용할 수 없다. 같은 GPU 호스트라면 컨테이너에서 접근 가능한 호스트의 사설 IP를 사용한다.
3. **설정 → LLM 프로필 → 모델 추가**: 공급자 vLLM, Base URL `http://10.0.0.10:8000/v1` 형태와 서버가 실제 제공하는 모델 ID를 입력한다. 기본 후보는 `google/gemma-4-26B-A4B-it`, 컨텍스트 32,768, 최대 출력 3,072이다. 등록 자체는 모델을 호출하지 않는다.
4. 승인 후 **관리 → 전체 검증 → 연결·기능 검증만**을 실행한다. 빠른 테스트 통과만으로 역할을 지정할 수 없다. **150건 판정 평가는 선택 사항**이며 기본 설치 과정에서 자동 실행하지 않는다.
5. 통과한 모델에 **Test 지정**을 하고, 승인된 가상 입력 소수로 **테스트 분석 → 단건 분석**을 확인한다.
6. 운영에 사용할 모델에 **Production 지정**을 한다. 같은 검증 프로필을 두 용도에 지정해도 되며 같은 검증을 역할별로 두 번 할 필요는 없다. 서로 다른 프로필이면 각각 검증한다. 미지정 Test를 Production으로 대체하지 않는다.
7. **설정 → 서비스 API Key → 키 발급**: 키 이름·연동 시스템·최소 권한을 정한다. 수집기에는 `분석 접수·조회` 권한을 사용한다. 키 원문은 한 번만 표시되므로 비밀 저장소로 옮긴다.
8. **Production API → 최신 정의서 새로고침 → PDF 다운로드**에서 현재 계약을 수집기에 전달한다. 쿠키 없는 클라이언트로 새 서비스 키 인증을 확인하고, 승인된 가상 입력으로 운영 접수 경로를 확인한 후 실제 수집기를 연결한다.

**비용·데이터 주의:** `stub` 모드여도 모델의 빠른 테스트/전체 검증은 실제 LLM을 호출한다. OpenAI 프로필은 마스킹하지 않은 payload·Cookie의 외부 전송과 비용 승인이 필요하다. 150건 평가는 Verifier·재시도로 150회보다 많은 호출이 생길 수 있다. 이 가이드 작성과 GitHub 게시 작업에서는 실제 LLM 검증을 실행하지 않는다.

입력 스키마 변경은 새 수집기 요청을 거절할 수 있다. 변경 비교·샘플 검증·수집기 영향 확인 후 적용한다. 필드 정의는 추론 이력에 기록하며 필드 설명을 모델 입력에 추가하지 않는다. 기존 완료 결과와 접수된 작업의 당시 설정은 바꾸지 않는다.

## 7. 폐쇄망 신규 설치용 이미지 전달

승인된 빌드 환경에서 검증한 이미지 두 개를 준비해 태그·이미지 ID/digest와 함께 전달한다. 아래 태그는 예시이므로 실제 고정 태그로 바꾼다. 실행 중 컨테이너를 `docker commit`으로 포장하거나 DB·비밀 파일을 이미지에 넣지 않는다.

```bash
docker image save -o waf-ai-images.tar waf-ai-console-api:COMMIT_TAG waf-ai-console-frontend:COMMIT_TAG
sha256sum waf-ai-images.tar
# 승인된 보안 경로로 images.tar 및 공개 배포 파일 전달 후 대상에서:
sha256sum waf-ai-images.tar
docker image load -i waf-ai-images.tar
```

송신/수신 파일 해시와 대상 이미지 ID를 대조한다. 대상 `.env.production`에는 전달한 정확한 두 이미지 태그를 넣는다. 이후 `waf_compose up --no-build --pull never` 계열로 기동하고 운영서버에서 다시 빌드하지 않는다. Compose 파일의 상대 build 경로도 유효하도록 같은 Git 소스 디렉터리를 전달한다. 레지스트리를 사용한다면 승인된 내부 레지스트리의 digest로 고정할 수 있다.

## 8. 운영 전 확인표·검증 범위

- [ ] 사내 HTTPS·허용 Host·접근망·로그인 보호, API host port 비공개, 비밀값/원문 로깅 방지.
- [ ] DB 0013, 무결성/FK, 디스크 여유와 보관 용량, 백업·동일 키 복구 시험.
- [ ] API·두 worker 동일 이미지·모드·키·volume, 빈 DB의 신규 설치임을 확인.
- [ ] 현재 활성 지침·입력 스키마 및 같은 버전의 화면/OpenAPI/PDF 확인.
- [ ] 운영서버에서 승인된 실제 vLLM 검증, Test/Production 역할 명시 지정.
- [ ] 쿠키 없는 수집기 클라이언트의 서비스 키 인증·중복 접수·결과 조회 확인.
- [ ] 실제 GPU에서 구조화 출력, 지연시간, 부하·큐 복구, 판정 품질 확인. 150건 파일 점수를 운영 정확도로 간주하지 않기.
- [ ] 배포 이미지 취약점/사내 보안 점검의 미조치 항목 검토 및 승인.

운영용 Compose는 실제 파일을 해석해 필수 비밀값 누락 차단, 공통 backend 설정, 포트·볼륨·역할 분리를 검증했다. 이 가이드 작성 과정에서 운영서버 접속·TLS 설치·운영 LLM 호출은 수행하지 않았다. 현재 로컬 UI/기능 배포 검증과 실제 운영 검증은 구분하며, 이전 결과는 [최신 로컬 배포 기록](UI_Help_and_API_PDF_2026-09-08.md)에 있다.
