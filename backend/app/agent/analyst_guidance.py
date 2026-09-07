"""Analyst-facing follow-up guidance, without changing the stored verdict.

The structured checks come from the model; fallback checks are openly
suggested sources, never claims that a remote system has been queried.
"""
import re

from .contracts import AgentVerdict, AnalystCheck, WAFAnalysisOutput


_INTERNAL = re.compile(
    r"primary|verifier|독립\s*검증|1차\s*판정|실패\s*ID|failure[_ ]?id|"
    r"(?:판정|분석\s*결과).{0,30}(?:서로\s*다|불일치|일치하지)", re.IGNORECASE,
)
FOLLOW_UP_SUMMARY = "현재 분석에서는 정탐·오탐 판정을 보류했습니다."
EVIDENCE_SOURCE_CHECK = "일부 LLM 근거가 지정된 필드의 원문과 일치하지 않아 제외되었습니다. 남은 근거를 직접 확인하세요."
EVIDENCE_SOURCE_NOTICE = "일부 발췌는 지정한 원문에서 확인되지 않아 판정 근거에서 제외했습니다."


def analyst_text(value: str) -> bool:
    return bool(value.strip()) and _INTERNAL.search(value) is None


def merge_analyst_checks(*outputs: WAFAnalysisOutput) -> list[AnalystCheck]:
    checks: list[AnalystCheck] = []
    seen: set[tuple[str, str, str]] = set()
    for output in outputs:
        for check in output.analyst_checks:
            values = (check.source_ko, check.check_ko, check.why_ko)
            if values not in seen and all(analyst_text(value) for value in values):
                checks.append(check)
                seen.add(values)
            if len(checks) == 5:
                return checks
    return checks


def build_analyst_guidance(output: WAFAnalysisOutput, *, incomplete_execution: bool = False) -> dict:
    # The worker's exact source-validation diagnostic is a limitation, not a
    # request-specific investigation task. Preserve it without inventing work.
    source_notice = EVIDENCE_SOURCE_CHECK in output.recommended_checks or any(
        check.check_ko == EVIDENCE_SOURCE_CHECK for check in output.analyst_checks
    )
    checks = [check.model_dump(mode="json") for check in merge_analyst_checks(output)
              if check.check_ko != EVIDENCE_SOURCE_CHECK]
    # Previous output contracts remain accepted. Do not invent request-specific
    # findings when a model omitted structured follow-up guidance.
    if not checks:
        for item in output.recommended_checks:
            if item != EVIDENCE_SOURCE_CHECK and analyst_text(item):
                checks.append({
                    "source_ko": "해당 요청과 관련된 운영 자료",
                    "check_ko": item[:800],
                    "why_ko": (
                        "확인된 사실을 원문 근거와 대조해 정상 업무 요청인지 공격 시도인지 구분하는 데 활용하세요. 이 안내는 확인 결과를 미리 단정하지 않습니다."
                        if output.verdict == AgentVerdict.inconclusive
                        else "현재 판정을 다시 내리기 위한 필수 조건이 아니라, 요청의 영향 범위와 후속 조치의 적절성을 검토하기 위한 선택적 확인입니다."
                    ),
                })
            if len(checks) == 5:
                break
    if output.verdict == AgentVerdict.inconclusive and not checks:
        checks.append({
            "source_ko": "대상 애플리케이션의 요청 처리 규격 또는 담당자",
            "check_ko": "탐지된 입력이 해당 기능에서 허용되는 업무 데이터인지, 저장·출력·명령 실행 중 어떤 처리 경로로 사용되는지 확인하세요.",
            "why_ko": "허용된 업무 값으로만 취급되는지, 보안 경계를 우회하는 구문으로 해석될 수 있는지 구분하는 데 도움이 됩니다. 공격 시도 여부와 실제 성공 여부는 따로 판단하세요.",
        })
    limitations = [EVIDENCE_SOURCE_NOTICE] if source_notice else []
    if output.input_truncated:
        limitations.append("원문 전체가 분석 입력에 포함되지 않았습니다. 생략된 구간을 원문에서 확인하세요.")
    if incomplete_execution:
        limitations.append("자동 분석 절차를 모두 완료하지 못했습니다. 현재 판정을 확정 결과로 사용하지 말고 확인 항목을 검토하세요. 실행 상태는 기술 정보에서 확인할 수 있습니다.")
    summary = output.summary_ko
    if not analyst_text(summary):
        summary = {
            AgentVerdict.true_positive: "현재 분석에서는 공격 또는 악성 시도로 판단했습니다.",
            AgentVerdict.false_positive: "현재 분석에서는 정상 요청에 대한 오탐으로 판단했습니다.",
            AgentVerdict.inconclusive: FOLLOW_UP_SUMMARY,
        }[output.verdict]
    return {"summary_ko": summary, "checks": checks, "limitations": limitations}
