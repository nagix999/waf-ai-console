# 브라우저 없는 분석 보고서 PDF

## 변경 내용

분석 상세의 PDF 버튼은 ReportLab 5.0.1로 파일을 바로 내려받는다. Chromium·Playwright 패키지, 브라우저 설치 단계, 브라우저용 PDF 렌더러·번들을 제거했다. 새 백엔드 이미지는 깨끗한 Python Debian 이미지에서 빌드한다. 기존 브라우저 이미지 위에서 파일만 지우는 방식이 아니다.

- 정탐 근거·오탐 근거·참고 내용: 제목 칸에만 각각 붉은색·녹색·회청색 배경.
- 근거 설명: 본문 배경 유지. 로그 발췌만 별도 배경색.
- 긴 근거: 다음 페이지에 그룹 제목을 반복하며 이어 표시. 일반 길이의 근거는 가능한 한 한 페이지에 유지.
- 한글 글꼴 내장, A4 페이지 번호와 PDF 목차 제공. 밝은/어두운 테마 유지.

웹과 PDF는 `buildAnalysisReport`와 `parseApiDocument`를 공유한다. 같은 입력·부록·디코딩 선택에서 문구와 순서를 유지한다. PDF 조판은 ReportLab이 담당하므로 웹 CSS를 직접 실행하지 않으며 줄바꿈·표 폭·페이지 나눔은 다르다. LLM으로 내용을 요약하거나 다시 작성하지 않는다. PDF 접근성 태그는 제공하지 않는다. 글꼴에 없는 문자와 제어문자는 `[U+XXXX]`로 표시한다.

## 구성과 보안

관리자 API → 허용한 보고서 필드 → 별도 Python 프로세스 → 로컬 Node 텍스트 변환 → ReportLab PDF 순서로 처리한다. Node는 기존 웹 보고서 생성기를 실행하는 일회성 프로세스다. React·브라우저·추가 HTTP 서버는 실행하지 않는다. Node 실행 파일과 자체 완결된 약 57 kB 변환 번들을 이미지에 포함한다.

PDF 생성 중 외부 다운로드·URL 접근·이미지/HTML 실행이 없다. 모든 동적 값은 문자로 출력한다. API Key·암호화 키·DB 접속 정보·`NODE_OPTIONS`를 하위 프로세스에 전달하지 않는다. 원문 전체·확장 필드·Agent 중간 출력은 제외한다. 관리자 권한·다운로드 감사와 명시적인 디코딩 접근 감사는 유지한다.

요청 계약은 유지한다: `GET /api/v1/analyses/{id}/report.pdf`, `include_appendix`, `include_decoding`, `theme=light|dark`. 서버 프로세스당 동시 출력 1건·45초·입력 봉투 2 MiB·표시 문서 512,000 bytes·1,000개 블록/행·최대 80페이지·10 MiB 제한을 적용한다. 한도 초과는 부분 파일 대신 오류로 반환한다. Excel 및 운영 API 정의서 PDF는 기존 방식 그대로다.

## 설치와 폐쇄망

Docker 배포는 기존 Compose 절차를 사용한다. 서버에 Chrome이나 Node를 별도 설치할 필요가 없다. 이미지 **빌드**에는 기본 이미지와 pip/npm 의존성이 필요하므로 외부 다운로드가 전혀 불가능한 운영망에서는 연결 가능한 빌드 환경에서 운영서버와 같은 아키텍처로 이미지를 만든 뒤 승인된 반입 절차로 옮긴다. Chromium만 없애도 모든 빌드 다운로드가 사라지는 것은 아니다.

1. 외부 빌드 환경에서 저장소 루트를 context로 두 이미지를 빌드한다.
2. `docker image save`로 API·Frontend 이미지를 내보낸다.
3. 운영망에서 `docker image load`로 가져온다.
4. 기존 `.env`, 포트, 볼륨, 모델·지침 설정을 유지하고 Compose의 `image`를 반입한 태그로 지정한다.
5. 기존 프로젝트 이름과 Compose 파일들을 그대로 사용해 `up -d --no-build --pull never --wait`를 실행한다.

DB 마이그레이션은 추가하지 않았다. 기존 데이터와 비밀 설정을 백업하고 `down -v`·볼륨 삭제는 하지 않는다. 과거 복구용 이미지와 백업은 자동으로 지우지 않는다.

Docker를 사용하지 않는 개발 환경은 Node 22 이상과 프로젝트 Python 의존성을 준비한 뒤 다음을 실행한다. 브라우저 설치 명령은 없다.

```bash
cd frontend
npm ci
npm run build:report
cd ../backend
pip install -e '.[dev]'
WAF_TEST_REPORT_RENDERER=1 pytest tests/test_report_pdf.py tests/test_report_pdf_layout.py
```

보고서 내용 생성기를 수정하면 변환 번들도 다시 빌드한다. Docker 빌드는 이를 자동으로 수행한다. 개발용 UI 자동화 스크립트는 기존 외부 테스트 브라우저를 사용할 수 있지만 배포 이미지의 의존성이 아니다.
