# 로컬 SonarQube 소스 진단

현재 개발 PC에서 Backend와 Frontend 및 배포 스크립트를 정적 분석합니다. 별도의 SonarQube Community Build와 PostgreSQL 17을 사용하며 WAF 서비스, SQLite, 운영 설정, Git은 변경하지 않습니다. 외부 소스 분석 서비스로 코드를 업로드하지 않습니다.

## 처음 실행

저장소 루트에서 실행합니다. Linux Docker Engine과 Compose 플러그인, Python 3, Docker 소켓 접근 권한 및 여유 디스크 8 GiB 이상이 필요합니다. SonarQube와 Scanner가 WAF 서비스와 함께 실행되므로 메모리 여유도 확인하세요. Compose의 메모리 상한은 SonarQube 4 GiB, Scanner 3 GiB, PostgreSQL 512 MiB, 접속용 Nginx 128 MiB입니다.

```bash
bash scripts/sonarqube.sh --check
bash scripts/sonarqube.sh
```

기본 명령은 설치 또는 기동부터 진단과 보고서 생성까지 진행합니다. 최초 실행 시 네 개의 공식 이미지를 다운로드하므로 인터넷과 충분한 다운로드 공간이 필요합니다. 기동과 첫 분석에는 몇 분이 걸릴 수 있습니다.

SonarQube의 Elasticsearch를 위해 다음 호스트 설정이 필요합니다. 현재 개발 PC에는 이미 적용했으며, **재부팅하면 다시 적용해야 할 수 있습니다.** 스크립트는 `sudo`를 실행하거나 영구 sysctl 설정을 수정하지 않습니다.

```bash
sudo sysctl -w vm.max_map_count=524288
```

설정이 부족하면 사전 점검이 중단되고 위 명령을 안내합니다. 다른 서비스를 중지하거나 기존 포트를 변경하지 않습니다.

## 접속과 로그인

- 주소: <http://127.0.0.1:19000>
- 사용자: `admin`
- 비밀번호 파일: 저장소 루트의 `.local-sonarqube/admin-password`
- 프로젝트: `WAF AI Console (local)`

비밀번호는 **안전한 로컬 편집기**에서 확인해 로그인하세요. 파일 내용을 터미널, 대화, 로그에 출력하거나 공유하지 않습니다. 초기 기본 비밀번호는 스크립트가 생성한 비밀번호로 변경합니다. `.local-sonarqube/scan-token`과 `postgres-password`도 자동 생성하며 비밀 파일은 권한 `0600`, 상태 디렉터리는 `0700`으로 관리합니다.

이미 설치한 환경에서 저장된 비밀번호와 서버 비밀번호가 다르면 임의 초기화하지 않고 중단합니다. 상태 파일을 삭제하거나 새로 덮어쓰지 말고 원래 파일을 복구하거나 관리자 화면에서 자격 증명을 확인하세요. DB 볼륨만 남아 있고 설치 설정이 없는 경우에도 새 설치로 덮어쓰지 않습니다.

기본 포트가 사용 중이라면 **첫 설치에 한해** 다른 포트를 지정할 수 있습니다. 설치 후에는 저장된 포트를 유지합니다.

```bash
bash scripts/sonarqube.sh --port 19001
```

웹 포트는 브라우저 접속용 Nginx가 `127.0.0.1`에만 바인딩합니다. 다른 PC에 공개하는 운영 구성이 아닙니다. SonarQube와 PostgreSQL은 직접 호스트에 포트를 공개하지 않습니다.

## 명령

| 명령 | 동작 |
| --- | --- |
| `bash scripts/sonarqube.sh` | 설치 또는 기동 후 현재 코드 진단 |
| `bash scripts/sonarqube.sh --check` | Docker, Compose, 호스트 설정, 디스크 사전 점검 |
| `bash scripts/sonarqube.sh up` | 접속용 Nginx, SonarQube와 DB 기동, 관리자 및 프로젝트 준비 |
| `bash scripts/sonarqube.sh scan` | 실행 중인 SonarQube로 현재 코드 새 진단 |
| `bash scripts/sonarqube.sh report` | 재스캔 없이 최근 제출한 분석의 보고서 저장 재개 또는 기존 보고서 경로 확인 |
| `bash scripts/sonarqube.sh status` | 컨테이너와 SonarQube 상태 확인 |
| `bash scripts/sonarqube.sh stop` | 접속용 Nginx, SonarQube와 전용 DB 중지, 데이터와 설정 보존 |

