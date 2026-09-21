# 기존 Docker 서비스 재배포

저장소 루트에서 실행합니다. **신규 설치용이 아니라, 이미 실행 중인 WAF 서비스를 현재 소스로 교체하는 스크립트**입니다. Linux, Python 3.10 이상(표준 라이브러리만 사용), Docker 접근 권한, Docker Compose v2가 필요합니다.

```bash
# 설정과 미완료 작업 확인만 — 서비스/DB를 변경하지 않음
bash scripts/redeploy.sh --check

# 실제 재배포 — 표시된 프로젝트 이름을 입력하면 시작
bash scripts/redeploy.sh
```

현재 로컬 배포는 `waf-ai-console`, API `127.0.0.1:18000`, 웹 `127.0.0.1:18080`, `moduagent`입니다. 스크립트에 이 값을 고정하지 않고 **기존 컨테이너의 Compose 정보를 찾아 재사용**합니다. 실행 중인 환경변수와 일치하지 않는 설정은 적용하지 않습니다. 암호화 키·로그인 설정·포트·DB 볼륨·모델·역할·지침·입력 스키마를 임의로 바꾸지 않습니다.

다른 서버나 프로젝트를 명시해야 한다면:

```bash
bash scripts/redeploy.sh --project waf-standalone --check
bash scripts/redeploy.sh --project waf-standalone
```

예전 Compose 경로가 없거나 env 파일을 자동으로 찾지 못할 때만 **기존 배포에 실제 사용한 파일**을 지정하세요. 포트만 바꿔 사용 중인 Compose도 그 파일을 지정하면 됩니다.

```bash
bash scripts/redeploy.sh --project waf-standalone \
  --file docker-compose.yml --env-file .env --check
```

검사 통과 후 `--check`를 빼고 실행합니다. 여러 Compose 파일이면 `--file`을 기존 순서대로 반복합니다. `.env.production`을 사용했던 환경은 그 경로를 지정하세요. env 파일을 새로 만들거나 키를 새로 발급하지 않습니다. 확인 질문을 생략하려면 `--yes`를 추가합니다.

## 실행 순서

1. 기존 컨테이너와 Compose의 설정·포트·볼륨·네트워크 일치, 미완료 분석·모델 검증 부재 확인.
2. 새 태그로 backend·frontend 빌드. API와 두 worker는 같은 backend 이미지를 사용. **이전 이미지는 삭제하지 않음.**
3. 미완료 작업 재확인 후 WAF 네 서비스만 중지. 이때부터 웹/API 접속이 잠시 끊김.
4. SQLite backup API로 일관된 DB 사본 생성. WAL 내용 포함. DB 무결성·외래 키 검사.
5. 백업의 별도 사본에서 `alembic upgrade head` 시험. 기존 테이블의 모든 기존 열·행을 해시로 대조.
6. 실제 DB에 같은 마이그레이션 적용 후 같은 보존 검사.
7. API healthy 확인 후 worker·웹 시작. 포트·환경·볼륨 보존, 웹 응답, **새 worker의 heartbeat** 확인.

현재 소스는 `0022_production_lifecycle`까지 적용합니다. API 시작 시 기존 운영 구성의 기준 스냅샷을 남기는 것은 앱의 기존 V5 동작이며, Candidate를 운영에 자동 승격하지 않습니다. 모델·지침·스키마 변경은 기존 승인·승격 경로를 사용합니다.

`git pull/push`, Gateway 수정, `docker compose down -v`, 볼륨/이미지 삭제, 키 재생성, 실제 LLM 검증을 수행하지 않습니다. 단, 서비스가 다시 열리면 새 분석 요청이나 기존 복구 대상에 의해 모델 호출이 발생할 수 있습니다. 재배포 중 수집기 요청은 일시 중지하거나 수집기에서 재시도하도록 준비하세요. 시작 전 미완료 작업이 있으면 강제 취소하지 않고 중단합니다.

