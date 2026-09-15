# ReportLab 시안 생성 도구

승인 전 디자인 검토에 사용한 독립 프로토타입을 보존한다. 승인된 조판은 `backend/app/services/report_pdf_layout.py`에 적용했다. 이 도구는 고정된 가상 데이터 3종만 생성하며 API·DB·LLM·환경 파일을 읽지 않는다. 결과 폴더에 같은 이름의 파일이 있으면 덮어쓰지 않고 중단한다.

- `prepare.mjs`: 웹 보고서의 내용 생성·파서를 재사용해 Markdown, 표시용 JSON, 비교용 입력 생성.
- `renderer.py`: JSON을 ReportLab PDF로 조판. 브라우저와 네트워크 사용 없음.
- `test_renderer.py`: 단위 테스트. 네트워크 연결과 하위 프로세스 실행을 차단.
- `verify.py`: 설치된 Poppler로 본문 보존과 페이지 경계 검사. 생성기의 운영 의존성이 아님.

## 준비 및 생성

프로젝트 루트에서 실행한다. Node는 기존 프런트엔드 요구 버전을 사용한다. Python 환경에는 프로젝트에 고정된 ReportLab 5.0.1이 있어야 한다. 필요한 의존성이 없으면 외부망에서 설치하려고 하지 말고 준비된 환경이나 이미지를 사용한다.

```bash
REPORT_PREVIEW_DIR=$(mktemp -d /tmp/waf-reportlab-preview-XXXXXX)
node scripts/reportlab_preview/prepare.mjs "$REPORT_PREVIEW_DIR"
python scripts/reportlab_preview/renderer.py "$REPORT_PREVIEW_DIR"
python -m pytest scripts/reportlab_preview/test_renderer.py -q
python scripts/reportlab_preview/verify.py "$REPORT_PREVIEW_DIR"
```

준비된 기존 API 이미지로 생성하려면 다음을 사용할 수 있다. 이미지 이름은 로컬에 이미 있는 태그로 지정한다. 운영 컨테이너를 교체하거나 운영 볼륨을 연결하지 않는다.

```bash
REPORT_PREVIEW_IMAGE=waf-ai-console-api:reportlab-final-20260915
docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$PWD/scripts/reportlab_preview,target=/prototype/scripts/reportlab_preview,readonly" \
  --mount "type=bind,source=$PWD/backend/app/assets/fonts,target=/prototype/backend/app/assets/fonts,readonly" \
  --mount "type=bind,source=$REPORT_PREVIEW_DIR,target=/preview" \
  "$REPORT_PREVIEW_IMAGE" python /prototype/scripts/reportlab_preview/renderer.py /preview
python scripts/reportlab_preview/verify.py "$REPORT_PREVIEW_DIR"
```

위 두 생성 방법 중 하나만 선택한다. 같은 폴더에서 둘 다 생성하면 PDF 파일 중복으로 중단된다. `pytest`에서 고정 시안 3종 테스트까지 실행하려면 준비된 JSON 폴더를 컨테이너의 `/preview`에 읽기 전용으로 연결한다. 이 폴더가 없으면 해당 테스트 하나만 생략한다.

기존 브라우저 비교 스크립트는 제거했다. 과거 참고 PDF만 디자인 이력으로 보존한다.

현재 도구는 그룹 카드·제목 칸 색상·발췌 배경과 페이지별 그룹명 반복을 적용한 시안 02를 생성한다.

검토용 완성 파일: [카드형 PDF 시안](../../docs/previews/reportlab-v2/README.md). [첫 시안](../../docs/previews/reportlab-v1/README.md)은 비교용 이력으로 보존한다.