`scan`은 서버가 이미 실행 중이어야 합니다. 중지 후에는 `up` 또는 기본 명령을 사용하세요. 이 명령들은 WAF 배포 스크립트를 호출하지 않으며 `.env`를 읽거나 실행하지 않습니다.

서버에서 분석은 끝났지만 로컬 보고서 저장만 실패했다면 `report`를 사용하세요. 기존 작업 ID로 결과를 조회하며 Scanner를 다시 실행하거나 분석을 재접수하지 않습니다. JSON과 Markdown이 모두 있으면 경로만 안내하고, 일부 파일만 생성된 경우에는 기존 파일을 보존한 별도 재시도 폴더에 저장합니다.

## 이미지와 네트워크

최초 설치는 다음 공식 이미지 태그를 확인하고, 실제 다운로드한 이미지의 **digest를 `.local-sonarqube/settings.json`에 고정**합니다.

- `sonarqube:community`
- `postgres:17-alpine`
- `sonarsource/sonar-scanner-cli:latest`
- `nginx:1.30.4-alpine`

이후 실행은 저장된 digest를 재사용하며 자동으로 새 버전이나 DB 업그레이드를 적용하지 않습니다. 이 최초 digest 고정은 이미지가 해당 호스트에서 식별된다는 뜻이지, 이미지의 모든 취약점이 없다는 보증은 아닙니다. 버전 갱신은 기존 설정과 DB를 보존하는 별도 작업으로 검토해야 합니다.

인터넷을 사용하지 않는 첫 설치에는 `--offline`을 사용할 수 있습니다.

```bash
bash scripts/sonarqube.sh --offline
```

이 옵션은 이미지를 다운로드하지 않습니다. **첫 초기화 전에 네 공식 태그의 이미지와 유효한 RepoDigest가 로컬 Docker에 준비되어 있어야 합니다.** 일반적인 이미지 tar 파일 적재만으로 RepoDigest가 보존되는 것은 보장되지 않습니다. digest 정보를 확인할 수 없으면 안전하게 중단하며 임의 이미지로 대체하지 않습니다. 설치 후 고정된 이미지를 Docker 캐시에서 삭제한 경우에도 먼저 정확한 이미지를 준비해야 합니다.

전체 구성은 WAF와 다른 Compose 프로젝트 `waf-ai-console-sonar`를 사용하며 WAF 네트워크나 DB에 연결하지 않습니다. SonarQube, DB, Scanner는 전용 `internal` 네트워크 `diagnostics`에만 연결하므로 외부로 직접 통신하지 못합니다. 데이터는 전용 볼륨에 보존합니다. 이미지 다운로드는 Docker 호스트에서 수행합니다.

가벼운 Nginx 서비스 `browser`가 `diagnostics`와 별도의 접속용 `browser` 네트워크를 연결해 로컬 브라우저 요청을 SonarQube로 전달합니다. 이는 Chromium 같은 브라우저 엔진이 아니라 HTTP 프록시입니다. 프록시만 호스트의 `127.0.0.1:19000`을 공개하며 non-root, 읽기 전용 파일 시스템, 모든 Linux capability 제거와 접근 로그 비활성화를 적용합니다. SonarQube나 Scanner에 외부 네트워크를 추가하지 않고 로컬 화면 접근을 제공하기 위한 구성입니다.

## 어떤 코드가 전달되는가

저장소 전체를 컨테이너에 마운트하지 않습니다. 매 진단마다 **허용 목록으로 만든 소스 전용 스냅샷만 읽기 전용으로 전달**합니다.

