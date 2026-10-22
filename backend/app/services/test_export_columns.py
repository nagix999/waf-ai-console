"""Stable English Excel columns and non-sensitive, illustrative examples."""
import hashlib
import re

# name, Korean label, description, example, group
DEFINITIONS = (
    ("row_number", "문항 번호", "원본 접수 순서입니다. 필터·정렬 후에도 다시 매기지 않습니다.", "1", "case"),
    ("case_name", "문항명", "접수 시 지정한 문항 이름입니다.", "로그인 요청 확인", "case"),
    ("difficulty", "난이도", "접수 시 지정한 난이도입니다.", "hard", "case"),
    ("test_category", "테스트 유형", "접수 시 지정한 문항 유형입니다.", "SQLi", "case"),
    ("ingest_status", "접수 상태", "accepted/duplicate/rejected는 정상 접수/중복/거부를 뜻합니다.", "accepted", "execution"),
    ("status", "처리 상태", "선택한 실행의 상태입니다. 접수 거부 문항은 rejected입니다.", "completed", "execution"),
    ("error_code", "오류 코드", "접수·실행 오류 코드이며 오류가 없으면 비어 있습니다.", "primary_agent_failed", "execution"),
    ("retry_count", "재실행 횟수", "선택한 답안·평가 기준에서 문항에 연결된 재실행 횟수입니다.", "0", "execution"),
    ("analysis_id", "분석 ID", "선택한 실행의 분석 식별자입니다.", "11111111-1111-4111-8111-111111111111", "execution"),
    ("source_system", "연동 시스템", "서버가 기록한 분석 출처입니다.", "waf-collector", "execution"),
    ("event_id", "이벤트 ID", "수집 시스템의 이벤트 식별자입니다.", "sample-event-001", "input"),
    ("company_name", "회사", "이벤트가 발생한 회사 또는 조직입니다.", "예시 회사", "input"),
    ("src_ip", "출발지 IP", "WAF가 기록한 요청 출발지이며 공격 주체를 보장하지 않습니다.", "192.0.2.10", "input"),
    ("dest_ip", "목적지 IP", "WAF가 기록한 요청 목적지입니다.", "198.51.100.20", "input"),
    ("src_port", "출발지 포트", "요청 출발지 포트이며 미기록 시 비어 있습니다.", "43122", "input"),
    ("dest_port", "목적지 포트", "요청 목적지 포트입니다.", "443", "input"),
    ("payload", "HTTP 원문", "마스킹하지 않은 HTTP 원문과 Cookie를 포함할 수 있습니다. 긴 값은 __part_2 등으로 이어집니다.", "GET /sample HTTP/1.1\\r\\nHost: example.invalid\\r\\n\\r\\n", "input"),
    ("signature", "시그니처", "WAF 탐지 시그니처이며 정답을 의미하지 않습니다.", "Example SQL Injection", "input"),
    ("event_name", "이벤트명", "WAF 탐지 이벤트 이름입니다.", "요청 구문 탐지", "input"),
    ("waf_vendor", "WAF 벤더", "WAF 제품 또는 벤더 이름입니다.", "generic", "input"),
    ("waf_action", "WAF 조치", "D는 차단, A는 허용입니다. 정오탐 정답이 아닙니다.", "D", "input"),
    ("extra_fields", "추가 입력 전체", "저장된 추가 입력의 JSON입니다. 개별 추가 입력 컬럼과 내용이 겹칠 수 있습니다.", '{"policy_name":"sample"}', "input"),
    ("verdict", "AI 최종 판정", "Primary·Verifier와 서버 정책을 거친 최종 판정입니다. true_positive/false_positive/inconclusive는 정탐/오탐/보류입니다.", "true_positive", "result"),
    ("summary_ko", "분석 요약", "저장된 최종 분석 요약입니다.", "요청에서 공격 구문을 확인했습니다.", "result"),
    ("severity", "심각도", "저장된 위협 심각도입니다. 판정의 정확도와는 다릅니다.", "HIGH", "result"),
    ("confidence_score", "판정 신뢰도", "AI 분석에 저장된 신뢰도이며 실제 정확도를 보장하지 않습니다.", "0.95", "result"),
    ("threat_category", "위협 유형", "저장된 위협 분류입니다.", "sql_injection", "result"),
    ("explanation_ko", "판정 설명", "저장된 결과 설명입니다. 이전 형식은 탐지 비교 설명을 사용합니다.", "입력 구문과 요청 목적을 비교해 판단했습니다.", "result"),
    ("technique_ko", "공격 방식", "요청에서 해석한 공격 방식입니다.", "쿼리 조건을 우회하려는 구문입니다.", "result"),
    ("potential_impact_ko", "예상 영향", "공격이 성립했을 때의 영향이며 실제 침해 성공을 뜻하지 않습니다.", "취약한 처리 경로에서는 정보 노출이 발생할 수 있습니다.", "result"),
    ("signature_relation", "탐지 내용과 요청의 관계", "탐지 설명과 요청 내용의 관계입니다. exact/partial/mismatch/unknown을 사용합니다.", "exact", "result"),
    ("matched_points", "탐지와 일치하는 부분", "탐지 설명과 요청의 일치점을 JSON 배열로 제공합니다.", '["탐지 대상 구문이 요청에 있습니다."]', "result"),
    ("mismatched_points", "탐지와 다른 부분", "탐지 설명과 요청의 차이점을 JSON 배열로 제공합니다.", "[]", "result"),
    ("comparison_limit_ko", "탐지 비교의 제한", "입력 범위 등 탐지 비교 시 고려할 제한입니다.", "대상 애플리케이션의 처리 방식은 로그에 없습니다.", "result"),
    ("evidence_true_positive", "주요 정탐 근거", "웹 표시 순서의 정탐 근거 최대 5개입니다. 여러 항목은 줄바꿈 --- 줄바꿈으로 구분합니다.", "위치: payload\n로그 발췌: sample\n판단 이유: 공격 구문의 위치를 확인했습니다.", "result"),
    ("evidence_false_positive", "주요 오탐 근거", "웹 표시 순서의 오탐 근거 최대 5개입니다. 여러 항목은 줄바꿈 --- 줄바꿈으로 구분합니다.", "위치: payload\n로그 발췌: sample\n판단 이유: 정상 입력 문맥을 확인했습니다.", "result"),
    ("evidence_context", "주요 참고 내용", "웹 표시 순서의 참고 근거 최대 5개입니다. 방향이 없는 과거 근거도 포함하며 여러 항목은 줄바꿈 --- 줄바꿈으로 구분합니다.", "위치: waf_action\n로그 발췌: D\n판단 이유: WAF 차단 관측값입니다.", "result"),
    ("expected_verdict", "접수 당시 기대 판정", "업로드·요청 시 제공한 최초 답안입니다. 참고 판정을 수정해도 그대로 유지합니다.", "true_positive", "evaluation"),
    ("reference_verdict", "평가 기준 답안", "최신/접수 당시/저장 평가/공식 답안 중 선택한 비교 기준입니다.", "false_positive", "evaluation"),
    ("comparison_outcome", "답안 비교 결과", "선택한 기준 답안과 최종 AI 판정의 비교 결과입니다. 실행 실패·평가 제외는 오답과 구분합니다.", "false_positive", "evaluation"),
    ("initial_verdict", "외부 1차 판정", "요청에 전달된 외부 모델 등의 1차 판정입니다. Agent Primary 출력이나 정답이 아닙니다.", "false_positive", "evaluation"),
    ("initial_probability", "외부 1차 판정 신뢰도", "외부 1차 판정 클래스의 신뢰도(0~1)입니다. 최종 AI 신뢰도와 같은 척도가 아닙니다.", "0.9", "evaluation"),
    ("initial_model_version", "외부 1차 모델 버전", "요청 시 전달한 외부 1차 모델의 버전입니다.", "external-model-v1", "evaluation"),
)
EVIDENCE_COLUMNS = ("evidence_true_positive", "evidence_false_positive", "evidence_context")


def column_catalog(extra_fields, definitions=()):
    columns = [dict(zip(("name", "label_ko", "description", "example", "group"), definition)) for definition in DEFINITIONS]
    descriptions = {field["name"]: field.get("description", "") for field in definitions}
    used = set()
    for key, kind in sorted(extra_fields.items()):
        suffix = key if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,119}", key) else "field_" + hashlib.sha256(key.encode()).hexdigest()[:12]
        base = "extra_fields." + suffix
        name, index = base, 2
        while name in used:
            name, index = f"{base}_{index}", index + 1
        used.add(name)
        columns.append({"name": name, "label_ko": ("추가 입력: " + key)[:240], "source_key": key,
            "description": (descriptions.get(key) or f"저장된 추가 입력 {key}의 값입니다.")[:1024],
            "example": {"integer": "1", "real": "0.5", "true": "true", "false": "false", "array": "[]", "object": "{}", "null": "null"}.get(kind, "예시 입력값"), "group": "input"})
    return columns