이미지 빌드는 Dockerfile의 기반 이미지·npm·Python 패키지를 받을 수 있어야 합니다. **완전한 오프라인 이미지 패키징 기능은 아닙니다.** 필요한 캐시가 없고 다운로드도 불가능하면 서비스 중지 전 빌드 단계에서 끝납니다. Chromium을 설치하지 않습니다.

## 백업과 오류 처리

매 실행마다 `.local-deploy/redeploy/run-.../`를 만들고 다음을 보존합니다.

- `backup.db`: 실제 마이그레이션 전 백업. `rehearsal.db`는 시험용이므로 혼동하지 마세요.
- `baseline.json`: 기존 열·행의 건수와 해시. 원문 값은 포함하지 않음.
- `previous-compose.json`, `previous-containers.json`: 이전 실행 구성.
- `compose.json`: 새 실행 구성. 다음 재배포에서도 컨테이너가 이 파일을 참조함.
- `state.json`: 마지막 진행 단계.

**이 폴더에는 암호화 키·인증 설정과 분석 데이터가 포함됩니다.** 디렉터리는 700, 비밀 구성·백업은 600 권한으로 보관하고 Git에서는 제외합니다. 접근을 제한한 별도 저장소에도 백업하세요. 현재 컨테이너가 참조하는 `compose.json`은 지우지 마세요. DB만 있고 기존 암호화 키가 없으면 원문 복구가 불가능합니다.

실패·중단 시 자동 DB 복원, downgrade, 구버전 시작을 하지 않습니다. 빌드 중 실패는 기존 서비스에 영향이 없고, 서비스 중지 이후 실패는 일부 서비스가 중지되거나 새 버전으로 실행된 상태일 수 있습니다.

1. 출력된 폴더의 `state.json`과 다음 명령으로 먼저 상태를 확인합니다. 경로와 프로젝트 이름은 실제 출력값으로 대체하세요.

   ```bash
   docker compose --env-file /dev/null -p waf-ai-console \
     -f .local-deploy/redeploy/run-실제값/compose.json ps -a
   ```

2. **실제 DB 마이그레이션 단계에 들어가기 전** 중단됐다면, 같은 경로의 `previous-compose.json`으로 기존 서비스를 다시 시작할 수 있습니다. 진행 기록이 모호하면 실행하지 말고 DB 버전부터 확인하세요.

   ```bash
   docker compose --env-file /dev/null -p waf-ai-console \
     -f .local-deploy/redeploy/run-실제값/previous-compose.json \
     up -d --no-build --pull never
   ```

3. **실제 DB 마이그레이션 단계 진입 이후**에는 구버전을 바로 시작하거나 백업을 덮어쓰지 마세요. 현재 DB까지 별도로 보존하고 오류 원인·적용된 버전·이후 접수된 데이터를 확인한 뒤 새 버전 복구 또는 검토된 수동 복원을 진행합니다.

단일 호스트, `/data/waf.db` SQLite named volume, API·analysis worker·model-test worker·웹 각 1개 배포를 지원합니다. 같은 DB를 쓰는 다른 실행 컨테이너가 있거나 구성 비교가 불일치하면 멈춥니다. 기존 Gateway는 건드리지 않으며 외부 DNS/TLS/방화벽·로그인·실제 LLM 품질은 별도로 확인해야 합니다.

## 작성 시 확인한 범위

2026-09-21 기준 쉘 문법 검사, 합성 SQLite WAL 백업·변경 감지·실패 중단 순서 및 공통 파일 보호 테스트 **26건 통과**. 실행 중인 로컬 Docker에서 `--check`도 통과했습니다. **실제 이미지 빌드·컨테이너 교체·운영 DB 마이그레이션·LLM 호출은 수행하지 않았습니다.** 배포 전체 흐름의 테스트는 Docker 명령을 대체한 모의 실행이며, 실배포 성공을 확인한 결과는 아닙니다.