- 대상: `backend/app`, `backend/alembic`, `backend/scripts`, `backend/tests`, `frontend/src`, `frontend/scripts`, `deploy`, `scripts`의 허용된 코드 확장자
- 추가 대상: Dockerfile, 개발 및 운영 Compose, `package.json`, Vite 설정, `pyproject.toml`, Alembic 설정
- 제외: `.env*`, 알려진 비밀 파일, 키 파일, DB, 로그, 캐시, 문서, 샘플, 목업, handoff, 빌드 결과, `backend/app/data`
- 보호: symlink 거부, 기존 출력 덮어쓰기 거부, 파일별 5 MiB 및 전체 100 MiB 한도, 파일별 SHA-256 기록

분류 검증 시 스냅샷은 약 741개 파일 규모였으며 실제 실행 시 현재 코드에 따라 달라집니다. Backend 테스트, Frontend의 `.test.mjs`, 배포 및 스크립트의 `test_*.py`는 테스트로 분리합니다. 운영 코드인 `backend/app/test_run_schemas.py` 같은 파일은 이름만 보고 제외하거나 테스트로 분류하지 않습니다.

허용 목록은 코드에 직접 적힌 비밀까지 탐지하거나 제거하는 도구가 아닙니다. 진단 대상 소스에 실제 키나 운영 로그를 넣지 마세요. 스냅샷과 보고서도 내부 자료로 취급하고 `.local-sonarqube`를 외부에 공유하지 않습니다.

## 결과 확인

전체 이슈는 SonarQube 프로젝트 화면에서 확인합니다. 진단이 끝나면 다음 로컬 파일도 생성됩니다.

```text
.local-sonarqube/
  settings.json
  admin-password
  postgres-password
  scan-token
  runs/<실행 시각과 임의 구분값>/
    manifest.json
    scanner.log
    report.json
    report.md
    source/
    scanner/
```

`manifest.json`은 파일 경로, 해시, 크기와 소스/테스트 분류이며 파일 본문은 포함하지 않습니다. `report.json`에는 주요 지표, Quality Gate와 이슈 및 Security Hotspot 각각 첫 최대 100개가 담깁니다. `report.md`는 주요 지표와 이슈 첫 최대 100개를 정리한 요약입니다. **전체 이슈를 내보낸 파일은 아니므로** 나머지는 웹 화면에서 확인하세요. Security Hotspot은 사람이 검토할 지점이지 확정 취약점이 아닙니다.

현재 Quality Gate는 진단 결과로만 표시하며 WAF 배포나 Git 업로드를 차단하지 않습니다. 발견된 코드도 자동으로 수정하지 않습니다. 특히 이전 실행을 재현하기 위해 보존한 고정 버전 엔진과 legacy 코드는 의도적인 중복일 수 있으므로 중복률을 낮추려고 임의 통합하거나 삭제해서는 안 됩니다.

## 이번 진단의 범위와 한계

- 소스의 버그, 보안상 검토할 코드, 중복과 유지보수 문제를 확인합니다.
- Scanner가 Backend/Frontend 테스트를 실행하지는 않습니다. **최초 진단에서는 커버리지를 측정하거나 보고서를 가져오지 않습니다.** SonarQube가 커버리지 `0%`를 표시해도 실측 결과로 해석하지 않습니다.
- Community Build 사용과 로컬 정적 분석 자체에는 유료 API 비용이 없습니다. 호스트의 메모리, 디스크와 이미지 다운로드 자원은 사용합니다.
- 의존 패키지의 알려진 CVE, 실행 중 서비스의 침투 시험, LLM 판정 품질이나 프롬프트의 정확도 검증을 대체하지 않습니다.

## 첫 설치 및 진단 기록 — 2026-11-16

로컬 환경에서 SonarQube `26.11.0.134175`와 SonarScanner CLI `8.4.0.16527`로 첫 서버 측 분석이 완료됐습니다. 첫 Scanner 작업에는 약 16분이 걸렸으며, 이후 진단 시간을 보장하는 수치는 아닙니다.

| 항목 | 첫 분석 값 |
| --- | ---: |
| 인덱싱한 파일 | 745개 |
| 스냅샷의 소스 / 테스트 파일 | 486개 / 259개 |
| 분석 지표의 코드 파일 | 475개 |
| 코드 줄 수 (`ncloc`) | 51,677 |
| 이슈 | 117개 |
| Bug / Vulnerability / Code Smell | 41개 / 21개 / 55개 |
| Security Hotspot | 0개 |
| 중복 줄 비율 | 8.6% |

