# Internal Egress 검증 기록 — 2026-09-07

## 변경 범위

- 공급자 표시를 `vLLM`으로 변경하고 설정에 Internal Egress 탭을 추가했다.
- 개별 RFC1918 IPv4/ULA IPv6·포트의 관리자 CRUD를 구현했다. 중복·동시 수정·사용 중 항목 보호와 변경 감사를 포함한다.
- DB 목록을 vLLM 허용 기준으로 사용한다. 환경변수 자동 가져오기/대체 허용은 없다. 기존 프로필과 분석 이력을 자동 수정하지 않는다.
- 프로필 등록·수정·활성화·테스트 접수·승격, worker 시작과 매 HTTP 요청 직전에 검사한다. vLLM도 redirect/환경 프록시를 사용하지 않는다.
- 테스트 도중 비활성화·수정·권한 철회가 발생하면 성공 응답으로 이전 상태를 되살리지 않는다.
- DB 변경은 `0007_internal_egress`의 새 테이블 하나이며 운영 패키지 추가는 없다.

## 실행한 검증

| 검증 | 결과 |
|---|---|
| 최종 전체 backend pytest | **978 passed**, 208.95초 |
| 최종 전체 frontend Node 테스트 | **160 passed** |
| Vite production build | 성공, `/tmp/waf-internal-egress-build` |
| 관리자 CRUD와 0006→0007 합성 DB migration | 기존 이력 보존·빈 목록·중복/제약·설정 존재 시 downgrade 거부 확인 |
| 실제 ModuAgent + 모의 HTTP transport | redirect/환경 프록시 차단, SDK 재시도 전에 권한 재확인 |
| 모의 브라우저 | CRUD·오류·충돌·모델 설정 연동 및 390px 라이트/다크 확인 |

Backend는 `waf-ai-console-label-tests:local` 일회용 컨테이너에 backend를 읽기 전용으로 마운트하고 `--network none`, `PYTHONDONTWRITEBYTECODE=1`, `pytest -p no:cacheprovider tests`로 실행했다. 기존 Starlette/AnyIO deprecation warning 1개가 남아 있다. Frontend는 Node 컨테이너에서 네트워크 없이 검증했다. 운영 DB volume은 연결하지 않았다.

새 회귀에서는 다음을 확인했다.

- 공개/loopback/link-local/mapped IPv6, hostname/CIDR, zone ID, 비표준 숫자 IP, userinfo/query/fragment/제어문자 및 포트 0·범위 초과 차단
- 허용 IP가 같아도 포트가 다르면 차단, IPv6 및 기본 포트 정규화
- 환경변수가 있더라도 DB가 비면 프로필 등록과 worker 실행 차단
- 실행 중 권한 해제 시 이후 HTTP 요청과 재시도 차단, 안전한 오류코드 표시
- 테스트 완료가 동시 비활성화를 Verified로 덮어쓰지 않음
- 서비스 Key로 관리자 CRUD 접근 불가, 성공한 변경만 감사 기록

브라우저는 `127.0.0.1:18081` 개발용 정적 리소스만 읽고 API/health/docs 요청은 모의 처리 또는 차단했다. 실제 API·LLM·운영 DB 호출은 없다. 잘못된 입력에서 POST 없음, 사용 중 주소 편집/삭제 제한, 조회 후 사용 상태가 바뀐 삭제의 409 처리, revision 충돌 시 입력 유지와 명시적 재확인, 삭제 취소, 조회 실패와 빈 목록 구분, HTML 이스케이프, vLLM 미허용 대상의 저장/테스트/승격/활성화 차단, OpenAI 승인 화면 비영향을 확인했다. 모델 테스트/승격 액션은 실제로 실행하지 않았다. 개발 서버는 종료했다.

합성 스크린샷은 `/tmp/waf-internal-egress-desktop.png`, `/tmp/waf-internal-egress-mobile-light.png`, `/tmp/waf-internal-egress-mobile-dark.png`에 있다. 전체 페이지 촬영 전 포커스와 스크롤 위치를 초기화했으며 주 작업자가 화면을 직접 확인했다.

## 수행하지 않은 작업과 적용 주의

- 실행 중인 Docker 재배포와 실제 DB의 0007 migration은 **수행하지 않았다**. 기존 서비스와 데이터는 그대로다.
- 실제 내부 vLLM 연결·GPU 추론·네트워크 ACL·TLS 인증서 검증은 하지 않았다. 실제 로그 전송과 유료 OpenAI 호출도 하지 않았다.
- 배포 시 기존 환경 allowlist는 무시된다. `vllm.internal` 등의 프로필은 실제 내부 IP 등록과 주소 변경/재검증이 필요하다. OpenAI 주소·승인 정책은 유지된다.
- HTTPS vLLM은 인증서 SAN의 IP 일치와 기본 CA 신뢰 저장소를 별도 확인해야 한다. 환경변수 기반 프록시/CA 설정은 사용하지 않는다.
- 앱의 허용 목록은 OS 방화벽이 아니며, 이미 전송된 요청을 회수하거나 정책 변경과 dispatch를 OS 수준으로 원자화하지 않는다.

API 정의와 안전한 적용/롤백 순서는 [Internal Egress 정의서](Internal_Egress_v0.1.md)를 참고한다.
