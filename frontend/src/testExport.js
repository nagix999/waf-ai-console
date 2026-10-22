import { reportTypes } from "./analysisDownloads.js";
import { testRunQuery } from "./testRuns.js";

const uuid = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
export const MAX_TEST_EXPORT_BYTES = 32 * 1024 * 1024;

export function testExportFilters(filters) {
  const { limit, offset, ...selection } = testRunQuery(filters);
  return selection;
}

function exportPostPath(id, preview = false) {
  if (!uuid.test(id || "")) throw Error("invalid_test_export_request");
  return `/api/v1/test-runs/${id}/${preview ? "export/preview" : "export.xlsx"}`;
}

async function exportError(response) {
  const body = await response.json().catch(() => ({}));
  const code = ["test_export_too_large", "test_export_too_many_columns", "test_evaluation_not_found", "test_export_busy",
    "test_export_selection_changed", "invalid_test_export_columns", "test_export_column_collision"].includes(body?.detail) ? body.detail : "test_export_unavailable";
  const error = Error(code); error.status = response.status; throw error;
}

export async function fetchTestExportPreview(id, filters, { fetchImpl = globalThis.fetch, signal } = {}) {
  const response = await fetchImpl(exportPostPath(id, true), { method: "POST", credentials: "include", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ filters }) });
  if (!response.ok) return exportError(response);
  const data = await response.json();
  if (!Number.isInteger(data?.count) || data.count < 0 || data.count > 5000 || !/^[a-f0-9]{64}$/.test(data.scope || "") ||
      !Array.isArray(data.columns) || !data.columns.length || data.columns.length > 512 ||
      data.columns.some(column => !/^[A-Za-z_][A-Za-z0-9_.-]*$/.test(column?.name || "") ||
        !["label_ko", "description", "example", "group"].every(key => typeof column[key] === "string")) ||
      new Set(data.columns.map(column => column.name)).size !== data.columns.length) throw Error("invalid_test_export_response");
  return data;
}

export async function fetchSelectedTestExport(id, payload, { fetchImpl = globalThis.fetch, signal } = {}) {
  if (!payload?.columns?.length) throw Error("invalid_test_export_request");
  const response = await fetchImpl(exportPostPath(id), { method: "POST", credentials: "include", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", Accept: reportTypes.xlsx }, body: JSON.stringify(payload) });
  return workbookResponse(response, signal);
}

export function testExportPath(id, query = {}) {
  if (!uuid.test(id || "") || Object.keys(query).some(key => !["evaluation_id", "reference_basis"].includes(key))) throw Error("invalid_test_export_request");
  return `/api/v1/test-runs/${id}/export.xlsx?${new URLSearchParams(query)}`;
}

export async function fetchTestExport(id, query, { fetchImpl = globalThis.fetch, signal } = {}) {
  const response = await fetchImpl(testExportPath(id, query), { method: "GET", credentials: "include", cache: "no-store", signal, headers: { Accept: reportTypes.xlsx } });
  return workbookResponse(response, signal);
}

async function workbookResponse(response, signal) {
  if (!response.ok) return exportError(response);
  if (response.headers.get("content-type")?.split(";")[0] !== reportTypes.xlsx) throw Error("invalid_test_export_response");
  if (Number(response.headers.get("content-length")) > MAX_TEST_EXPORT_BYTES) throw Error("test_export_too_large");
  const blob = await response.blob();
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  if (!blob.size || blob.size > MAX_TEST_EXPORT_BYTES) throw Error("test_export_too_large");
  return blob;
}

export function testExportError(error, w) {
  if (error?.status === 401) return w("로그인이 만료되었습니다. 다시 로그인하세요.", "Your session expired. Sign in again.");
  if (error?.status === 403) return w("문항 다운로드는 관리자만 사용할 수 있습니다.", "Case exports require administrator access.");
  if (error?.message === "test_export_selection_changed") return w("대상 문항이나 필드 구성이 바뀌었습니다. 대상 다시 확인을 눌러 건수를 확인하세요.", "Cases or field definitions changed. Check the export scope again.");
  if (error?.message === "invalid_test_export_columns") return w("다운로드할 필드를 다시 선택하세요.", "Select the export fields again.");
  if (error?.message === "test_export_column_collision") return w("긴 값의 분할 컬럼명이 추가 필드와 겹칩니다. 해당 추가 필드를 제외하고 다시 시도하세요.", "A continuation column conflicts with an extra field. Exclude that field and retry.");
  if (error?.message === "test_export_too_large") return w("출력 용량 한도를 초과했습니다. 부분 파일은 생성하지 않았습니다.", "The export exceeds the size limit. No partial file was created.");
  if (error?.message === "test_export_too_many_columns") return w("추가 필드와 긴 필드의 분할 컬럼이 너무 많아 다운로드할 수 없습니다.", "Too many extra fields or continuation columns to export.");
  if (error?.message === "test_export_busy") return w("다른 Excel을 작성 중입니다. 잠시 후 다시 시도하세요.", "Another Excel export is being prepared. Try again shortly.");
  if (error?.status === 404) return w("테스트 또는 선택한 평가 기록을 찾을 수 없습니다. 다시 조회하세요.", "The Test or selected evaluation was not found. Refresh this page.");
  return w("Excel을 내려받지 못했습니다. 잠시 후 다시 시도하세요.", "Unable to download Excel. Try again shortly.");
}

export function saveTestExport(blob, id, { documentImpl = document, urlImpl = URL, schedule = setTimeout } = {}) {
  testExportPath(id);
  const url = urlImpl.createObjectURL(blob), anchor = documentImpl.createElement("a");
  try {
    anchor.href = url; anchor.download = `WAF-Test-${id}.xlsx`; anchor.hidden = true;
    documentImpl.body.append(anchor); anchor.click();
  } finally {
    anchor.remove(); schedule(() => urlImpl.revokeObjectURL(url), 1000);
  }
}
