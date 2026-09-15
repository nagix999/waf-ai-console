// One authenticated GET, never a model call. Decoding requires explicit opt-in.
export const reportTypes = Object.freeze({
  pdf: "application/pdf",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
});
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function reportPath(id, format) {
  if (typeof id !== "string" || !uuid.test(id) || !Object.hasOwn(reportTypes, format)) throw new Error("invalid_report_request");
  return `/api/v1/analyses/${id}/report.${format}`;
}

export function reportDownloadError(error) {
  if (error?.status === 401) return "로그인이 만료되었습니다. 다시 로그인해 주세요.";
  if (error?.status === 403) return "보고서는 관리자만 내려받을 수 있습니다.";
  if (error?.status === 404) return "해당 분석을 찾을 수 없습니다.";
  if (error?.message === "report_not_final") return "실제 분석이 완료된 최종 결과만 내려받을 수 있습니다. 진행 중·실패·모의 분석은 제외합니다.";
  if (error?.message === "report_too_large") return "보고서가 출력 한도를 초과했습니다. 부분 파일은 생성하지 않았습니다. 화면에서 내용을 확인해 주세요.";
  if (error?.message === "report_export_busy") return "다른 PDF를 작성 중입니다. 잠시 후 다시 내려받아 주세요.";
  if (error?.message === "report_export_timeout") return "PDF 작성 시간이 초과됐습니다. 잠시 후 다시 시도해 주세요.";
  return "보고서를 내려받지 못했습니다. 잠시 후 다시 시도해 주세요.";
}

export async function fetchReportFile(id, format, { signal, pdfOptions, fetchImpl = globalThis.fetch } = {}) {
  let path = reportPath(id, format);
  if (format === "pdf" && pdfOptions) {
    path += "?" + new URLSearchParams({ include_appendix: pdfOptions.includeAppendix === true,
      include_decoding: pdfOptions.includeDecoding === true, theme: pdfOptions.theme === "dark" ? "dark" : "light" });
  }
  const response = await fetchImpl(path, {
    method: "GET", credentials: "include", cache: "no-store", signal,
    headers: { Accept: reportTypes[format] },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    // Never surface arbitrary server text or response bodies in the UI.
    const code = ["report_not_final", "report_too_large", "report_export_busy", "report_export_timeout"].includes(body?.detail) ? body.detail : "report_export_unavailable";
    const error = new Error(code); error.status = response.status; throw error;
  }
  if (response.headers.get("content-type")?.split(";")[0] !== reportTypes[format]) throw new Error("invalid_report_response");
  const expectedSize = Number(response.headers.get("content-length"));
  if (expectedSize > MAX_FILE_BYTES) throw new Error("report_too_large");
  const blob = await response.blob();
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  if (!blob.size || blob.size > MAX_FILE_BYTES) throw new Error(blob.size ? "report_too_large" : "invalid_report_response");
  return blob;
}

export function saveReportFile(blob, id, format, { documentImpl = document, urlImpl = URL, schedule = setTimeout } = {}) {
  reportPath(id, format); // Validate before creating a URL or a filename.
  const url = urlImpl.createObjectURL(blob);
  const anchor = documentImpl.createElement("a");
  try {
    anchor.href = url;
    anchor.download = `WAF-분석보고서-${id}.${format}`;
    anchor.hidden = true;
    documentImpl.body.append(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    // Give the browser time to consume the blob before freeing it; no storage.
    schedule(() => urlImpl.revokeObjectURL(url), 1000);
  }
}