위 이슈는 **도구의 탐지 결과이며 아직 개별 타당성을 검토하지 않았습니다.** Vulnerability 21개가 실제 취약점 21개로 확정됐다는 뜻은 아닙니다. 고정 엔진의 의도적인 중복도 정리 대상으로 자동 분류하지 않습니다. 커버리지는 이번에도 측정하지 않았으며 UI의 `0%`를 실제 테스트 커버리지로 사용하지 않습니다.

SonarQube와 DB 및 Scanner의 내부 네트워크 격리, 익명 프로젝트 조회의 `HTTP 401` 차단과 기존 WAF 서비스의 정상 상태를 확인했습니다. 서버 측 분석과 로컬 보고서 저장을 모두 완료했습니다. 첫 보고서는 `.local-sonarqube/runs/20261116-142806-f7be76/report.md`와 `report.json`이며, 이번에는 `issues-export.json`으로 전체 117개도 별도 내보냈습니다. 일반 실행의 로컬 요약은 첫 최대 100개를 기록하고 나머지는 웹 화면에서 확인합니다.

최초 기준선의 Quality Gate 상태는 `OK`였지만, 실제 평가된 조건 목록은 비어 있었습니다. **보안 합격이나 운영 배포 승인으로 사용하지 않습니다.** 배포 연동이나 이슈 자동 수정 및 상태 변경은 수행하지 않았습니다. 도구 자체의 오프라인 안전성 테스트 68개와 셸 문법 검사 및 Git diff 공백 검사를 통과했습니다. WAF의 전체 Backend/Frontend 테스트와 커버리지를 이번 설치에서 다시 실행한 것은 아닙니다.

## 첫 117건의 코드 검토와 보완

아래 건수는 첫 분석의 기준선입니다. 수정 후 사라진 경고 수를 뜻하지 않으며, SonarQube의 이슈 상태를 자동 변경하거나 규칙을 제외하지 않습니다. 언어가 다른 같은 번호는 함께 묶었습니다.

| 규칙 | 기준선 건수 | 보완 또는 유지 이유 |
| --- | ---: | --- |
| docker S6471 | 2 | API/worker UID 10001과 Nginx non-root 실행. 기존 볼륨은 중지·백업·보존 검사를 마친 뒤 제한된 도구로 권한 준비 |
| JS S2245 | 4 | 화면/접수 식별자 fallback이며 인증용이 아님. 인증에 재사용하지 않고 기존 crypto 우선 방식 유지 |
| JS S5332 | 3 | 합성 HTTP fixture와 내부 vLLM placeholder. OpenAI HTTPS 고정과 내부 대상 검사는 유지. 실제 운영망 전송 보호는 별도 확인 |
| JS S5443 | 1 | 시안 출력은 고유한 0700 폴더와 exclusive 0600 파일 사용. 기존 폴더의 소유권/권한을 임의 변경하지 않음 |
| Python S2068 | 4 | 합성 DB의 개발용 인증값. 실제 운영 비밀값을 벤치마크에 주입하지 않으며 개발 전용 제한 유지 |
| Python S2612 | 4 | scanner용 사본 0644/0755 유지. 소유한 0700 상위 폴더를 복사 전에 검사하고 검증된 디렉터리 FD에 고정 |
| Python S5443 | 3 | 허용 tmpfs 비교 1건과 네트워크 격리 fixture 로그 2건. 실제 공용 임시 파일 생성과 구분 |
| JS S2871 | 12 | 벤더 표시 정렬 1건만 locale 명시. 해시·키·근거 집합 정규화 11건은 환경과 무관한 기존 순서 유지 |
| JS S9011 | 12 | 실제 폼 제출 버튼에 type=submit 명시. type=button으로 일괄 변경하지 않음 |
| JS S9383 | 16 | 비동기 진입점과 callback 실패의 최종 경계 보강. 화면 전환·병렬 조회·중지·오래된 응답 차단 유지 |
| Python S5863 | 1 | 합성 소스 트리의 독립적인 예상 지문값 검사와 반복 실행의 결정성 검사를 구분 |
| JS/Python S1313 | 23 | 보안 CIDR 6, 변경 가능한 배포 기본값 5, 합성 fixture 11, placeholder 1. 실제 대상 등록/SSRF 경계를 약화하지 않음 |
| JS S2187 | 2 | 기존 assertion을 보존한 node:test 명시적 테스트로 전환. 테스트 파일 삭제/분석 제외 금지 |
| JS S2310 | 2 | Markdown backtick 구간을 건너뛰는 처리를 명시적 while 커서로 정리 |
| JS S6845 | 9 | 원문·표·JSON의 키보드 스크롤 진입점 유지. JSON에 명명된 region을 보강하고 실제 focus 동작 검사 |
| JS S6848 | 1 | 비대화형 wrapper 클릭 대신 각 native button이 명시적으로 메뉴를 닫음 |
| JS S6852 | 1 | 활성 tab으로 진입하는 기존 roving 패턴 유지. tablist 자체의 중복 Tab 정지점은 추가하지 않음 |
| JS S6853 | 3 | 표시 이름과 설명, id/label 연결을 명확히 하고 접근성 이름 검사 |
| JS S7767 | 4 | SHA-256의 32비트 정수 변환/넘침 처리이므로 Math.trunc로 변경하지 않음 |
| JS S7773 | 1 | Number.NaN으로 의도 명시 |
| JS/Python S8786 | 7 | 보고서/문서/JSON의 해당 정규식을 문자 순회로 교체하고 긴 합성 입력과 기존 문법 비교 |
| JS S9379 | 1 | 공통 Dialog의 초기 focus ref로 안전한 취소 버튼 선택. 닫힌 뒤 호출 버튼으로 복귀 |
| Python S9152 | 1 | yield 뒤는 분석 성공 확정이지 무조건 실행할 정리가 아님. 고정 worker를 finally로 바꿔 실패를 성공 처리하지 않음. 기존 소유권·lease 회수 검사 유지 |

