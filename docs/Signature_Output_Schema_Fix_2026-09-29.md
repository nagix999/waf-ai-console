# 탐지 비교 설명의 한 글자 출력 수정

## 증상과 원인

사용자가 `matched_points: ["요"]`, `mismatched_points: ["탐"]`처럼 결과 JSON 자체에 한 글자가 저장된 것을 확인했다. 정상 문장 fixture는 실제 React 화면에서 전체 문장으로 표시됐다. 화면 CSS나 글자 잘림을 우선 수정할 문제가 아니다.

서버의 세 설명 필드에 `pattern: "\\S"`가 있었다. Pydantic에서는 문자열에 공백 아닌 문자가 있는지 찾지만, 일부 구조화 출력 엔진은 이 패턴을 문자열 전체를 생성하는 문법으로 사용한다. XGrammar v0.1.32의 `GenerateString`은 패턴을 문법으로 변환하고 양 끝에 JSON 따옴표를 붙인다. `\\S`에는 반복이 없으므로 이 경로에서는 정확히 한 글자가 된다. 같은 세 필드에만 증상이 있다는 사용자 관측과 맞는다.

- [XGrammar v0.1.32 구현, GenerateString](https://github.com/mlc-ai/xgrammar/blob/v0.1.32/cpp/json_schema_converter.cc#L1286-L1303)
- [vLLM 구조화 출력 문서](https://docs.vllm.ai/en/stable/features/structured_outputs/)

운영서버의 실제 structured-output backend 및 패키지 버전을 직접 확인하거나 실제 모델 호출로 재현한 것은 아니다. 엔진 구현·애플리케이션 스키마·보고된 증상에 근거한 원인 판단이다.

## 수정

- `matched_points`, `mismatched_points`, `uncertainty_ko`의 모델 전달 JSON Schema에서 비어 있지 않은 내용 검사용 정규식만 제거했다. ID처럼 전체 문자열 패턴이 필요한 다른 필드는 유지한다.
- 공백 전용 값은 서버의 `AfterValidator`에서 거부한다. 원문을 strip해서 저장하거나 문장을 덧붙이지 않는다.
- 400/500자 상한, 배열 최대 5개, relation별 요구사항은 유지한다.
- 새 모델 응답에서 앞뒤 공백을 제외한 내용이 한 글자면 `signature_detail_requires_explanation`을 반환한다. 기존 최초 포함 최대 4회 교정 Retry가 필드·고정 안내만 다시 보내며 잘못된 답변이나 WAF 원문은 오류에 노출하지 않는다.
- 기존 저장 결과는 호환 모델로 그대로 읽는다. 한 글자를 근거 없이 문장으로 복원하거나 기존 최종 판정을 변경하지 않는다.
- Primary/Verifier 모두 적용한다. 모델·프로필·고정 지침 버전(v2.13/v2.14)·운영 선택·DB 데이터는 변경하지 않는다. 모델 제공자의 제약 해석에 따른 생성 경로가 달라지는 버그 수정이며 동일한 모델 출력까지 보장하지 않는다.

## 확인한 범위와 다음 확인

로컬 검증은 스키마의 정규식 제거, 공백·초과 길이 거부, 한글/공백/줄바꿈/따옴표 문장 보존, 과거 한 글자 결과 호환, Primary/Verifier의 모의 vLLM/OpenAI HTTP 응답 교정 Retry를 포함한다. 실제 API 비용은 발생시키지 않았다. 브라우저에서도 네 개의 비교 문장이 온전히 표시되는 것을 확인했다.

배포 후 Test에서 문제가 있었던 입력으로 **새 테스트**를 실행해 세 필드의 문장과 Agent Trace를 확인해야 한다. 이미 완료된 결과는 새로 분석하지 않으면 바뀌지 않는다. 운영 데이터 외부 전송, Production 지침 적용, Docker 재배포, Git 게시는 이번 수정에서 수행하지 않았다.
