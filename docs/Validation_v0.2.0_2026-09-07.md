# v0.2.0 검증 기록 — 2026-09-07

## 변경 범위

합의된 입력 스키마 설정·필드 정의 불변 버전·샘플 검증 후 적용/복귀, 접수 스키마 고정, 필드 정의의 암호화 추론 이력, Production API/온라인 문서/OpenAPI 동기화를 구현했다. 필드 설명을 LLM 입력에 추가하지 않는 사용자 선택을 적용했다. 기존 테스트 결과 목록 탐색 변경을 보존하며 v0.1.0 이후 구현한 프로젝트 소스를 v0.2.0으로 묶었다.

## 최종 결과

| 검증 | 결과 |
| --- | --- |
| Backend 전체 50개 테스트 파일 | **1,258 passed**, 실패/skip 없음 |
| 병렬 그룹 1 / 2 / 3 | 501 / 370 / 387 passed |
| 입력 스키마 관련 부분집합 | core/migration 38 + admission/inference/docs 11 + dataset 3 = 52 passed |
| Frontend 전체 | **242 passed**, 실패/skip 없음 |
| Vite production build | 71 modules, 성공 |
| 합성 브라우저 동작 | 7개 시나리오 성공 |
| 반응형 | 1280/375px의 기본 설정·편집·API 문서에서 가로 넘침 없음 |
| 배포용 Python wheel | v0.2.0 빌드 및 schema 서비스/Production 문서 리소스 포함 확인 |
| Production 문서 템플릿 | docs와 backend 패키지 사본 byte 단위 동일 |
| 게시 파일 검사 | git diff --check 성공, 실제 .env/DB/배포 설정/키 파일/빌드 캐시 제외 |

백엔드는 기존 Python 3.12 테스트 이미지에 현재 프로젝트를 읽기 전용으로 마운트하고 `--network none`, `PYTHONDONTWRITEBYTECODE=1`, `python -m pytest -o addopts='' -p no:cacheprovider -q`로 실행했다. 실제 DB 볼륨은 연결하지 않았다. 기존 Starlette/AnyIO BlockingPortal deprecation 경고만 남아 있다. 초기 검증의 직접 함수 호출 호환, 테스트 fixture 수명, 최신 migration head 기대값을 보정한 뒤 전체를 재실행했다.

프런트엔드는 Node 24.11.1에서 `node --test --test-isolation=none src/*.test.mjs`, `node node_modules/vite/bin/vite.js build`로 확인했다. 최종 빌드는 index-CNGHhLWp.js / index-CKdVZ4TB.css다. wheel은 테스트 이미지의 setuptools 부재로 호스트 setuptools 78.1.0에서 소스 복사본을 사용해 빌드했고 리소스 포함을 확인했다. 실제 애플리케이션 런타임 검증은 Python 3.12 pytest이며 Python 3.10 애플리케이션 지원을 의미하지 않는다.

## 확인한 경계

- 기본 11개 필드 계약 보호, 추가 타입/중첩 구조/길이/enum/숫자 범위, null과 필수의 분리, 예약명 금지, 미등록 확장 값 보존.
- 필드 설명 변경을 포함한 지문·불변 버전, 암호화 손상 차단, 활성 revision 충돌, 세션/버전/5분 만료 검증 토큰, 이전 버전 복귀.
- 새 필수 필드에 따른 실제 접수 422와 값 미반사, 포트 생략/null 구분, 같은 이벤트 재전송의 원래 버전 보존.
- 이전 대기 분석과 테스트의 스키마 유지, 미기록 과거 작업의 기본 정의 출처 구분, 기존 완료 데이터 미백필.
- Primary/Verifier 모의 호출에 필드 설명이 없고 실제 확장 값은 기존 방식대로 포함됨. 암호화된 입력 단계 출력에 전체 필드 정의, 일반 metadata에는 식별 정보만 포함됨.
- 동적 API 문서·OpenAPI·접수에서 같은 버전/지문 사용, 활성 변경·복귀 후 최신 정의 조회, 추가 캐시 금지.
- 150건 후보 검증·이름 있는 실행·분석 전체의 동일 스냅샷. 첫 행/세 번째 행 실패 시 접수 전체 원자적 롤백과 기존 분석의 모든 컬럼 보존. 이 검증은 실제 LLM 호출 없이 접수만 수행함.
- SQLite 기존 부모 테이블 재작성 없이 새 FK/스냅샷 추가, 기존 합성 하위 이력 보존, 사용 이력 존재 시 downgrade 차단.
- 브라우저: 보호 필드/복제·저장/검증-동의-샘플 변경 무효화/적용/동일 Markdown 다운로드/재검증 복귀·이력/복귀 후 문서 재조회. 예상 외 API 호출·XSS 실행·브라우저 예외 없음.

브라우저는 실제 React 컴포넌트와 합성 fetch mock을 사용했으며 운영 API에 접속하지 않았다. 임시 증거는 `/tmp/waf-schema-ui-smoke-AfJZnG/`에 있고 서버·Chrome은 종료했다.

## 미검증·미실행

- 실제 GPU/vLLM/OpenAI 호출, 토큰 예산·판정 품질·비용, 실제 수집기와의 호환/부하.
- 실행 중인 Docker 재배포, 운영 DB의 0012 마이그레이션, 실제 백업 복원.
- 운영 이미지의 새 네트워크 dependency install 및 운영 UI/API 연결 smoke.
- 기존 보안 점검의 미조치 사항에 대한 보완과 재검증.

배포 시 0012_input_schemas까지 마이그레이션하고 API/worker/model-tester를 같은 버전으로 맞춰야 한다. 필드 버전 적용은 기존 운영 DB에서 별도로 관리자가 수행하며, 이번 GitHub 게시가 자동 배포나 운영 스키마 변경을 의미하지 않는다.
