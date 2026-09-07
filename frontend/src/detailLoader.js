const POLL_MS = 3000;
const MAX_ATTEMPTS = 3;
const REQUEST_TIMEOUT_MS = 30000;

const isRunning = (detail) => ["pending", "processing"].includes(detail?.status);
const isTerminal = (detail) => ["completed", "failed"].includes(detail?.status);
const canRetry = (error) => !error?.status || [408, 429].includes(error.status) || error.status >= 500;

export function emptyDetailState(id) {
  return {
    id, detail: null, error: "", loading: false,
    labelHistory: null, labelHistoryError: "", labelsLoading: false,
    runs: null, runsError: "", runsLoading: false,
  };
}

// One in-flight request per resource. Refresh/cancel also invalidates responses
// from transports that cannot abort, so an old view can never publish late data.
function resource({ fetchData, onData, onError, onLoading, shouldPoll, setTimer, clearTimer }) {
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
    clearTimer(timer);
    timer = undefined;
    const version = ++generation;
    controller = new AbortController();
    onLoading(true);
    let delay;
    try {
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
      onData(value);
      if (shouldPoll()) delay = POLL_MS;
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
        if (delay !== undefined) timer = setTimer(load, delay);
      }
    }
  }

  return {
    refresh() { cancel(); failures = 0; return load(); },
    cancel,
  };
}

export function createDetailLoader({ id, api, onChange, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let state = emptyDetailState(id);
  let disposed = false;
  let agentVisible = false;

  function update(values) {
    if (disposed) return;
    state = { ...state, ...values };
    onChange(state);
  }

  function makeResource(fetchData, dataKey, errorKey, loadingKey, shouldPoll, onData) {
    return resource({
      fetchData, setTimer, clearTimer, shouldPoll,
      onLoading: (loading) => update({ [loadingKey]: loading }),
      onError: (error, retry) => update({
        [errorKey]: `${error.message || "조회 실패"}${retry ? " · 잠시 후 다시 조회합니다." : " · 자동 조회를 중지했습니다. 새로고침으로 다시 시도하세요."}`,
      }),
      onData: (value) => {
        const previous = state[dataKey];
        const hadError = Boolean(state[errorKey]);
        update({ [dataKey]: value, [errorKey]: "" });
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
      if (next.status !== previous?.status || hadError) {
        // Read history once more after observing completion; an earlier history
        // response may still contain running steps from before the final commit.
        if (agentVisible) agent.refresh();
        if (previous && next.status !== previous.status && isTerminal(next)) labels.refresh();
      }
    },
  );

  return {
    start() {
      if (disposed) return;
      update(emptyDetailState(id));
      detail.refresh();
      labels.refresh();
    },
    setAgentVisible(visible) {
      if (disposed || visible === agentVisible) return;
      agentVisible = visible;
      if (visible) agent.refresh();
      else agent.cancel();
    },
    refresh() {
      if (disposed) return;
      detail.refresh();
      labels.refresh();
      if (agentVisible) agent.refresh();
    },
    refreshAgent() {
      if (!disposed && agentVisible) agent.refresh();
    },
    dispose() {
      disposed = true;
      detail.cancel();
      labels.cancel();
      agent.cancel();
    },
  };
}
