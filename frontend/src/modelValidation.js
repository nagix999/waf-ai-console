import { initialListState } from "./analysisView.js";
import { modelProfileError, providerOf } from "./llmProfiles.js";
import { newTestRequestKey, validateTestName } from "./testRuns.js";

const validFingerprint = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);

export function modelTestPayload(mode, includeDataset = false, expectedFingerprint, runOptions) {
  if (!["quick", "full"].includes(mode) || typeof includeDataset !== "boolean" || (mode === "quick" && includeDataset)) throw new Error("invalid_model_test_options");
  if (expectedFingerprint !== undefined && !validFingerprint(expectedFingerprint)) throw new Error("invalid_model_profile_fingerprint");
  if (runOptions && validateTestName(runOptions.name)) throw new Error("invalid_test_name");
  return { mode, include_dataset: includeDataset, ...(expectedFingerprint === undefined ? {} : { expected_profile_fingerprint: expectedFingerprint }), ...(runOptions ? { name: runOptions.name.trim(), idempotency_key: runOptions.idempotency_key } : {}) };
}

export const emptyFullValidationState = () => ({ profile: null, name: "", idempotencyKey: "", busy: false, error: "", needsReconfirm: false });

export function createFullValidationController({ api, onChange, onSubmitted, onRequireRefresh }) {
  let state = emptyFullValidationState(); let disposed = false;
  const publish = patch => { if (!disposed) { state = { ...state, ...patch }; onChange(state); } };
  function open(profile) {
    if (state.busy || disposed) return;
    const missingFingerprint = !validFingerprint(profile.profile_fingerprint);
    publish({ profile: { id: profile.id, name: profile.name, model_name: profile.model_name, provider: providerOf(profile), profile_fingerprint: profile.profile_fingerprint }, name: "", idempotencyKey: newTestRequestKey(), needsReconfirm: missingFingerprint,
      error: missingFingerprint ? "설정 식별 정보를 확인하지 못했습니다. 목록을 다시 조회합니다. 취소한 뒤 최신 프로필의 전체 검증을 다시 열어 확인하세요." : "" });
    if (missingFingerprint) onRequireRefresh?.();
  }
  function close() { if (!state.busy) publish({ profile: null, error: "", needsReconfirm: false }); }
  async function submit(includeDataset) {
    if (disposed || state.busy || !state.profile || state.needsReconfirm || !validFingerprint(state.profile.profile_fingerprint) || typeof includeDataset !== "boolean") return false;
    const nameError = validateTestName(state.name); if (nameError) { publish({ error: nameError }); return false; }
    const profile = state.profile;
    publish({ busy: true, error: "" });
    try {
      const result = await api.runModelProfileTest(profile.id, "full", includeDataset, profile.profile_fingerprint, { name: state.name.trim(), idempotency_key: state.idempotencyKey });
      if (disposed) return false;
      publish({ profile: null, busy: false });
      onSubmitted?.(result, profile, includeDataset); return true;
    } catch (error) {
      if (!disposed) {
        const changed = error.message === "model_profile_changed_reconfirm";
        publish({ busy: false, needsReconfirm: changed, error: changed ? "모달을 연 뒤 프로필 설정이 변경되어 실행하지 않았습니다. 최신 목록을 다시 조회합니다. 취소한 뒤 프로필의 공급자·모델·비용 안내를 다시 확인하고 전체 검증을 열어 주세요." : error.status ? modelProfileError(error) : "접수 응답을 확인하지 못했습니다. 자동으로 재요청하지 않습니다. 취소 후 최근 테스트 이력을 새로 확인하세요." });
        if (changed) onRequireRefresh?.();
      }
      return false;
    }
  }
  return { open, close, submit, setName(name) { if (!state.busy && !disposed) publish({ name, idempotencyKey: newTestRequestKey(), error: "" }); }, getState: () => state, dispose() { disposed = true; state = emptyFullValidationState(); } };
}

export function datasetListState(source) {
  if (typeof source !== "string" || !/^waf-internal-model-test-[A-Za-z0-9-]+$/.test(source)) return null;
  const state = initialListState("test");
  state.applied.source_system = source; state.draft.source_system = source; state.advanced = true;
  return state;
}

export function datasetEvaluationStatus(status) {
  const labels = { waiting: "기능 검증 완료 대기", running: "150건 판정 평가 진행 중", completed: "판정 평가 처리 완료", failed: "판정 평가 실행 실패", skipped: "판정 평가 미실행" };
  return Object.hasOwn(labels, status) ? labels[status] : "판정 평가 상태 미확인";
}

export function uploadLabelNotice(result) {
  if (!Number.isInteger(result?.label_attached) || !Number.isInteger(result?.label_unchanged)) return "";
  return `합성 기대값 연결 ${result.label_attached}건 · 기존 답안과 동일 ${result.label_unchanged}건. 분석이 완료되면 최종 판정과 비교합니다.`;
}

export function expectedVerdictUploadError(item) {
  const code = typeof item?.message === "string" ? item.message : item?.code;
  const messages = {
    expected_verdict_conflict: "기존 분석에 연결된 답안과 expected_verdict가 다릅니다. 기존 답안을 덮어쓰지 않고 이 행을 거부했습니다.",
    invalid_expected_verdict: "expected_verdict는 true_positive, false_positive, inconclusive 중 하나여야 합니다.",
  };
  return Object.hasOwn(messages, code) ? `${messages[code]} (${code})` : "";
}
