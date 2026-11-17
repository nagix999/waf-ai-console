import { analysisEvidenceMeta, rememberEvidenceMeta } from "./analysisEvidenceMeta.js";

const POLL_MS = 3000;
const MAX_ATTEMPTS = 3;
const REQUEST_TIMEOUT_MS = 30000;

const isRunning = (detail) => ["pending", "processing"].includes(detail?.status);
const isTerminal = (detail) => ["completed", "failed"].includes(detail?.status);
const canRetry = (error) => !error?.status || [408, 429].includes(error.status) || error.status >= 500;

export function emptyDetailState(id) {
  return {
    id, detail: null, evidenceMeta: null, error: "", loading: false,
    labelHistory: null, labelHistoryError: "", labelsLoading: false,
    runs: null, runsError: "", runsLoading: false,
    evidenceRelations: null, relationsError: "", relationsLoading: false,
    evidenceDisplay: null, displayError: "", displayLoading: false, displayPollingStopped: false,
  };
}
// One in-flight request per resource. Refresh/cancel also invalidates responses
// from transports that cannot abort, so an old view can never publish late data.
function resource({ fetchData, onData, onError, onLoading, onUnexpectedError, isActive, shouldPoll, setTimer, clearTimer }) {
  let generation = 0;
  let controller;
  let timer;
  let deadline;
  let failures = 0;

  function cancel() {
    generation += 1;
    clearTimer(timer);
    clearTimer(deadline);
    timer = undefined;
    controller?.abort();
    controller = undefined;
    onLoading(false);
  }

  async function load() {
    if (!isActive()) return;
    clearTimer(timer);
    timer = undefined;
    const version = ++generation;
    controller = new AbortController();
    let delay;
    try {
      onLoading(true);
      if (version !== generation || !isActive()) return;
      const request = fetchData({ signal: controller.signal });
      const value = await Promise.race([request, new Promise((_, reject) => {
        deadline = setTimer(() => {
          const error = new Error("조회 시간 초과");
          error.status = 408;
          reject(error);
          controller?.abort();
        }, REQUEST_TIMEOUT_MS);
      })]);
      if (version !== generation) return;
      failures = 0;
      try {
        onData(value);
        if (shouldPoll()) delay = POLL_MS;
      } catch {
        // A display/callback error is not a transient transport failure.
        const error = new Error("detail_callback_failed");
        error.status = 400;
        throw error;
      }
    } catch (error) {
      if (version !== generation) return;
      failures += 1;
      const retry = canRetry(error) && failures < MAX_ATTEMPTS;
      onError(error, retry);
      if (retry) delay = POLL_MS * failures;
    } finally {
      if (version === generation) {
        clearTimer(deadline);
        deadline = undefined;
        controller = undefined;
        onLoading(false);
        if (delay !== undefined) timer = setTimer(loadInBackground, delay);
      }
    }
  }

  // Both user refreshes and timer callbacks need the same final boundary.
  // Callback failures must stop polling, without logging arbitrary error text.
  function loadInBackground() {
    const version = generation + 1;
    load().catch(() => {
      if (version !== generation) return;
      cancel();
      onUnexpectedError();
    });
  }

  return {
    refresh() { cancel(); failures = 0; if (isActive()) loadInBackground(); },
    cancel,
  };
}

export function createDetailLoader({ id, api, onChange, includeLabels = true, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let state = emptyDetailState(id);
  let disposed = false;
  let agentVisible = false;
  let observerFailed = false;

  function update(values) {
    if (disposed || observerFailed) return false;
    state = { ...state, ...values };
    try {
      onChange(state);
      return true;
    } catch {
      // A broken consumer cannot render its own error reliably. Keep a safe
      // readable snapshot, stop all work, and retry notification only after an
      // explicit refresh. Do not repeatedly call or log the failing observer.
      observerFailed = true;
      state = { ...state, error: "화면을 갱신하지 못했습니다. 새로고침으로 다시 시도하세요.",
        loading: false, labelsLoading: false, runsLoading: false };
      detail.cancel(); labels.cancel(); agent.cancel();
      return false;
    }
  }

  function makeResource(fetchData, dataKey, errorKey, loadingKey, shouldPoll, onData) {
    return resource({
      fetchData, setTimer, clearTimer, shouldPoll, isActive: () => !disposed && !observerFailed,
      onLoading: (loading) => update({ [loadingKey]: loading }),
      onError: (error, retry) => update({
        [errorKey]: `${error?.status === 408 ? "조회 시간 초과" : "조회 실패"}${retry ? " · 잠시 후 다시 조회합니다." : " · 자동 조회를 중지했습니다. 새로고침으로 다시 시도하세요."}`,
      }),
      onUnexpectedError: () => update({
        [errorKey]: "화면을 갱신하지 못했습니다. 새로고침으로 다시 시도하세요.", [loadingKey]: false,
      }),
      onData: (value) => {
        const previous = state[dataKey];
        const hadError = Boolean(state[errorKey]);
        if (!update({ [dataKey]: value, [errorKey]: "" })) return;
        onData?.(value, previous, hadError);
      },
    });
  }

  const labels = makeResource(
    async (options) => (await api.evaluationLabels(id, options)).items,
    "labelHistory", "labelHistoryError", "labelsLoading", () => false,
  );
  const agent = makeResource(
    (options) => api.agentRuns(id, options),
    "runs", "runsError", "runsLoading", () => agentVisible && isRunning(state.detail) && !state.error,
  );
  const detail = makeResource(
    (options) => api.analysis(id, options),
    "detail", "error", "loading", () => isRunning(state.detail),
    (next, previous, hadError) => {
      const transport = api.analysisEvidenceMeta?.(next) || analysisEvidenceMeta(next);
      // Even a pre-header server must not cause a browser to invent a different
      // evidence list from the JSON it just received.
      const evidenceMeta = transport || (next.result?.schema_version === "waf-analysis-v3"
        ? { view: "core-v1", status: "skipped" } : null);
      if (evidenceMeta) rememberEvidenceMeta(next, evidenceMeta);
      update({ evidenceMeta });
      if (next.status !== previous?.status || hadError) {
        // Read history once more after observing completion; an earlier history
        // response may still contain running steps from before the final commit.
        if (agentVisible) agent.refresh();
        if (includeLabels && previous && next.status !== previous.status && isTerminal(next)) labels.refresh();
      }
    },
  );

  return {
    start() {
      if (disposed) return;
      observerFailed = false;
      if (!update(emptyDetailState(id))) return;
      detail.refresh();
      if (includeLabels) labels.refresh();
    },
    setAgentVisible(visible) {
      if (disposed || visible === agentVisible) return;
      agentVisible = visible;
      if (visible) agent.refresh();
      else agent.cancel();
    },
    // Compatibility hook only. Core evidence now arrives with the analysis;
    // changing tabs must not fetch or apply an original-evidence display plan.
    setEvidenceVisible() {},
    refresh() {
      if (disposed) return;
      observerFailed = false;
      detail.refresh();
      if (includeLabels) labels.refresh();
      if (agentVisible) agent.refresh();
    },
    refreshAgent() {
      if (!disposed && agentVisible) agent.refresh();
    },
    refreshRelations() {
      if (!disposed) detail.refresh();
    },
    getState() { return state; },
    dispose() {
      disposed = true;
      detail.cancel();
      labels.cancel();
      agent.cancel();
    },
  };
}
