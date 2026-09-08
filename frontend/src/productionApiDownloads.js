// Fetch only the documented server PDF endpoint. Never send document text or
// user-entered URLs, and never execute an API example while downloading.
const MAX_BYTES = 10 * 1024 * 1024;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function apiDocumentPdfPath(schema) {
  if (!uuid.test(schema?.version_id || "") || !/^[0-9a-f]{64}$/i.test(schema?.content_hash || "")) throw new Error("invalid_document_reference");
  return `/api/v1/production-api.pdf?${new URLSearchParams({ expected_schema_version_id: schema.version_id, expected_schema_hash: schema.content_hash })}`;
}

export function apiDocumentDownloadError(error) {
  if (error?.status === 401) return "로그인이 만료되었습니다. 다시 로그인해 주세요.";
  if (error?.status === 403) return "API 정의서를 내려받을 권한이 없습니다.";
  if (error?.message === "input_schema_document_changed") return "입력 스키마가 변경됐습니다. 최신 정의서를 새로고침한 뒤 다시 내려받으세요.";
  if (error?.status === 413 || error?.message === "document_too_large") return "정의서가 PDF 출력 한도를 초과했습니다. 화면에서 내용을 확인해 주세요.";
  return "PDF를 내려받지 못했습니다. 잠시 후 다시 시도해 주세요.";
}

export async function fetchApiDocumentPdf(schema, { signal, fetchImpl = globalThis.fetch } = {}) {
  const response = await fetchImpl(apiDocumentPdfPath(schema), { method: "GET", credentials: "include", cache: "no-store", signal, headers: { Accept: "application/pdf" } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body?.detail === "input_schema_document_changed" ? body.detail : "document_export_unavailable");
    error.status = response.status; throw error;
  }
  if (response.headers.get("content-type")?.split(";")[0] !== "application/pdf") throw new Error("invalid_document_response");
  if (Number(response.headers.get("content-length")) > MAX_BYTES) throw new Error("document_too_large");
  const blob = await response.blob();
  if (!blob.size || blob.size > MAX_BYTES) throw new Error(blob.size ? "document_too_large" : "invalid_document_response");
  if (await blob.slice(0, 5).text() !== "%PDF-") throw new Error("invalid_document_response");
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  return blob;
}

export function saveApiDocumentPdf(blob, version, { documentImpl = document, urlImpl = URL, schedule = setTimeout } = {}) {
  if (!Number.isSafeInteger(version) || version < 1) throw new Error("invalid_document_reference");
  const url = urlImpl.createObjectURL(blob); const anchor = documentImpl.createElement("a");
  try {
    anchor.href = url; anchor.download = `Production_API_v0.2.0_schema-v${version}.pdf`; anchor.hidden = true;
    documentImpl.body.append(anchor); anchor.click();
  } finally { anchor.remove(); schedule(() => urlImpl.revokeObjectURL(url), 1000); }
}
