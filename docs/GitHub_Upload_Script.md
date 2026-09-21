# GitHub 업로드 스크립트

저장소 폴더에서 실행합니다. Python 3.10 이상, Git 2.34 이상, 대상 GitHub 저장소에 쓸 수 있는 기존 인증이 필요합니다. GitHub CLI 설치는 필수가 아닙니다. SSH 키 또는 Git credential helper를 사용하며 토큰을 명령어·remote URL·소스 파일에 넣지 마세요.

```bash
# 네트워크 연결 없이 파일·Git 설정 검사만
bash scripts/github-upload.sh --check

# 변경 목록을 확인하고 커밋한 다음 현재 브랜치에 업로드
bash scripts/github-upload.sh --message "Add deployment and GitHub upload scripts"
```

저장소·대상 브랜치·변경 파일을 표시하고 `소유자/저장소:브랜치`를 입력받습니다. 커밋할 변경이 없으면 `--message` 없이 기존 커밋만 올릴 수 있습니다. 이미 검토한 자동 실행에만 `--yes`를 사용하세요.

현재 환경은 일반 `.git`을 사용할 수 없어 `.local-deploy/canonical-v5-reviewed.git`을 자동 탐색합니다. 원래 `.git`은 변경하지 않습니다. 현재 브랜치는 **`feature/canonical-v5-reviewed`**이며 기본 업로드도 이 브랜치입니다. **`main` 반영, PR 생성, GitHub Release 생성, Docker 재배포는 하지 않습니다.** 일반 clone 환경에서는 그 저장소의 정상 `.git`과 현재 브랜치를 사용합니다.

## 선택 옵션

```bash
# 특정 원격 브랜치로 올릴 때 — 자동 checkout/merge가 아님
bash scripts/github-upload.sh --branch feature/canonical-v5-reviewed \
  --message "Update canonical V5 deployment tooling"

# 릴리스 준비를 마친 경우에만 태그 추가 (자동으로 버전 파일을 바꾸지는 않음)
bash scripts/github-upload.sh --message "Prepare v0.3.0" --tag v0.3.0

# 별도 Git 위치나 remote를 명시해야 할 때
bash scripts/github-upload.sh --git-dir .local-deploy/canonical-v5-reviewed.git \
  --remote origin --check
```

`--branch main`을 명시하면 현재 브랜치의 커밋을 원격 `main`에 바로 반영하려고 시도합니다. 보호 규칙과 권한에 따라 거절될 수 있으므로 보통 기본 브랜치 업로드 후 GitHub에서 PR로 검토·병합하세요. 브랜치 보호를 우회하지 않습니다. `--tag`는 명시했을 때만 생성하며 같은 커밋을 가리키는 기존 태그는 유지하고 다른 커밋의 태그는 덮어쓰지 않습니다.

## 보호 동작과 한계

- 스테이징된 기존 변경이나 진행 중인 merge/rebase가 있으면 중단합니다. 기존 index를 임의로 비우지 않습니다.
- 추적 중인 변경과 새 소스 파일을 목록으로 보여주고 커밋합니다. 수정·삭제도 포함됩니다. 목록 전체를 검토하세요. 일부분만 올리고 싶다면 필요한 파일을 직접 커밋한 뒤 작업 폴더가 정리된 상태에서 실행하세요.
- `.gitignore`를 존중하며 `.env`, DB, 백업, 비밀 설정 폴더, 패키지 생성물 등을 제외합니다. 환경 설정 예제 `.env.example`·`.env.production.example`은 대상에 포함되므로 실제 비밀 값을 넣으면 안 됩니다.
- 알려진 API Key·개인 키 패턴과 100 MiB 이상 파일을 검사합니다. 새로 올릴 **중간 커밋**도 확인해 나중에 지운 비밀 파일이 이력으로 전송되는 것을 차단합니다.
- **완전한 비밀정보 탐지기는 아닙니다.** 임의 비밀번호·토큰, 이미지 속 정보, 실제 운영 로그를 전부 판별할 수 없습니다. 실제 사내 데이터·민감정보가 포함되지 않았는지 직접 확인해야 합니다.
- 실제 실행 시 원격 브랜치를 fetch하고 원격 변경이 로컬에 없으면 중단합니다. 자동 pull/merge/rebase/reset/강제 push는 하지 않습니다. fetch의 강제 갱신 옵션은 로컬 원격 추적 정보에만 적용되며 GitHub 이력을 덮어쓰는 force push와 다릅니다.
- 브랜치와 명시한 태그만 `--atomic`으로 push합니다. 모든 브랜치·태그·safety branch를 한꺼번에 올리지 않습니다.
- 검토 이후 파일 내용이 달라지면 중단합니다. 실행 도중 다른 터미널이나 편집기에서 같은 저장소를 변경하지 마세요.
- 앱 테스트나 LLM 평가를 대신 실행하지 않습니다. 기능 검증과 배포 준비는 별도입니다.

`--check`는 로컬 검사입니다. 인증·원격 최신 상태·브랜치 보호·push 권한은 실제 실행에서 확인합니다. push 실패나 연결 중단 시 **로컬 커밋·태그·스테이징은 남을 수 있고 원격 반영 여부가 불명확할 수 있습니다.** 자동 reset·태그 삭제·재시도를 하지 않으므로 먼저 GitHub와 로컬 상태를 확인하세요. 스테이징이 남았다면 내용을 검토해 직접 커밋한 뒤 다시 실행하면 됩니다. Git 원문 오류는 비밀 값이 노출되지 않도록 숨깁니다.

## 검증 범위

2026-09-21 기준 전용 테스트 **12건 통과**. 일회용 로컬 Git 저장소에서 커밋·브랜치/태그 atomic push·원격 변경 감지·기존 스테이징 보존·중간 커밋의 비공개 파일 차단을 확인했습니다. 현재 프로젝트에서도 `--check`를 통과했습니다. **실제 GitHub 인증·업로드·브랜치 보호 규칙은 확인하지 않았고, 현재 프로젝트에 커밋·태그를 생성하지 않았습니다.**
