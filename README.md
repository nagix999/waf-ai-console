# WAF AI Analysis Console

WAF 이벤트를 비동기로 접수하고 AI 분석 결과와 분석가 리뷰, Agent 실행 이력을 관리하기 위한 내부용 MVP 골격입니다.

현재 버전은 검증된 Production vLLM 프로필을 ModuAgent에 연결하고, Primary/Verifier 정책으로 구조화된 판정을 생성합니다. 안전한 초기 기동을 위해 Docker Compose 기본값은 여전히 명시적인 `stub` 모드입니다.

## 빠른 실행

```bash
docker compose up --build
```

- UI: http://localhost:8080
- API 문서: http://localhost:8000/docs
- 기본 관리자: `admin` / `change-me-now`
- 개발용 서비스 API 키: `dev-service-key-change-me`

위 기본값은 로컬 확인 전용입니다. 내부 환경에 배포할 때는 반드시 `.env.example`을 복사해 비밀번호, 세션 비밀키, 암호화 키, API 키를 교체하세요.

## 로컬 개발

백엔드:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --reload
```

워커:

```bash
cd backend
python -m app.worker
```

프론트엔드:

```bash
cd frontend
npm install
npm run dev
```

## 현재 포함 범위

- REST 단건 접수와 최대 60초 대기 응답
- CSV/JSON 일괄 업로드
- SQLite WAL, lease 기반 비동기 작업 선점
- 암호화된 payload 및 Agent 단계 입출력 저장
- 환경변수 기반 단일 관리자 로그인과 서비스 API 키
- append-only 분석가 리뷰 및 외부 리뷰 중복 방지
- 분석 목록·상세·Agent 이력을 확인하는 React UI
- Docker Compose의 API/worker/frontend 분리
- Alembic 초기 스키마와 API 테스트
- 복수 vLLM 프로필과 암호화된 API Key
- 빠른 테스트/전체 검증 비동기 실행과 단계별 결과
- 동일 설정 지문으로 전체 검증을 통과한 프로필만 Production 승격
- 장시간 전체 검증이 분석 큐를 막지 않도록 analysis worker와 model-test worker 분리
- ModuAgent 0.6.2 Standard execution과 Pydantic 구조화 판정 계약
- 조건부 독립 Verifier와 불일치/실패 시 자동 보류 정책
- 32K 입력 예산에 맞춘 앞/뒤/시그니처 주변 보존형 payload 잘림
- 실제 Agent 입력·검증 출력·run ID·fingerprint·failure ID 이력
- 판정 근거, 위협/시그니처 분석, Verifier 사유, 튜닝 자문을 분리한 상세 UI

## vLLM 프로필 검증

설정 화면에서 프로필을 등록한 뒤 다음 검증을 실행할 수 있습니다.

- 빠른 테스트: `/v1/models`, 기본 Chat Completion, 중첩 JSON Schema
- 전체 검증: 빠른 테스트 + system role + 32K 근접 입력 + 제한된 동시 요청/p95

모든 요청은 `enable_thinking=false`, redirect 비활성화, 환경 프록시 무시 조건으로 실행됩니다. `WAF_VLLM_ALLOWED_TARGETS`에는 `host:port` 또는 IPv4 `CIDR:port`를 쉼표로 구분해 설정합니다. UI에서 임의의 외부 주소를 등록할 수 없습니다.

## 실제 Agent 활성화

1. 설정 화면에서 vLLM 프로필을 등록합니다.
2. 전체 검증을 통과시킨 뒤 하나의 프로필을 Production으로 승격합니다.
3. `.env`의 `WAF_AGENT_MODE=moduagent`를 설정하고 analysis worker를 재기동합니다.

```bash
docker compose up --build -d worker
```

모델 호출은 `temperature=0`, `enable_thinking=false`, 최대 출력 토큰은 Production 프로필 값으로 실행됩니다. 네트워크/timeout/HTTP 408/5xx만 1회 재시도합니다. HTTPS 프로필은 인증서 검증을 끌 수 없습니다. 사설 CA가 필요하면 컨테이너 신뢰 저장소에 CA를 설치해야 합니다.

상세 정책은 `backend/app/agent/README.md`를 참고하세요.

## 다음 단계 후보

- 합성 보안 케이스로 평가셋/회귀 테스트 화면 연결
- 실제 vLLM에서 출력 스키마와 품질 기준 검증
- 운영 프롬프트 버전의 승인·롤백 UI
