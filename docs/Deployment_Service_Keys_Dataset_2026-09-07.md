# 서비스 키·합성 150건 Docker 재배포 기록

기준일: 2026-09-07. 사용자 요청에 따라 앞서 검증한 구현을 실제 로컬 Docker 서비스에 적용했다. 별도 기능 추가·실제 모델 검증·키 발급은 수행하지 않았다.

## 배포 결과

- UI `http://127.0.0.1:18080`, API `http://127.0.0.1:18000` 유지.
- API·analysis worker·model-test worker·frontend 모두 새 이미지로 실행, 재시작 횟수 0. API healthy.
- 두 worker의 `role=analysis` / `role=model_test`, `mode=moduagent` 시작 확인. 실제 모델 연결 성공을 뜻하지 않는다.
- 기존 관리자 로그인과 `/health/live`, `/health/ready` 정상. 실제 기존 배포 키로 `/auth/me` 조회 시 401을 확인했고 값은 출력하지 않았다.
- 서비스 키 목록 `{items: []}`, Internal Egress 빈 배열. 후보 검증·업로드 라벨 응답의 새 API 스키마 확인. 발급·등록·분석 접수·모델 테스트는 하지 않았다.

| 이미지 | ID |
| --- | --- |
| `waf-ai-console-api:deploy-20260907T081611Z` | `970a026c5f496144af2b8ed41b2a807f445e5d451c1b9881131a90b9e9ed26bb` |
| `waf-ai-console-frontend:deploy-20260907T081611Z` | `969267a589cb2662e4d758b46e34aae0527dfbe57228947d2da1c76760e64c68` |

Backend는 이전 runtime 기반으로 오프라인 wheel 빌드/설치했고 패키지 35개의 버전이 동일함을 검사했다. 설치본 및 `/app` 소스의 worker·moduagent import와 데이터 150건 포함을 DB/네트워크 생성 금지 상태에서 확인했다. Frontend는 기존 npm 캐시를 사용했다. 새 자산은 `index-CpO1AVyM.js`, `index-DpS_Irz5.css`다.

## DB 백업·마이그레이션·보존

실제 사전 조회에서 DB `0006`, 완료 분석 150건, 종료된 모델 테스트 4건, 활성 작업 0건을 확인했다. 접수 중지 후 큐를 다시 확인하고 모든 writer를 중지했다. 기존 API와 두 worker는 20초 종료 유예 뒤 exit 137로 중지됐다. 큐가 비어 있었고 이후 backup/integrity/행 보존 검사 모두 통과했다. 종료 신호 처리의 원인 분석은 수행하지 않았다.

SQLite backup API로 만든 13,504,512-byte 새 **0006** 백업을 볼륨과 비공개 호스트에 보관했다. 백업 원본을 덮어쓰지 않고 복사본에서 `0006 → 0007 → 0008 → 0009`와 보존 검사를 먼저 수행했다. 이후 네트워크 차단 일회용 새 backend 이미지로 실제 DB에 동일 migration을 적용하고 API 기동보다 먼저 검사를 완료했다.

| 대상 | 배포 전 / 기동 후 |
| --- | --- |
| 분석 | 150 / 150 |
| Agent 실행 | 150 / 150 |
| Agent 단계 | 1,169 / 1,169 |
| Label / 리뷰 | 각 0 / 0 |
| 모델 프로필 | 2 / 2 |
| 모델 테스트 이력 | 4 / 4 |
| 프롬프트 버전 / 활성 선택 | 각 1 / 1 |
| 접근 감사 | 4,429 / 4,429 |

백업의 모든 기존 컬럼/행을 내용 지문으로 비교했다. 암호화 데이터·결과 JSON·프로필·정책·감사를 변경하지 않았고 DB integrity 및 FK 검사도 통과했다. 새 설정 테이블은 비어 있다. 기존 모델 테스트의 데이터셋 포함 여부는 false, 추가 메타데이터와 분석 연결은 null이며 새 인덱스도 확인했다.

기존 `waf-ai-console_waf-data` 볼륨, 네트워크, 서비스별 포트·마운트·명령·관리자/세션/암호화 설정을 유지했다. 현재 Compose 환경에서 제거한 항목은 `WAF_BOOTSTRAP_API_KEY`, `WAF_BOOTSTRAP_SOURCE_SYSTEM`, `WAF_BOOTSTRAP_API_KEY_ENABLED`, `WAF_VLLM_ALLOWED_TARGETS`뿐이며 나머지 값의 일치는 비밀 비노출 지문 검사로 확인했다.

## 배포된 웹 번들 확인

실제 18080 서버에서 HTML과 지정된 JS/CSS 세 경로만 읽고 모든 API·인증·health·docs·외부 요청·WebSocket은 mock 또는 차단했다. 실제 API 요청 0, 예상 밖 API 0, 외부 요청 0, 페이지 오류 0이었다. 390px 라이트/다크에서 가로 넘침 없이 다음 흐름을 확인했다.

- DB 발급 키 전용 화면, 원문 1회 표시/소거·폐기 확인, 응답 유실 시 자동 재발급 금지.
- 전체 검증 범위 선택·취소, 후보 지문 변경 재확인, OpenAI 외부 전송/비용 고지.
- 150건 진행·집계와 정확한 테스트 source 검색 이동.
- 업로드 참고 답안 연결/유지 건수와 오류 표시.

브라우저의 키 발급·모델 검증·파일 접수는 모두 합성 mock이며 서비스 데이터를 생성하지 않았다. 검증 브라우저는 종료했다. 소스의 전체 테스트 기록(backend 1,137 / frontend 187)은 [구현 검증 기록](Validation_Service_Keys_Dataset_2026-09-07.md)을 따른다.

## 다음 사용·복구 주의

기존 배포 키는 이제 사용할 수 없다. 설정에서 새 서비스 API Key를 발급해 수집기의 키를 교체해야 한다. 기존 source 경계를 유지하려면 동일한 `source_system`을 사용한다. 만료는 없으며 원문은 발급 직후 한 번만 표시된다.

현재 Production OpenAI 프로필은 그대로다. vLLM 내부 대상은 자동 이관하지 않았으므로 실제 내부 IP·포트를 등록하고 기존 hostname 프로필을 검토·변경·재검증한 뒤 사용해야 한다. 실제 vLLM/OpenAI 호출, 합성 150건 품질·시간·비용 측정, GPU·장시간 부하 검증은 수행하지 않았다.

활성 구성과 0006 백업은 `.local-deploy/service-keys-dataset-20260907T081611Z/`에 있다. Compose/manifest/backup 파일은 비공개이며 출력·공유하지 않는다. `0009`는 기존 분석 이력 때문에 자동 downgrade할 수 없다. 이전 이미지 재기동이나 백업 복원은 신규 데이터 유실과 과거 키 인증 복구 위험이 있으므로 별도 승인·백업 절차 없이 수행하지 않는다. 현재 기동 명령과 복구 안내는 `.local-deploy/README.md`에 갱신했다.