고정 엔진 40개와 Backend 표시 처리 4개 파일은 바이트 단위로 보존합니다. 해당 경고의 의미 변경이 필요하면 구버전을 남긴 새 실행 계약과 공식 평가·운영 승인 경로를 거쳐야 합니다. 경고 수를 줄이기 위한 `NOSONAR` 일괄 추가나 legacy 전체 제외는 하지 않습니다.

배포용 소스 지문에서도 `.local-sonarqube`를 제외해 scanner 상태와 로컬 자격 증명을 소스 입력으로 읽지 않도록 했습니다. API 스키마와 DB migration, 모델·지침·운영 선택은 이번 보완에서 변경하지 않습니다. 컨테이너 실행 권한의 적용은 별도 재배포 때 이루어지며, 코드 수정만으로 실행 중 서비스가 교체되지는 않습니다.

재부팅하면 호스트의 일시적인 `vm.max_map_count`가 초기화될 수 있습니다. 이때 코드 테스트는 계속할 수 있지만 Sonar 재진단은 필요한 값을 다시 적용하기 전까지 완료할 수 없습니다. 미완료 scanner 기록을 완료 결과로 사용하지 않습니다.

재진단에서는 JavaScript 분석 중 Scanner가 기존 2 GiB 상한에 닿아 메모리 회수와 재읽기가 반복됐습니다. 해당 임시 컨테이너를 3 GiB로 늘린 뒤 메모리 압박이 줄고 분석을 완료했으므로 다음 실행의 기본 상한도 3 GiB로 조정했습니다. 분석 규칙이나 파일 범위를 줄이지 않았으며 WAF 서비스의 메모리 설정과는 별개입니다. 다른 작업과 함께 실행할 때는 호스트 여유 메모리를 먼저 확인하세요.

## 보완 후 최종 검증 — 2026-11-16

최종 진단 `20261116-173812-c2556f`는 17:50:42 KST에 완료됐습니다. Scanner 실행은 약 11분 24초였으며 소스 490개와 테스트 263개, 총 753개 파일의 해시가 당시 작업 트리와 모두 일치했습니다. 분석 규칙과 범위를 줄이거나 이슈 상태를 변경하지 않았습니다.

