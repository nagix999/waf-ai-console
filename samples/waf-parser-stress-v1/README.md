# 파싱 난이도 WAF 로그 50건

운영 로그를 사용하지 않고 작성한 가상 데이터다. 요청 형식이 깨져도 남은 공격 근거로 판정할 수 있는지, 실제 정보가 부족할 때는 보류하는지 확인한다. 기대값은 실모델 실행 전에 고정했으며 독립 전문가가 검증한 정답은 아니다.

- 업로드: `parser_stress_50.json` — 정탐 20건, 오탐 20건, 보류 10건
- 해설: `reference/answers_50.json` — 원문 발췌·기대 파서 상태·기대 판정 이유. **업로드하거나 LLM에 전달하지 않는다.**
- 각 유형은 5건(정탐 2, 오탐 2, 보류 1)이다. WAF D/A는 관측값이지 정답이 아니다.

| 유형 | 현재 generic-http-v3 상태 |
| --- | --- |
| HTTP 버전 누락 | partial |
| 문자로 기록된 줄바꿈 (`\r\n`) | partial |
| JSON 안에 감싼 요청 원문 | failed |
| Syslog 접두사가 붙은 요청 | failed |
| HTTP/2 의사 헤더의 텍스트 덤프 | failed |
| 탭으로 구분한 요청 라인 | failed |
| CR 단독 줄바꿈·접힌 헤더 | partial |
| 본문만 수집된 로그 | failed |
| Multipart·중복 필드 | success |
| 긴 헤더·청크·본문 경계 | success |

총 partial 15, failed 25, success 10건이다. 이는 HTTP 구조 인식 상태이며 분석 실패나 공격 여부가 아니다. success도 HTTP 프레이밍의 유효성을 보장하지 않는다. HTTP/2 사례는 바이너리 프레임이 아닌 로그 텍스트다.

46번은 약 84KB 원문 끝에 공격 구문을 두어 입력 길이 제한 시 원문 선별·인용 보존을 검사한다. 48번은 긴 정상 쿠키의 대조 사례다. 주소는 문서용 IP·예약 도메인이고 인증값은 가짜다. 페이로드는 실행하거나 요청 대상으로 접속하지 않는다.

기존 [hard50](../waf-dummy-v1/hard_50.json)은 변경하지 않는다. 두 파일은 별도 이름으로 각각 50건씩 접수해 같은 모델·지침으로 비교한다. 테스트 업로드 경로는 `expected_verdict`, `difficulty`, `test_category`, `case_name`을 평가용으로 분리하고 LLM 입력에서 제외한다.

생성 파일의 재현성 확인(네트워크·모델 호출 없음):

```bash
backend/.venv/bin/python scripts/build_parser_stress_samples.py
```

생성기 `--emit-files`는 편집 도구용 JSON을 표준 출력으로 제공하며 파일을 직접 변경하지 않는다. 평가 후 결과에 맞춰 기대 라벨을 수정하지 않는다. 라벨 검토가 필요하면 별도 버전으로 남긴다.

2026-09-10에 `gpt-5.4-mini`로 기존 hard50과 함께 실제 평가했다. 조건·지표·불일치 문항은 [100건 평가 보고서](../../docs/evaluations/Parser_Stress_GPT54Mini_100_2026-09-10.md)에 기록했다. 가상 데이터의 결과이며 운영·Gemma4 성능을 의미하지 않는다.
