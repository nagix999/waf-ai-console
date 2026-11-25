/* Wireframe-only projection of the existing AnalysisSummary timing fields. */
(function () {
  'use strict';
  const W = window.WAF = window.WAF || {};
  const keys = ['total', 'queue', 'processing'];
  const fields = { total: 'total_elapsed_ms', queue: 'queue_wait_ms', processing: 'processing_duration_ms' };
  const seconds = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value / 1000 : null;
  const format = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value.toLocaleString('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}초` : '—';
  function values(item = {}) {
    const result = Object.fromEntries(keys.map(key => [key, seconds(item[fields[key]])]));
    return { ...result, completed: item.status === 'completed', terminal: ['completed', 'failed', 'canceled', 'stopped'].includes(item.status), recorded: keys.filter(key => result[key] !== null).length };
  }
  function summary(rows = []) {
    const completed = rows.filter(item => item.status === 'completed');
    const samples = Object.fromEntries(keys.map(key => [key, completed.map(item => values(item)[key]).filter(value => value !== null).sort((a, b) => a - b)]));
    const recorded = Object.fromEntries(keys.map(key => [key, samples[key].length]));
    const missing = Object.fromEntries(keys.map(key => [key, completed.length - samples[key].length]));
    const mean = Object.fromEntries(keys.map(key => [key, samples[key].length ? samples[key].reduce((sum, value) => sum + value, 0) / samples[key].length : null]));
    const p95 = Object.fromEntries(keys.map(key => [key, samples[key].length ? samples[key][Math.ceil(samples[key].length * 0.95) - 1] : null]));
    return { total: rows.length, completed: completed.length, excluded: rows.length - completed.length, recorded, missing, mean, p95 };
  }
  W.timing = {
    values, format, summary,
    fields,
    labels: { total: '접수→완료', queue: '큐 대기', processing: '분석 소요' },
    definitions: {
      total: '서버에 분석이 접수된 때부터 최종 결과가 완료될 때까지입니다. 큐 대기 시간을 포함합니다.',
      queue: '분석 접수부터 worker가 처음 분석을 시작할 때까지입니다.',
      processing: 'worker의 첫 분석 시작부터 최종 완료까지입니다. 최초 큐 대기는 제외하지만 실행 중 대기와 재시도 및 복구 간격은 포함합니다. 병렬 단계의 시간 합계가 아닙니다.',
      response: 'HTTP 요청→응답은 요청별로 별도 측정해야 합니다. 즉시 반환한 202, 재요청이나 연결 종료에 따라 달라져 접수→완료와 같지 않습니다. 이 시안에서는 미측정입니다.'
    }
  };
})();
