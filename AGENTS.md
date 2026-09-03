# WAF AI Analysis Console

## Working agreement

- 사용자와 설계를 합의한 뒤 구현한다.
- 요청 범위를 임의로 확장하지 않는다.
- 변경 전 현재 코드와 관련 문서를 먼저 확인한다.
- 기존 변경을 보존하고 작은 단위로 구현한다.
- 구현 후 테스트 결과와 미검증 항목을 구분해서 보고한다.
- 새로운 운영 의존성이나 DB 스키마 변경은 먼저 설명한다.

## Required reading

작업을 시작하기 전에 다음 문서를 읽는다.

- README.md
- docs/WAF_AI_Analysis_Architecture_v0.1.md
- backend/app/agent/README.md

## Fixed architecture decisions

- Backend: FastAPI, SQLAlchemy, SQLite WAL, Alembic
- Frontend: React, Vite
- Deployment: Docker Compose 우선, 향후 Kubernetes/PostgreSQL 전환
- Agent framework: moduagent==0.6.2
- Model: google/gemma-4-26B-A4B-it, 32K context
- vLLM 프로필은 여러 개 등록할 수 있지만 Production은 하나만 허용
- enable_thinking=false
- Agent는 비동기 worker에서 실행
- Primary 판정 후 정책 조건에 따라 독립 Verifier 실행
- Verifier는 Primary 결과를 전달받지 않는다
- Verifier 실패 또는 판정 불일치는 inconclusive로 처리
- 판정값: true_positive, false_positive, inconclusive
- WAF action D/A는 Deny/Allow 관측값이며 정답이 아니다
- 분석가의 과거 판정은 현재 모델 입력이나 학습 데이터로 사용하지 않는다
- 분석가 리뷰는 analysis_id 기준 append-only API로 적재한다

## Security constraints

- WAF payload와 Cookie를 마스킹하지 않는다.
- payload와 Agent 단계 입출력은 암호화해서 저장한다.
- payload를 로그, 예외 메시지, 메트릭에 기록하지 않는다.
- 원문 및 Agent 이력은 관리자만 조회할 수 있고 접근 이력을 남긴다.
- vLLM 요청은 서버에서 허용한 내부 대상에만 전송한다.
- API Key와 .env를 커밋하거나 출력하지 않는다.
- 실제 사내 로그를 테스트 fixture나 프롬프트에 추가하지 않는다.
- 테스트에는 합성 데이터만 사용한다.

## Validation commands

Backend:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
alembic upgrade head
pytest
```
