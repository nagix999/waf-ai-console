# main 반영과 Docker 재배포를 한 번에 실행

현재 작업 내용을 GitHub `main`에 반영하고 **명령을 실행한 서버의 기존 WAF Docker 서비스**를 교체합니다. 다른 운영서버에 SSH로 접속해 배포하거나 Gateway를 수정하지 않습니다.

```bash
# 사전 검사만: GitHub에 연결하지 않고 Git·현재 Docker 설정 확인
bash scripts/deploy.sh --check

# 변경 확인 → 커밋 → main 반영 → Docker 재배포
bash scripts/deploy.sh
```

표시된 저장소·변경 파일·Docker 프로젝트·포트를 확인한 뒤 `nagix999/waf-ai-console:main`을 입력하면 전체 순서가 이어집니다. 실제 저장소가 다르면 화면에 표시되는 값을 입력하세요. 변경이 있으면 기본 메시지 `Deploy current changes to main`으로 커밋하고, 변경이 없으면 기존 커밋을 사용합니다.

```bash
# 커밋 설명을 지정할 때
bash scripts/deploy.sh --message "Update canonical V5 deployment"

# 기존 Docker 프로젝트를 직접 지정할 때
bash scripts/deploy.sh --project waf-standalone

# 릴리스 준비를 마쳤을 때만 태그도 명시적으로 추가
bash scripts/deploy.sh --tag v0.3.0
```

이미 대상을 검토한 자동 실행은 `--yes`로 확인 입력을 생략할 수 있습니다. 태그는 옵션을 지정했을 때만 추가하며 버전 파일을 자동으로 변경하지 않습니다. 기존 태그를 다른 커밋으로 이동하지 않습니다.

## 실행 순서

1. Git 상태·비공개 파일·키 패턴을 검사하고 Docker 설정·DB 볼륨·미완료 작업을 확인합니다.
2. 저장소·변경 목록·`main` 반영·현재 서버의 일시 중단에 대해 한 번 확인받습니다.
3. 원격 최신 상태를 fetch합니다. 현재 브랜치가 원격 `main`의 모든 커밋을 포함할 때만 진행합니다.
4. 확인한 변경을 커밋하고 `main`을 해당 커밋으로 fast-forward 반영합니다. 명시한 태그가 있으면 함께 atomic push합니다. **로컬 작업 브랜치는 바꾸지 않습니다.**
5. 원격 반영을 확인한 커밋의 파일을 별도로 추출합니다. Docker 이미지는 이 파일로 빌드하므로 이후 작업 폴더의 수정 사항은 섞이지 않습니다.
6. Docker 대상·설정·미완료 작업을 다시 확인하고 기존 서비스가 실행 중인 상태에서 새 이미지를 빌드합니다.
7. WAF 서비스를 중지하고 DB 백업 → 사본 마이그레이션 시험 → 실제 마이그레이션 → 기존 데이터 보존 검사를 수행합니다.
8. API 준비 상태 확인 후 worker·웹을 시작하고 새로운 worker heartbeat와 웹 응답을 확인합니다.

`main`과 현재 브랜치가 갈라졌다면 자동 merge/rebase하지 않습니다. 먼저 변경을 검토·통합해야 합니다. 브랜치 보호로 push가 거절되면 PR 등 저장소 정책을 따라야 하며 Docker 단계는 실행하지 않습니다. 강제 push·전체 브랜치 업로드·자동 운영 모델 승격은 하지 않습니다.

앱 전체 테스트나 실제 LLM 평가는 자동 실행하지 않습니다. 이미지 빌드·마이그레이션·서비스 상태 확인과 모델 품질 검증은 별개입니다. 서비스가 다시 열리면 접수된 분석에 따라 모델 호출이 발생할 수 있습니다.

## env 오류와 기존 설정

기존 Compose·env 경로는 실행 중인 컨테이너에서 확인합니다. 파일을 옮긴 경우에만 기존 파일의 실제 위치를 지정하세요.

```bash
bash scripts/deploy.sh --project waf-standalone \
  --file docker-compose.yml --env-file /실제/경로/.env --check
```

