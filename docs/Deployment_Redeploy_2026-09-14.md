# 로컬 Docker 재배포 — 2026-09-14

이 문서는 같은 날의 이전 배포 기록이다. 현재 구성과 기동은 [판정 근거·보류 설명 최신 재배포](Deployment_Analyst_Evidence_2026-09-14.md)를 따른다.

사용자 요청에 따라 이전 정상 배포 설정과 `moduagent` 모드를 복원하고 현재 작업 코드로 API·analysis worker·model-test worker·웹 UI를 재배포했다. GitHub 게시, 오케스트레이션 LLM 추가 및 실제 모델 재평가는 이번 작업에 포함하지 않았다.

## 현재 접속과 설정

- UI: http://127.0.0.1:18080
- API: http://127.0.0.1:18000
- 실행 모드: API·두 worker 모두 `moduagent`
- Production/Test: 기존 `gpt-5.4-mini` 지정 유지. 비활성 Gemma 프로필과 역할별 Verifier 설정도 보존.
- Compose: `.local-deploy/redeploy-20260914-EovpNl/compose.json`
- 이미지: `waf-ai-console-api:redeploy-20260914-EovpNl`, `waf-ai-console-frontend:redeploy-20260914-EovpNl`

작업 시작 시에는 기본 Compose의 `stub`, 8080/8000 포트로 네 컨테이너가 이미 실행 중이었다. 과거 중단 기록과 현재 상태가 달랐으므로 실제 컨테이너를 확인한 뒤 이전 비공개 배포 설정의 환경값·명령·포트·네트워크·볼륨을 사용했다. 비밀값은 출력하지 않았다. 최신 코드 빌드가 기존 빌드 캐시와 일치했으며 새 배포 태그를 붙이고 네 컨테이너를 재생성했다.

## 반영 내용과 보존

[보류 설명 개선](Decision_Explanation_and_Orchestration_2026-09-11.md), JSON 트리와 [요청 입력 검사 v1](Request_Integrity_v1_2026-09-11.md)을 포함하는 현재 코드다. 코드의 시스템 지침은 `waf-system-v2.9`, 판정 지침은 `waf-judgment-v2.9`, ModuAgent는 `0.6.2`다. 기존 편집 지침과 접수 당시 고정한 모델·지침 스냅샷은 변경하지 않았다.

SQLite backup API로 일관성 사본을 만들고 무결성·외래 키 검사 및 이전 암호화 키로 14,988개 값의 복호화를 확인했다. 재배포 직후 HTTP 확인 전에 19개 테이블의 전체 행 해시를 대조해 모두 동일함을 확인했다. DB 버전은 `0014_agent_configuration`이며 새 마이그레이션 적용이 필요하지 않았다.

분석 889건(완료 740·실패 149), Agent 실행 890개·단계 6,563개, LLM 프로필 2개, 편집 지침 2개, 서비스 키 2개와 테스트 실행 11개를 보존했다. 기본 분석 목록은 재실행을 제외해 830건이며 `include_retries=true` 조회는 889건이다. 이후 원문·보고서 등 정상 조회로 감사 이력이 추가됐다.

백업과 설정의 위치·권한·현재 재기동 명령은 [로컬 복구 자료](../.local-deploy/README.md)를 따른다. 기본 Compose 단독 실행으로 현재 설정을 덮어쓰지 않는다. DB 복원·삭제·다운그레이드는 수행하지 않았다.

## 검증

- 관련 백엔드 124개 테스트 통과: 보류 설명, 보고서 내보내기, 요청 입력 검사 및 worker 반영. 파싱 난이도/Hard/Medium 총 150건의 오프라인 입력 검사 포함.
- 프런트엔드 전체 31개 테스트 파일 통과, API·UI 이미지 빌드 완료.
- 이미지 안의 백엔드 102개 파일이 현재 소스와 일치함을 확인.
- 네 서비스 running, API healthy, 재시작 0회. 이전 환경값·모드·역할·포트·데이터 볼륨 일치 확인.
- HTTP 22개 확인 통과: UI·JS/CSS, readiness, 미인증 거부, 잘못된 Origin 거부, 이전 관리자 계정 로그인, 대시보드/Agent 설정/지침, 분석/원문/Agent 이력, 분석 PDF·Excel, 운영 API 정의서·PDF·OpenAPI, 로그아웃.

호스트의 기존 Python 가상환경 경로가 없어 백엔드는 네트워크와 운영 DB를 연결하지 않은 Python 3.12 임시 컨테이너로 검사했다. 최초 실행의 샘플 경로 누락을 읽기 전용 샘플 연결로 해결한 뒤 전부 통과했다. 목록 개수 점검은 기존의 기본 재실행 제외 규칙을 반영해 수정했다. 앱 코드 수정은 없었다. 기존 AnyIO 폐기 예정 경고 1개가 남아 있다.

실제 LLM 호출은 0회다. `moduagent` 기동과 기존 모델 지정은 확인했지만 OpenAI/Gemma 연결 품질·유료 재평가·실제 분석가 사용성은 이번 검증 결과로 주장하지 않는다.
