# 관리자 요청 보안 — 2026-09-08

이번 변경은 관리자 요청 출처 검사, Production API Key 호환 유지, HTTPS·접속 IP·로그인 제한의 배포 준비다. 새 운영 패키지·DB 마이그레이션·LLM 호출·분석 정책 변경은 없다. 회사/VPN 접근망은 사용자 답변에 따라 기존 방화벽에서 관리한다. WAF에 별도 회사 CIDR 목록을 중복 등록하지 않는다.

## 관리자 요청

- 로그인·로그아웃은 세션 유무와 무관하게 출처를 검사한다. 유효한 관리자 세션의 POST/PUT/PATCH/DELETE 등 변경 요청도 라우팅·본문 읽기·API Key 조회 전에 검사한다.
- Origin의 scheme·host·실효 port를 정확하게 비교한다. 같은 `cyberailabs.team` 아래의 사용자 Jupyter도 허용 출처가 아니다. Origin이 없을 때만 Referer의 정확한 origin으로 대체하고 둘 다 없으면 거부한다.
- `null`, 복수/중복 Origin, 사용자 정보가 들어간 URL, 잘못된 포트와 출처는 거부한다. 잘못된 Origin을 정상 Referer로 구제하지 않는다. `Sec-Fetch-Site`가 있으면 `same-origin`/`none`만 허용하며 출처 검사 자체를 대체하지 않는다.
- 차단 응답은 403 `csrf_origin_required`/`csrf_origin_invalid`이며 헤더·원문·비밀값을 포함하지 않는다. 차단된 요청으로 분석·테스트·설정 변경 또는 서비스 키의 최근 사용 시각 갱신을 하지 않는다.
- 쿠키 없는 서비스 API Key 접수·업로드·리뷰는 Origin 없이 기존 권한·source 범위를 적용한다. 유효한 관리자 세션과 API Key를 동시에 보낸 경우 세션이 우선하며 출처 검사도 적용한다.
- GET/HEAD/OPTIONS의 기존 권한은 유지한다. Origin 검사는 인증을 대신하지 않는다. 관리자 원문·보고서·Agent 열람의 권한과 감사 정책도 유지한다.

브라우저는 같은 출처의 변경 요청에 Origin을 자동으로 보낸다. 정상 UI에 별도 토큰 입력은 없다. 스크립트로 관리자 로그인/변경 API를 호출하던 경우에는 정확한 Origin을 명시해야 한다. 일반 수집기에 관리자 쿠키를 사용하지 않는다. API Key를 보유한 서버가 HTTP 헤더를 만들 수 있다는 점과 브라우저 CSRF 방어는 별개의 보안 경계다.

## 배포 설정과 세션

운영은 `WAF_ENVIRONMENT=production`, `WAF_PUBLIC_ORIGIN=https://waf.cyberailabs.team`, `WAF_SESSION_HTTPS_ONLY=true`를 사용한다. public origin에는 경로·쿼리·와일드카드·끝의 `/`를 넣지 않는다. 운영에서 Origin이 누락되거나 HTTPS 쿠키가 꺼지면 시작을 거부하고 설정 오류에 비밀값을 출력하지 않는다.

public origin을 설정하면 API는 정확한 Host만 허용하고 나머지는 421 `untrusted_host`로 거부한다. 직접 내부 점검용 GET/HEAD `/health/live`, `/health/ready`만 Host 검사 예외이며 관리자/API 접근의 우회 경로는 아니다. 현재 health 라우트는 GET만 지원하므로 실제 점검은 GET을 사용한다. Origin의 기준을 요청의 X-Forwarded-Host/Proto 값으로 바꾸지 않는다.

HTTPS 세션 쿠키는 `__Host-waf_session`, Secure, HttpOnly, SameSite=Strict, Path=/, Domain 없음으로 발급·삭제한다. 이전 이름의 `session`은 HTTPS 환경의 인증 fallback으로 사용하지 않으므로 적용 후 관리자는 다시 로그인한다. DB·분석 이력은 변경하지 않는다. 로컬 HTTP 개발 모드는 기존 `session` 이름을 유지한다.