자동 재배포가 생성한 Compose는 환경변수 값을 이미 고정하므로 `--env-file /dev/null`을 사용합니다. `/dev/null`은 누락된 env가 아니라 **추가 env 파일을 읽지 않는 정상 설정**이며 이를 허용하도록 보완했습니다. Docker에 기록된 상대경로는 원래 배포 폴더를 기준으로 찾고, 사용자가 지정한 상대경로는 현재 터미널 폴더 기준으로 찾습니다.

실제 env가 없으면 기존 설정을 추정하거나 빈 파일을 생성하지 않고 중단합니다. **암호화 키를 새로 만들거나 `.env.example`로 덮어쓰면 안 됩니다.** 현재 컨테이너와 키·모드·포트·볼륨이 일치해야 진행합니다.

## 실패 시

- 사전 검사 실패: GitHub·서비스·DB 변경 없음.
- GitHub 반영 실패: Docker 빌드·중지 없음. 로컬 커밋·태그·스테이징은 남을 수 있음.
- 이미지 빌드 실패: GitHub `main`은 이미 반영됐지만 기존 서비스는 계속 실행됨.
- DB 이전 또는 재기동 실패: 서비스가 일부 중지되거나 새 버전으로 실행된 상태일 수 있음. 자동 DB 복원·downgrade·구버전 기동 없음.

GitHub와 Docker 전체가 하나의 원자적 작업은 아닙니다. **배포 실패 때문에 GitHub `main`을 자동으로 되돌리지 않습니다.** 연결 중단 시 원격 반영 여부부터 확인하세요. 원격 상태·DB 이전 단계가 불명확한 상태에서 그대로 반복 실행하거나 DB를 덮어쓰지 마세요.

통합 실행 기록과 커밋 소스는 `.local-deploy/release/run-.../`, Docker의 비밀 설정·DB 백업·상태 기록은 `.local-deploy/redeploy/run-.../`에 보존합니다. Git에는 포함하지 않습니다. 현재 배포가 참조하는 구성·소스 폴더는 삭제하지 마세요. DB 복구는 [재배포 복구 안내](Docker_Redeploy_Script.md)를 따릅니다.

## 기존 스크립트와 호환성

| 명령 | 동작 |
| --- | --- |
| `bash scripts/deploy.sh` | GitHub `main` 반영 + 현재 서버 Docker 재배포 |
| `bash scripts/deploy.sh --check` | 위 작업의 로컬 사전 검사만 |
| `bash scripts/github-upload.sh` | GitHub 업로드만; 기본 대상은 현재 브랜치 |
| `bash scripts/redeploy.sh` | 현재 작업 폴더로 Docker 재배포만 |
| `bash scripts/deploy.sh gateway check` | 기존 회사 Gateway 연동 사전 검사 |
| `bash scripts/deploy.sh gateway deploy` | 기존 회사 Gateway 연동 배포 |

과거 `scripts/deploy.sh check / deploy / status / rollback` 명령은 **기존 Gateway 기능 그대로** 유지합니다. 통합 배포를 하려면 뒤에 `deploy`를 붙이지 말고 인자 없이 실행하세요. 통합 검사 `--check`와 과거 Gateway 검사 `check`는 서로 다릅니다.

Linux, Python 3.10+, Git 2.34+, Docker Compose v2, 기존 GitHub 인증과 Docker 권한이 필요합니다. 신규 설치·원격 서버 배포·이미지 오프라인 패키징은 포함하지 않습니다. 기반 이미지·npm·Python 패키지가 캐시에 없으면 빌드 시 다운로드가 필요합니다. Chromium을 추가하지 않습니다.

## 작성 시 검증

2026-09-21 기준 통합 순서·실패 중단·커밋 소스 추출·경로 보호·env 경로·기존 업로드/재배포 관련 테스트 **56건 통과**. 현재 프로젝트에서 `bash scripts/deploy.sh --check`를 실행해 `/dev/null` 기록과 `moduagent`, API 18000·웹 18080 포트 호환성을 확인했습니다. 쉘 문법 검사도 통과했습니다.

통합 순서의 Docker 동작은 모의 실행으로 검증했고 Git push 검증은 일회용 로컬 저장소만 사용했습니다. **이 작업에서는 실제 GitHub 연결·커밋·push·이미지 빌드·DB 이전·재배포를 수행하지 않았습니다.** 전체 실배포 성공과 운영 네트워크·LLM 품질은 별도 확인이 필요합니다.