| 분류 | 최초 | 최종 |
| --- | ---: | ---: |
| Bug | 41 | 11 |
| Vulnerability | 21 | 18 |
| Code Smell | 55 | 38 |
| 합계 | 117 | 67 |

기존 경고 50개가 해소됐고 새 경고는 없습니다. 남은 67개는 고정 정렬 11개, SHA-256 정수 연산 4개, 키보드와 탭 포커스 10개, 보안 CIDR과 합성 IP 23개, 개발용 또는 조건부 보안 경고 18개, 고정 worker 구현 1개입니다. 위 검토 표의 유지 이유를 적용하며 경고가 남았다는 이유로 보안 경계를 낮추거나 고정 실행을 변경하지 않습니다.

Quality Gate는 **ERROR**입니다. `new_violations=0`과 `new_duplicated_lines_density=0` 조건은 통과했지만, 커버리지를 측정하거나 보고서를 가져오지 않아 `new_coverage=0 < 80` 조건이 실패했습니다. 이를 실제 테스트 커버리지 0%나 운영 배포 합격으로 해석하지 않습니다. 임계값을 내리거나 미측정 커버리지를 만들어 넣지 않았습니다.

검증 결과는 다음과 같습니다. 서로 겹치는 별도 검사는 전체 건수에 더하지 않습니다.

- Backend 전체: 테스트 4,678개를 겹치지 않는 범위로 실행하고 수정본 재검증을 반영해 4,657개 통과, 기존 실패 20개, 건너뜀 1개입니다. 기존 실패는 과거 버전 기대값 10개, 근거 정리 완료 상태가 없는 공식 평가/승격 fixture 6개, 참고 근거만 만들면서 정탐을 기대하는 mocked 결과 4개입니다. 관련 테스트와 helper 및 처리 코드 15개가 최초 소스와 Git HEAD에 동일함을 확인했습니다. 건너뛴 1개는 `/preview`의 선택형 PDF fixture가 없는 검사입니다.
- 별도 Backend 우선 회귀 397개와 실제 PDF 4개 통과. 오래된 문구와 현재 버전 연결을 바로잡은 테스트 파일도 각각 60개와 21개가 통과했습니다. 실제 지침이나 고정 엔진은 수정하지 않았습니다.
- Frontend 테스트 746개 전부와 production build, Node 22의 임시 출력 보호 테스트 10개 통과. 기존 대용량 JS chunk 빌드 경고는 남아 있습니다.
- 배포와 진단 도구 테스트 330개 중 324개 통과, 환경 조건부 6개 건너뜀. 셸 문법과 Git diff 공백 검사 통과.
- 최종 Backend/Web 이미지 빌드 통과. 격리한 합성 DB로 신규 migration과 root 소유 `0030 → 0031` 업그레이드, 기존 데이터 지문과 BLOB 보존, UID 10001 쓰기를 확인했습니다. Nginx UID 101의 읽기 전용 실행, 내부 포트 80과 접근 제한 `403`도 확인했습니다. `0600` 소스와 `0700` 폴더를 반입한 경우의 이미지 내 읽기 권한도 검증했습니다.
- 실제 재사용 컴포넌트의 Playwright 검사 7개 그룹 통과. 모달 초기 포커스와 복귀, 메뉴, radio/checkbox 이름, JSON 키보드 진입을 확인하고 SK/Light/Dark, KR/EN과 좁은 화면을 캡처했습니다. 배포 앱 전체 동선 검증이나 완전한 영문 번역 검증은 아닙니다.

전체 이슈는 `.local-sonarqube/runs/20261116-173812-c2556f/issues-export.json`, 비교 결과와 테스트 로그 및 스크린샷은 `.local-deploy/sonar-hardening-checks`에 보존합니다. 고정 실행 파일 44개와 Frontend 근거 처리 파일 3개는 최초 진단과 바이트 동일합니다. 공개 API v3, DB migration과 운영 의존성을 추가하거나 모델/지침/운영 설정을 변경하지 않았습니다. 실제 LLM 호출, 운영 데이터 사용, 실행 중 서비스 교체와 Git 업로드도 하지 않았습니다.
