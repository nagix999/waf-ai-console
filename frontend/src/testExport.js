import { reportTypes } from "./analysisDownloads.js";

const uuid = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
export const MAX_TEST_EXPORT_BYTES = 32 * 1024 * 1024;

export function testExportPath(id, query = {}) {
  if (!uuid.test(id || "") || Object.keys(query).some(key => !["evaluation_id", "reference_basis"].includes(key))) throw Error("invalid_test_export_request");
  return `/api/v1/test-runs/${id}/export.xlsx?${new URLSearchParams(query)}`;
}

export async function fetchTestExport(id, query, { fetchImpl = globalThis.fetch, signal } = {}) {
  const response = await fetchImpl(testExportPath(id, query), { method: "GET", credentials: "include", cache: "no-store", signal, headers: { Accept: reportTypes.xlsx } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = Error(["test_export_too_large", "test_export_too_many_columns", "test_evaluation_not_found", "test_export_busy"].includes(body?.detail) ? body.detail : "test_export_unavailable");
    error.status = response.status; throw error;
  }
  if (response.headers.get("content-type")?.split(";")[0] !== reportTypes.xlsx) throw Error("invalid_test_export_response");
  if (Number(response.headers.get("content-length")) > MAX_TEST_EXPORT_BYTES) throw Error("test_export_too_large");
  const blob = await response.blob();
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  if (!blob.size || blob.size > MAX_TEST_EXPORT_BYTES) throw Error("test_export_too_large");
  return blob;
}

export function testExportError(error, w) {
  if (error?.status === 401) return w("로그인이 만료되었습니다. 다시 로그인하세요.", "Your session expired. Sign in again.");
  if (error?.status === 403) return w("전체 문항 다운로드는 관리자만 사용할 수 있습니다.", "Whole-Test exports require administrator access.");
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
