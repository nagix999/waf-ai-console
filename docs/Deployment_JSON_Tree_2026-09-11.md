# JSON 트리 UI Docker 재배포 — 2026-09-11

같은 날 Agent API의 문자열형 입출력 지원 누락을 보완해 다시 배포했다. 최신 UI는 아래 **Agent 이력 문자열 JSON 후속 보완** 절을 따른다. 앞선 배포의 범위·검증 기록은 그대로 보존한다.

로컬 `waf-ai-console`의 프런트엔드만 교체했다. JSON 트리·타입별 색상·전체 검색/복사 UI를 반영하고, API·두 worker·DB·LLM/지침 설정은 변경하지 않았다. 요청 입력 검사 v1 및 공통 지침 v2.9 백엔드는 이번 배포에 포함하지 않는다.

- UI: http://127.0.0.1:18080
- API: http://127.0.0.1:18000
- 새 프런트엔드: `waf-ai-console-frontend:json-tree-20260911-Ki6v18`
- 기존 프런트엔드: `waf-ai-console-frontend:parser-stress-20260910-pRO7SD` — 복구용으로 유지
- API·두 worker: `waf-ai-console-api:context-review-20260910-fQQoxX` 유지

기존 Nginx 이미지 위에 새 정적 파일만 추가했다. Nginx 설정·포트·환경값·볼륨·네트워크·재시작 정책을 유지한다. 열려 있던 탭을 위해 이전 해시 이름의 정적 파일도 보존했다. DB 마이그레이션·백업 복원·분석 접수·재실행·LLM 호출은 하지 않았다.

## 검증

- 직전 UI 구현 검증: 프런트엔드 테스트 30개 파일 통과, Chromium 합성 화면에서 트리·키보드·검색·복사·다크/모바일·대용량·깊이 제한 확인.
- 재배포용 Vite 빌드 성공. JS 505.05 kB에 대한 500 kB 번들 크기 경고는 남아 있다.
- 제공 중인 HTML/JS/CSS와 배포 빌드 파일이 바이트 단위로 일치하고 새 JSON 표시 코드·스타일을 포함한다.
- UI 및 API readiness HTTP 200, 프런트엔드 경유 미인증 `/api/v1/auth/me` HTTP 401.
- Nginx 설정 검사 성공 및 설정 파일 SHA-256 일치.
- API·두 worker의 컨테이너 ID·이미지·시작 시각·재시작 횟수·실행 설정 지문 일치. 프런트엔드 실행 중, 재시작 0회.
- 초기 설정 지문 검사는 Docker가 빈 `Dns`, `DnsOptions`, `DnsSearch`를 `[]` 대신 `null`로 기록하여 실패했다. 이전 컨테이너 ID와 원래 지문이 일치하는 비공개 기준 자료로 대조해 이 세 기본값만 정규화한 재검증을 통과했다. 실제 DNS 값이나 다른 설정 변경은 없다.

현재 배포의 실제 관리자 세션을 이용한 상세 화면 클릭·실제 모델 품질 평가는 별도로 수행하지 않았다. 기능 브라우저 검증은 합성 화면이며, 배포 후 검증은 실제 제공 파일과 HTTP·컨테이너 상태를 대상으로 했다.

## 재기동과 프런트엔드 복구

비공개 자료: `.local-deploy/json-tree-deploy-20260911-Ki6v18/`. 기존 전체 Compose에는 비밀값이 있으므로 출력·공유·커밋하지 않는다. 새 overlay에는 프런트엔드 이미지 참조만 있다.

현재 프런트엔드 재기동:

```bash
docker compose -p waf-ai-console \
  -f /home/nagix/projects/waf-ai-console/.local-deploy/context-review-20260910-fQQoxX/compose.json \
  -f /home/nagix/projects/waf-ai-console/.local-deploy/json-tree-deploy-20260911-Ki6v18/compose.frontend.json \
  up -d --no-deps --no-build --pull never frontend
```

직전 프런트엔드로만 복구하려면 위 명령에서 새 `compose.frontend.json`의 `-f` 줄을 제외한다. API·worker를 교체하거나 이전 DB 백업을 복원하지 않는다. 이번에 실제 복구를 실행한 것은 아니다. 기본 개발 Compose 단독 실행이나 `down -v`는 사용하지 않는다.

## Agent 이력 문자열 JSON 후속 보완

원인: `AgentStepResponse.input/output`은 복호화된 `str | None`인데 최초 UI는 객체만 트리로 처리했다. 최초 회귀 테스트도 실제 문자열 대신 객체를 사용해 누락을 찾지 못했다. API·DB 변경 없이 Agent 이력에만 JSON 문자열 판독을 명시적으로 켰고, 회귀 fixture를 실제 응답과 같은 문자열로 수정했다.

- Agent 입력·출력: 트리/색상 JSON/저장 원문 전환. 전체 복사는 원래 문자열을 그대로 유지.
- `user_input`, `correction_user_input`: 사용자 선택 시 문자열 속 JSON을 펼쳐 표시. HTTP·payload·일반 프롬프트는 자동 파싱하지 않음.
- 중복 키, 숫자 반올림·범위 초과, 형식 오류와 처리 한도 초과는 원문으로 유지. 파서 예외나 원문을 로그로 출력하지 않음.
- 프런트엔드 테스트 30개 파일 통과, Vite 빌드 성공(JS 507.56 kB, 기존 번들 경고 유지).
- 합성 브라우저 검증: 실제 API 형태의 문자열 입출력, metadata, 중첩 JSON, 원문·복사, 검색, 모바일과 기존 트리 회귀 통과.
- 실제 배포 브라우저 검증: 기존 완료 테스트 한 건의 HTTP 구조 확인 출력, Primary 입력, 중첩 `user_input`, 실행 정보가 트리로 표시됨. 원문 보기가 API 반환 문자열과 일치. 페이지 예외·예상 밖 요청 0건.
- 관리자 세션으로 읽기 전용 조회해 기존 접근 감사가 추가되며 검사 후 로그아웃했다. 인증 외 쓰기와 외부 요청은 브라우저 검사에서 차단했고 새 분석·재실행·LLM 호출은 없었다. 원문·응답·인증값·실제 분석 화면 캡처는 검사 자료에 저장하지 않았다.
- API·두 worker의 컨테이너/이미지/시작 시각/실행 설정과 Nginx 설정을 보존했다. UI/readiness 200, 미인증 프록시 401, 제공 HTML/JS/CSS가 빌드와 일치.

최신 UI: `waf-ai-console-frontend:agent-json-20260911-SVpHZt`. 비공개 배포 및 검증 자료는 `.local-deploy/agent-json-fix-20260911-SVpHZt/`에 있다. 기존 `json-tree-20260911-Ki6v18` 이미지는 복구용으로 유지한다. 백엔드 요청 입력 검사 v1은 여전히 이번 배포 대상이 아니다.

```bash
docker compose -p waf-ai-console \
  -f /home/nagix/projects/waf-ai-console/.local-deploy/context-review-20260910-fQQoxX/compose.json \
  -f /home/nagix/projects/waf-ai-console/.local-deploy/json-tree-deploy-20260911-Ki6v18/compose.frontend.json \
  -f /home/nagix/projects/waf-ai-console/.local-deploy/agent-json-fix-20260911-SVpHZt/compose.frontend.json \
  up -d --no-deps --no-build --pull never frontend
```

직전 JSON 트리 UI로만 복구하려면 마지막 `agent-json-fix`의 `-f` 줄만 제외한다. API·worker·DB에는 변경이 없으므로 DB 복원을 하지 않는다.