로컬 개발에서 public origin을 비워 두면 직접 요청의 scheme/Host를 비교한다. 개발용 웹 프록시는 포트를 포함한 Host를 보존한다. 이 모드는 운영 Host 제한을 대신하지 않으며 운영은 반드시 고정 origin을 지정한다.

프록시 구성은 [신규 설치 가이드](Production_Docker_Compose_Guide.md)를 따른다. 기존 방화벽과 Gateway의 접근 제한을 유지하고 WAF API의 host port를 열지 않는다. 검토한 프록시만 client IP와 HTTPS 정보를 전달하도록 두 hop의 신뢰 범위를 고정한다. UI에서 차단된 로그인은 서비스 주소를 확인하도록 안내하고, 로그아웃 실패 시 화면·로그인 상태를 보존한다.

## 검증 및 남은 범위

회귀 테스트는 별도 메모리 DB·가상 이벤트·임시 서비스 키와 네트워크 차단 환경을 사용한다. 보안 테스트의 HTTP 클라이언트에는 기본 Origin을 넣지 않아 누락·악성 출처와 쿠키 없는 수집기를 독립적으로 검증한다. 일반 UI 테스트 fixture만 브라우저의 정상 Origin을 모방한다.

2026-09-09 검증 결과:

- 백엔드 전체 1,502개 통과, 실패·에러·스킵 0. 새 요청 보안 104개, PDF·샘플 파일·입력 스키마·마이그레이션을 포함한다. 기존 Starlette/AnyIO `BlockingPortal` 사용 중단 예정 경고만 발생했다.
- 프런트엔드 320개 통과, 운영 빌드 성공. 격리 Chromium에서 로그인 차단 안내·로그아웃 실패 후 입력 보존과 재시도·중복 로그아웃 방지 3개를 검증했다.
- 프록시 설정 단위 테스트 15개 통과. 실제 Nginx 1.30.4의 격리된 두 hop에서 HTTPS·IP 전달, 직접 접근 차단, Host/SNI 불일치, 로그인 제한과 경로·전달 IP 위조 우회 방지, upstream 부재 시 기동을 확인했다. 테스트 본문·쿼리·Cookie·API Key의 접근 로그 노출은 없었다.
- Nginx 검증의 API는 전달 정보를 확인하는 테스트 서버다. 설치된 실제 Uvicorn의 정확한 신뢰 peer 처리와 Host 보존은 별도 ASGI 테스트로 검증했으며, 운영 네트워크 전체를 검증한 것은 아니다.
- 기본 운영 Compose와 Gateway 연결 overlay의 설정 검증 통과. 필수 설정 누락 시 거부, API host port 미공개, 고정된 프록시 신뢰 주소와 읽기 전용 정책 마운트를 확인했다. 실제 `.env`를 읽거나 서비스를 변경하지 않았다.

저장소 루트에서 프록시 검증을 다시 실행할 수 있다. Nginx smoke에는 로컬 Docker와 지정 이미지가 필요하며, 별도 임시 컨테이너만 사용한다.

```bash
python3 -m unittest discover -s deploy/security -p 'test_*.py' -v
python3 deploy/security/check_compose.py
python3 deploy/security/proxy_smoke.py --image nginx:1.30.4-alpine
```

실행 중인 서비스 재배포, 운영 방화벽·DNS·Gateway 변경, 실제 LLM 호출은 하지 않았다. 기존 Gateway 연결 절차는 [서브도메인 배포 안내](Subdomain_Deployment_CyberAILabs.md)에 정리했다.

실제 운영 서버의 방화벽/VIP/SNAT, 인증서, 프록시 peer 주소, 접속 IP, 로그인 제한과 기존 Jupyter에 미치는 영향은 배포 전 별도로 확인한다. 기존 Gateway의 이미지·네트워크 변경은 기존 HTTPS/WebSocket 연결에 영향을 줄 수 있다. 이번 코드 변경만으로 운영 보호가 이미 적용된 것으로 간주하지 않는다. 서버 측 세션 폐기·인증 감사·패키지 취약점 등 이전 보안 점검의 다른 항목을 완료한 것으로 처리하지 않는다.

근거: [OWASP CSRF 방어](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html), [쿠키 접두사](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie#cookie_prefixes).
