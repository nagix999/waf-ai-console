(function () {
  'use strict';
  const W = window.WAF;
  const h = W.h;
  const pages = W.pages;
  const verdictNames = { true_positive: '정탐', false_positive: '오탐', inconclusive: '판정 보류' };
  const state = { filters: {}, statistics: {}, statisticsSources: {}, datasetRows: {}, selected: [], newMode: 'dataset' };
  const number = n => Number(n || 0).toLocaleString('ko-KR');
  const percent = n => n == null ? '—' : `${(Number(n) * 100).toFixed(1)}%`;
  const query = () => W.query instanceof URLSearchParams ? W.query : new URLSearchParams(location.hash.split('?')[1] || '');
  const analyses = () => W.data.analyses || [];
  const runs = () => W.data.tests || [];
  const datasets = () => W.data.datasets || [];
  const run = () => runs().find(r => String(r.id) === query().get('id')) || runs()[0] || {};
  const dataset = () => datasets().find(r => String(r.id) === query().get('id')) || datasets()[0] || {};
  const target = e => e?.dataset ? e : e?.target?.closest?.('[data-action]') || e?.currentTarget || {};
  const button = (label, action, kind = 'secondary', attrs = '') => `<button type="button" class="button button-${kind}" data-action="${action}" ${attrs}>${h(label)}</button>`;
  const go = (label, route, params, kind = 'secondary') => button(label, 'test-navigate', kind, `data-route="${route}" data-params="${h(JSON.stringify(params || {}))}"`);
  const option = (value, label, selected) => `<option value="${h(value)}" ${String(selected) === String(value) ? 'selected' : ''}>${h(label)}</option>`;
  const select = (label, name, choices, selected, attrs = '') => `<label class="field"><span>${h(label)}</span><select name="${name}" ${attrs}>${choices.map(c => option(c[0], c[1], selected)).join('')}</select></label>`;
  const verdictOptions = [['', '전체'], ['true_positive', '정탐'], ['false_positive', '오탐'], ['inconclusive', '판정 보류']];
  const tableBox = id => `<div class="table-shell"><div id="${id}" aria-label="${h(id)}"></div></div>`;
  const empty = text => `<div class="empty-state">${h(text)}</div>`;
  const label = text => `<span class="small muted">${h(text)}</span>`;
  const help = (text, description) => `<span class="metric-label">${h(text)} <button type="button" class="help-dot" aria-label="${h(description)}" data-tooltip="${h(description)}">?</button></span>`;
  const metric = (title, value, note, definition) => `<div class="metric">${definition ? help(title, definition) : label(title)}<div class="metric-value">${h(value)}</div><div class="small muted">${h(note || '')}</div></div>`;
  const config = r => `<details class="configuration-disclosure"><summary>실행에 사용한 설정</summary><div class="context-grid">${[
    ['주 분석 모델', r.primary || '기록 없음'], ['검증 모델', r.verifier || '기록 없음'], ['근거 정리 모델', r.editor || '기록 없음'],
    ['분석 지침', r.instructions || '기록 없음'], ['입력 스키마', r.schema || '기록 없음'], ['파서', r.parser || '기록 없음'], ['시그니처 설명', r.explanations || '기록 없음']
  ].map(([k, v]) => `<div class="context-item"><span class="small muted">${h(k)}</span><strong>${h(v)}</strong></div>`).join('')}</div></details>`;
  function casesFor(r) {
    const explicit = analyses().filter(a => a.test_id === r.id || a.test_run_id === r.id || a.run_id === r.id);
    return explicit.length ? explicit : analyses().filter(a => a.purpose === 'test');
  }
  function filtered(r) {
    const f = { ...(state.filters[r.id] || {}), ...Object.fromEntries(['verdict', 'reference', 'status', 'search'].map(k => [k, query().has(k) ? query().get(k) : state.filters[r.id]?.[k] || ''])) };
    return casesFor(r).filter(a => (!f.verdict || a.verdict === f.verdict) && (!f.reference || a.reference === f.reference) && (!f.status || a.status === f.status) && (!f.search || `${a.event_id} ${a.company} ${a.signature}`.toLowerCase().includes(f.search.toLowerCase())));
  }
  function stats(rows) {
    const complete = rows.filter(a => a.status === 'completed');
    const comparable = complete.filter(a => Object.hasOwn(verdictNames, a.reference) && Object.hasOwn(verdictNames, a.verdict));
    const correct = comparable.filter(a => a.verdict === a.reference).length;
    const tp = comparable.filter(a => a.reference === 'true_positive' && a.verdict === 'true_positive').length;
    const fp = comparable.filter(a => a.reference !== 'true_positive' && a.verdict === 'true_positive').length;
    const fn = comparable.filter(a => a.reference === 'true_positive' && a.verdict !== 'true_positive').length;
    const precision = tp + fp ? tp / (tp + fp) : null;
    const recall = tp + fn ? tp / (tp + fn) : null;
    return { complete, comparable, correct, precision, recall, accuracy: comparable.length ? correct / comparable.length : null, f1: precision != null && recall != null && precision + recall ? 2 * precision * recall / (precision + recall) : null };
  }
  function caseColumns() {
    return [
      { formatter: 'rowSelection', titleFormatter: 'rowSelection', hozAlign: 'center', headerSort: false, width: 48 },
      { title: '문항', field: 'event_id', minWidth: 160, formatter: c => `<button class="table-link" data-action="test-case-open" data-id="${h(c.getRow().getData().id)}">${h(c.getValue())}</button>` },
      { title: '회사 / 시그니처', field: 'signature', minWidth: 210, formatter: c => `<div class="cell-title">${h(c.getValue())}</div><div class="small muted">${h(c.getRow().getData().company)}</div>` },
      { title: '심층 판정', field: 'verdict', width: 126, formatter: c => c.getRow().getData().status === 'completed' ? W.verdict(c.getValue()) : W.status(c.getRow().getData().status) },
      { title: '참고 라벨', field: 'reference', width: 124, formatter: c => c.getValue() ? W.verdict(c.getValue()) : '<span class="muted">미등록</span>' },
      { title: '비교', field: 'reference', width: 100, formatter: c => { const a = c.getRow().getData(); return a.status !== 'completed' || !a.reference ? '<span class="muted">—</span>' : W.badge(a.verdict === a.reference ? '일치' : '불일치', a.verdict === a.reference ? 'success' : 'warning'); } },
      { title: '분석 요약', field: 'summary', minWidth: 260, formatter: c => `<span class="cell-summary" title="${h(c.getValue() || '')}">${h(c.getValue() || '결과 대기 중')}</span>` },
      { title: '접수→완료', field: 'total_elapsed_ms', width: 172, hozAlign: 'right', formatter: c => { const time = W.timing.values(c.getRow().getData()); return `<strong>${h(W.timing.format(time.total))}</strong><div class="small muted">큐 ${h(W.timing.format(time.queue))} / 분석 ${h(W.timing.format(time.processing))}</div>`; } }
    ];
  }
  function context(r) {
    return `<div class="test-context-strip"><span>${W.badge(r.purpose === 'official' ? '공식 테스트' : '개발 테스트', r.purpose === 'official' ? 'violet' : 'neutral')}</span><span>${W.status(r.status)}</span><span class="small muted">${h(r.dataset || '직접 입력 / 파일')}</span><span class="small muted">${h(r.date || '')}</span></div>`;
  }
  function localTabs(r, current) {
    return `<nav class="local-tabs" aria-label="테스트 상세">${[['cases', '문항별 결과'], ['evaluation', '평가 상세'], ['statistics', '통계 요약']].map(([key, title]) => `<button type="button" class="local-tab ${current === key ? 'active' : ''}" ${current === key ? 'aria-current="page"' : ''} data-action="test-tab" data-id="${h(r.id)}" data-tab="${key}">${title}</button>`).join('')}</nav>`;
  }
  pages.tests = {
    render() {
      const items = runs();
      return W.heading('테스트', '설정을 비교하고, 정답 데이터로 판정 품질을 확인합니다.', go('테스트 비교', 'compare', {}, 'secondary') + go('새 테스트', 'newtest', {}, 'primary')) +
        `<div class="metrics">${metric('전체 테스트', number(items.length), '공식 테스트와 개발 테스트')}${metric('공식 테스트', number(items.filter(r => r.purpose === 'official').length), '운영 반영 검토에 사용 가능')}${metric('처리 중', number(items.filter(r => ['running', 'processing', 'queued'].includes(r.status)).length), '요청 대기와 모델 처리 포함')}${metric('실행 실패', number(items.reduce((s, r) => s + Number(r.failed || 0), 0)), '판정 불일치와 별도 집계')}</div>` +
        `<section class="panel"><div class="panel-header"><div><h2>테스트 목록</h2><p class="small muted">테스트 이름을 누르면 문항별 결과와 평가를 확인할 수 있습니다.</p></div><div class="toolbar"><input id="test-list-search" class="search-field" type="search" placeholder="테스트 이름, 회사, 시그니처 검색" aria-label="테스트 검색" value="${h(query().get('q') || '')}">${select('종류', 'test-kind', [['', '전체'], ['official', '공식 테스트'], ['development', '개발 테스트']], query().get('kind') || '')}</div></div>${tableBox('tests-table')}</section>`;
    },
    mount() {
      W.table('tests-table', runs(), [
        { title: '테스트 이름', field: 'name', minWidth: 260, formatter: c => `<button class="table-link" data-action="test-open" data-id="${h(c.getRow().getData().id)}">${h(c.getValue())}</button><div class="small muted">${h(c.getRow().getData().dataset || '직접 입력 / 파일')}</div>` },
        { title: '종류', field: 'purpose', width: 128, formatter: c => W.badge(c.getValue() === 'official' ? '공식 테스트' : '개발 테스트', c.getValue() === 'official' ? 'violet' : 'neutral') },
        { title: '처리 상태', field: 'status', width: 118, formatter: c => W.status(c.getValue()) },
        { title: '문항 수', field: 'cases', width: 90, hozAlign: 'right' },
        { title: '완료 / 실패', field: 'completed', width: 130, hozAlign: 'right', formatter: c => `${number(c.getValue())} <span class="muted">/</span> <span class="${c.getRow().getData().failed ? 'text-danger' : 'muted'}">${number(c.getRow().getData().failed)}</span>` },
        { title: '정확도', field: 'accuracy', width: 100, hozAlign: 'right', formatter: c => percent(c.getValue()) },
        { title: '실행일', field: 'date', minWidth: 132 }
      ], { pagination: 'local', paginationSize: 8, selectableRows: false });
      const applyListFilters = () => {
        const search = document.getElementById('test-list-search')?.value.toLowerCase() || '';
        const kind = document.querySelector('[name="test-kind"]')?.value || '';
        W.tables['tests-table'].setFilter(r => (!kind || r.purpose === kind) && `${r.name} ${r.dataset} ${casesFor(r).map(a => `${a.event_id} ${a.company} ${a.signature}`).join(' ')}`.toLowerCase().includes(search));
        const params = new URLSearchParams(W.query);
        if (search) params.set('q', search); else params.delete('q');
        if (kind) params.set('kind', kind); else params.delete('kind');
        W.query = params;
        history.replaceState(null, '', `#tests${params.size ? '?' + params : ''}`);
      };
      W.tables['tests-table']?.on('tableBuilt', applyListFilters);
      document.getElementById('test-list-search')?.addEventListener('input', applyListFilters);
      document.querySelector('[name="test-kind"]')?.addEventListener('change', applyListFilters);
    },
    action: commonAction
  };
  function casePanel(r) {
    const f = { ...(state.filters[r.id] || {}), ...Object.fromEntries(['search', 'verdict', 'reference', 'status'].filter(k => query().has(k)).map(k => [k, query().get(k)])) };
    return `<section class="panel"><div class="panel-header"><div><h2>문항별 결과</h2><p class="small muted">${number(filtered(r).length)}건이 현재 조건에 포함됩니다. 다운로드는 현재 페이지가 아닌 조건 전체를 사용합니다.</p></div><div class="toolbar">${button('참고 라벨 입력', 'test-label')}${button('정답 데이터에 추가', 'test-add-dataset')}${button('Excel 다운로드', 'test-export')}</div></div><div class="panel-body"><form id="test-case-filter" class="toolbar filter-row"><label class="field"><span>문항 검색</span><input class="search-field" type="search" name="search" placeholder="이벤트 ID, 회사, 시그니처" value="${h(f.search || '')}"></label>${select('심층 판정', 'verdict', verdictOptions, query().get('verdict') || f.verdict || '')}${select('참고 라벨', 'reference', verdictOptions, query().get('reference') || f.reference || '')}${select('처리 상태', 'status', [['', '전체'], ['completed', '완료'], ['failed', '실패'], ['processing', '처리 중']], query().get('status') || f.status || '')}<button class="button button-primary" type="submit">검색</button>${button('초기화', 'test-filter-clear', 'ghost')}</form></div>${tableBox('test-cases-table')}<div class="panel-footer row small muted">체크한 문항에만 일괄 작업을 적용합니다. 심층 판정을 참고 라벨로 자동 복사하지 않습니다.</div></section>`;
  }
  function evaluationPanel(r) {
    const rows = casesFor(r), s = stats(rows), keys = Object.keys(verdictNames);
    return `<div class="metrics">${metric('정확도', percent(s.accuracy), `${number(s.correct)} / ${number(s.comparable.length)}건 일치`, '비교 가능한 완료 문항 중 참고 라벨과 심층 판정이 같은 비율입니다. 높을수록 전체 일치도가 높지만 판정별 성능을 함께 확인해야 합니다.')}${metric('정밀도', percent(s.precision), '정탐 판정 기준', '정탐으로 판정한 문항 중 참고 라벨도 정탐인 비율입니다. 낮으면 과탐이 많습니다. 참고 라벨이 판정 보류인 정탐 판정도 비일치로 계산합니다.')}${metric('재현율', percent(s.recall), '참고 정탐 기준', '참고 라벨이 정탐인 문항 중 정탐으로 찾아낸 비율입니다. 낮으면 공격을 놓치거나 보류하는 경우가 많습니다.')}${metric('F1', percent(s.f1), '정밀도와 재현율의 균형', '정밀도와 재현율의 조화 평균입니다. 높을수록 두 지표가 함께 좋습니다. 한쪽이 낮으면 F1도 낮아집니다.')}</div><div class="split evaluation-layout"><section class="panel"><div class="panel-header"><div><h2>참고 라벨과 심층 판정 비교</h2><p class="small muted">셀을 누르면 해당 조합의 문항으로 이동합니다.</p></div>${W.badge(`${number(s.comparable.length)}건 비교`, 'neutral')}</div><div class="panel-body"><div class="matrix-axis">가로: 심층 판정 / 세로: 참고 라벨</div><div class="confusion-matrix"><div class="matrix-corner">참고 ↘ AI →</div>${keys.map(k => `<div class="matrix-label">${verdictNames[k]}</div>`).join('')}${keys.map(expected => `<div class="matrix-label">${verdictNames[expected]}</div>${keys.map(actual => { const count = s.comparable.filter(a => a.reference === expected && a.verdict === actual).length; return `<button class="matrix-cell ${actual === expected ? 'matrix-correct' : count ? 'matrix-mismatch' : ''}" data-action="test-matrix" data-reference="${expected}" data-verdict="${actual}" aria-label="참고 ${verdictNames[expected]}, 심층 ${verdictNames[actual]} ${count}건"><strong>${number(count)}</strong><span>${actual === expected ? '일치' : '불일치'}</span></button>`; }).join('')}`).join('')}</div><p class="small muted">참고 라벨이 판정 보류인 문항도 포함합니다. 정답 미등록과 실행 실패는 비교에서 제외합니다.</p></div></section><section class="panel"><div class="panel-header"><h2>집계 범위</h2></div><div class="panel-body stack"><div class="summary-line"><span>전체 문항</span><strong>${number(rows.length)}</strong></div><div class="summary-line"><span>처리 완료</span><strong>${number(s.complete.length)}</strong></div><div class="summary-line"><span>비교 가능한 문항</span><strong>${number(s.comparable.length)}</strong></div><div class="summary-line"><span>참고 라벨 미등록</span><strong>${number(s.complete.length - s.comparable.length)}</strong></div><div class="summary-line"><span>실행 실패</span><strong>${number(rows.filter(a => a.status === 'failed').length)}</strong></div><div class="callout">참고 라벨은 분석가가 관리하는 비교 기준입니다. 공식 테스트는 사람이 승인한 정답 데이터의 공식 버전을 사용해야 합니다.</div></div></section></div>`;
  }
  const groupFields = [['signature', '시그니처'], ['company', '회사'], ['action', 'WAF 조치'], ['vendor', '벤더'], ['key', 'API 키 별칭'], ['severity', '심각도']];
  const filterFields = [['signature', '시그니처'], ['company', '회사'], ['action', 'WAF 조치'], ['verdict', '심층 판정'], ['reference', '참고 라벨'], ['severity', '심각도']];
  function statisticsState(r) {
    const source = query().get('stats');
    if (state.statisticsSources[r.id] !== source || !state.statistics[r.id]) {
      let saved;
      try { saved = source ? JSON.parse(source) : null; } catch (_) {}
      state.statistics[r.id] = saved && Array.isArray(saved.groups) && Array.isArray(saved.conditions) ? saved : { groups: ['signature'], mode: 'and', conditions: [{ field: 'signature', operator: 'contains', value: '' }], preset: '' };
      state.statisticsSources[r.id] = source;
    }
    return state.statistics[r.id];
  }
  function statisticsNavigate(r) { W.go('test', { id: r.id, tab: 'statistics', stats: JSON.stringify(state.statistics[r.id]) }); }
  function statisticalRows(r) {
    const f = statisticsState(r);
    return casesFor(r).filter(a => {
      if (f.preset === 'falsealarm' && !(a.reference === 'false_positive' && a.verdict === 'true_positive')) return false;
      if (f.preset === 'miss' && !(a.reference === 'true_positive' && a.verdict === 'false_positive')) return false;
      if (f.preset === 'mismatch' && (!a.reference || a.verdict === a.reference || a.status !== 'completed')) return false;
      const active = f.conditions.filter(c => c.value.trim());
      const result = active.map(c => { const actual = String(a[c.field] || ''), value = c.value.trim(); return c.operator === 'equals' ? actual === value : c.operator === 'not' ? actual !== value : actual.toLowerCase().includes(value.toLowerCase()); });
      return !result.length || (f.mode === 'and' ? result.every(Boolean) : result.some(Boolean));
    });
  }
  function groupedRows(r) {
    const groups = statisticsState(r).groups, map = new Map();
    for (const a of statisticalRows(r)) { const values = groups.map(key => key === 'action' ? ({ D: '차단', A: '허용' }[a[key]] || a[key] || '미등록') : a[key] || '미등록'), id = JSON.stringify(values); if (!map.has(id)) map.set(id, { values, rows: [] }); map.get(id).rows.push(a); }
    return [...map.values()].map(g => { const s = stats(g.rows), complete = s.complete.length; return { name: g.values.join(' / '), total: g.rows.length, tp: s.complete.filter(a => a.verdict === 'true_positive').length, fp: s.complete.filter(a => a.verdict === 'false_positive').length, hold: s.complete.filter(a => a.verdict === 'inconclusive').length, compared: s.comparable.length, accuracy: s.accuracy, distribution: complete ? s.complete.filter(a => a.verdict === 'true_positive').length / complete : 0, failed: g.rows.filter(a => a.status === 'failed').length }; }).sort((a, b) => b.total - a.total);
  }
  function timingStatistics(rows) {
    const time = W.timing.summary(rows);
    return `<section class="panel timing-statistics"><div class="panel-header"><div><h2>처리 시간</h2><p class="small muted">같은 문항 조건의 완료 결과만 집계합니다.</p></div>${W.badge('예시 값', 'neutral')}</div><div class="panel-body stack"><div class="metrics" style="--metric-count:3">${['total', 'queue', 'processing'].map(key => metric(`평균 ${W.timing.labels[key]}`, W.timing.format(time.mean[key]), `P95 ${W.timing.format(time.p95[key])} / 기록 ${time.recorded[key]}건 / 미기록 ${time.missing[key]}건`)).join('')}</div><dl class="definition-list">${['total', 'queue', 'processing'].map(key => `<div><dt>${h(W.timing.labels[key])}</dt><dd>${h(W.timing.definitions[key])}</dd></div>`).join('')}<div><dt>HTTP 요청→응답</dt><dd>${h(W.timing.definitions.response)}</dd></div></dl><p class="small muted">완료 ${time.completed}건이 대상이며 실패와 처리 중 등 ${time.excluded}건은 평균에서 제외합니다. 미기록은 0초로 계산하지 않습니다. P95는 기록된 값의 95%가 이 시간 안에 끝났다는 뜻입니다. 분석 소요는 단계 시간의 합계나 순수 LLM 추론 시간이 아닙니다.</p><p class="small muted">이 HTML의 값은 화면 구성을 위한 예시입니다. 실제 집계는 기존 서버의 접수, 최초 분석 시작과 완료 시각을 사용합니다. 파싱 상태만으로 추가 검증을 실행하지 않습니다.</p></div></section>`;
  }
  function statisticsPanel(r) {
    const f = statisticsState(r), rows = statisticalRows(r), s = stats(rows);
    return `<section class="panel"><div class="panel-header"><div><h2>그룹 집계</h2><p class="small muted">먼저 문항을 필터링한 뒤 최대 3개 필드로 묶습니다.</p></div>${W.badge(`${number(rows.length)}건 집계`, 'violet')}</div><div class="panel-body"><form id="statistics-form" class="stack"><div class="toolbar"><div class="segment-control" aria-label="불일치 필터">${[['', '전체'], ['mismatch', '참고 라벨과 다름'], ['falsealarm', '과탐 방향'], ['miss', '미탐 방향']].map(([v, text]) => `<button class="${f.preset === v ? 'active' : ''}" type="button" data-action="test-stats-preset" data-value="${v}">${text}</button>`).join('')}</div>${select('조건 연결', 'mode', [['and', 'AND: 모든 조건'], ['or', 'OR: 하나 이상']], f.mode)}</div><div class="condition-builder">${f.conditions.map((c, index) => `<div class="toolbar filter-row condition-row" data-index="${index}">${select('필드', 'condition-field', filterFields, c.field)}${select('조건', 'condition-operator', [['contains', '포함'], ['equals', '같음'], ['not', '다름']], c.operator)}<label class="field condition-value"><span>값</span><input name="condition-value" value="${h(c.value)}" placeholder="예: File Inclusion 또는 true_positive"></label>${f.conditions.length > 1 ? button('삭제', 'test-condition-remove', 'ghost', `data-index="${index}" aria-label="${index + 1}번째 조건 삭제"`) : ''}</div>`).join('')}</div><div>${button('조건 추가', 'test-condition-add', 'ghost')}</div><div class="grouping-controls toolbar">${select('집계 기준 1', 'group-1', groupFields, f.groups[0])}${select('집계 기준 2', 'group-2', [['', '사용 안 함'], ...groupFields], f.groups[1] || '')}${select('집계 기준 3', 'group-3', [['', '사용 안 함'], ...groupFields], f.groups[2] || '')}<button class="button button-primary" type="submit">집계하기</button></div></form></div>${tableBox('test-statistics-table')}<div class="panel-footer small muted">정탐 비율의 분모는 해당 그룹의 완료 문항입니다. 정확도는 참고 라벨이 있는 비교 가능 문항만 사용합니다. 적은 표본의 수치를 운영 규칙으로 단정하지 마세요.</div></section><div class="metrics">${metric('필터 후 문항', number(rows.length), '집계 기준 변경 전 범위')}${metric('비교 문항', number(s.comparable.length), '실행 실패와 정답 미등록 제외')}${metric('정확도', percent(s.accuracy), '참고 라벨과 심층 판정 일치 비율')}${metric('표본이 적은 그룹', number(groupedRows(r).filter(g => g.total < 5).length), '5건 미만은 해석에 주의')}</div>${timingStatistics(rows)}`;
  }
  pages.test = {
    render() {
      const r = run(), tab = query().get('tab') || 'cases';
      if (!r.id) return empty('표시할 테스트가 없습니다.');
      return `<div class="breadcrumb">${go('테스트', 'tests', {}, 'ghost')}<span>/</span><span>${h(r.name)}</span></div>` + W.heading(r.name, '실행 당시의 설정과 문항을 기준으로 결과를 확인합니다.', `${r.failed ? button('실패 항목 재실행', 'test-retry', 'secondary') : ''}${['running', 'processing', 'queued'].includes(r.status) ? button('테스트 중지', 'test-stop', 'danger') : ''}${go('이 설정으로 다시 테스트', 'newtest', { from: r.id }, 'secondary')}${r.purpose === 'official' ? go('운영 반영 검토', 'promotion', { id: r.id }, 'primary') : ''}`) + context(r) + config(r) + localTabs(r, tab) + (tab === 'evaluation' ? evaluationPanel(r) : tab === 'statistics' ? statisticsPanel(r) : casePanel(r));
    },
    mount() {
      const r = run(), tab = query().get('tab') || 'cases';
      if (tab === 'cases') {
        W.table('test-cases-table', filtered(r), caseColumns(), { selectableRows: true, pagination: 'local', paginationSize: 10 });
        W.tables['test-cases-table']?.on('rowSelectionChanged', data => { state.selected = data.map(a => a.id); });
        document.getElementById('test-case-filter')?.addEventListener('submit', e => { e.preventDefault(); state.filters[r.id] = Object.fromEntries(new FormData(e.target)); W.go('test', { id: r.id, tab: 'cases', ...state.filters[r.id] }); });
      }
      if (tab === 'statistics') {
        W.table('test-statistics-table', groupedRows(r), [
          { title: statisticsState(r).groups.map(k => groupFields.find(f => f[0] === k)?.[1] || k).join(' / '), field: 'name', minWidth: 250 },
          { title: '전체', field: 'total', width: 80, hozAlign: 'right' },
          { title: '정탐 / 오탐 / 보류', field: 'tp', minWidth: 192, formatter: c => { const a = c.getRow().getData(); return `<div class="verdict-stack-numbers"><span class="text-danger">${a.tp}</span><span class="text-success">${a.fp}</span><span class="text-warning">${a.hold}</span></div>`; } },
          { title: '정탐 비율', field: 'distribution', minWidth: 150, formatter: c => `<div class="mini-bar"><span style="width:${Math.round(c.getValue() * 100)}%"></span></div><span class="small">${percent(c.getValue())}</span>` },
          { title: '비교 문항', field: 'compared', width: 108, hozAlign: 'right' },
          { title: '정확도', field: 'accuracy', width: 108, hozAlign: 'right', formatter: c => percent(c.getValue()) },
          { title: '실행 실패', field: 'failed', width: 100, hozAlign: 'right' }
        ], { pagination: 'local', paginationSize: 8 });
        document.getElementById('statistics-form')?.addEventListener('submit', e => { e.preventDefault(); saveStatistics(e.target, r); statisticsNavigate(r); });
      }
    },
    action: commonAction
  };
  function saveStatistics(form, r) {
    const f = statisticsState(r), data = new FormData(form);
    f.mode = data.get('mode') || 'and';
    f.groups = [...new Set([data.get('group-1'), data.get('group-2'), data.get('group-3')].filter(Boolean))];
    f.conditions = [...form.querySelectorAll('.condition-row')].map(row => ({ field: row.querySelector('[name="condition-field"]').value, operator: row.querySelector('[name="condition-operator"]').value, value: row.querySelector('[name="condition-value"]').value }));
  }
  pages.datasets = {
    render() {
      return W.heading('정답 데이터', '모델 판정을 평가할 때 기준으로 사용하는 WAF 요청 사례입니다.', button('데이터셋 만들기', 'dataset-create', 'primary')) +
        `<div class="metrics">${metric('데이터셋', number(datasets().length), '주제와 사용 목적별 관리')}${metric('전체 사례', number(datasets().reduce((s, d) => s + Number(d.total || 0), 0)), '데이터셋 사본 기준')}${metric('사람 확인 완료', number(datasets().reduce((s, d) => s + Number(d.approved || 0), 0)), 'AI 판정의 자동 정답 전환 없음')}${metric('확인 필요', number(datasets().reduce((s, d) => s + Number(d.needs_review || 0), 0)), '라벨, 원문, 충돌 확인')}</div><section class="panel"><div class="panel-header"><h2>데이터셋 목록</h2><span class="small muted">공식 버전은 변경할 수 없습니다. 수정은 새 편집본에서 진행합니다.</span></div>${tableBox('datasets-table')}</section>`;
    },
    mount() {
      W.table('datasets-table', datasets(), [
        { title: '데이터셋', field: 'name', minWidth: 240, formatter: c => `<button class="table-link" data-action="dataset-open" data-id="${h(c.getRow().getData().id)}">${h(c.getValue())}</button>` },
        { title: '최신 공식 버전', field: 'version', minWidth: 132, formatter: c => ['published', 'official'].includes(c.getRow().getData().status) ? W.badge(c.getValue(), 'violet') : '<span class="muted">없음</span>' },
        { title: '전체 사례', field: 'total', width: 100, hozAlign: 'right' },
        { title: '사람 확인', field: 'approved', width: 100, hozAlign: 'right' },
        { title: '확인 필요', field: 'needs_review', width: 100, hozAlign: 'right', formatter: c => c.getValue() ? W.badge(number(c.getValue()), 'warning') : '0' },
        { title: '상태', field: 'status', width: 130, formatter: c => W.badge(['published', 'official'].includes(c.getValue()) ? '공식 버전 있음' : '편집 중', ['published', 'official'].includes(c.getValue()) ? 'success' : 'neutral') },
        { title: '최근 수정', field: 'updated', minWidth: 130 }
      ], { pagination: 'local', paginationSize: 8 });
    },
    action: commonAction
  };
  pages.dataset = {
    render() {
      const d = dataset(), published = query().get('version') === 'published', hasPublished = ['published', 'official'].includes(d.status);
      if (published && !hasPublished) return W.heading(d.name || '데이터셋', '아직 공식 버전이 없습니다.', go('편집 중인 사례 보기', 'dataset', { id: d.id })) + empty('사람이 라벨을 확인한 뒤 공식 버전을 만들어 주세요.');
      return `<div class="breadcrumb">${go('정답 데이터', 'datasets', {}, 'ghost')}<span>/</span><span>${h(d.name || '')}</span></div>` + W.heading(d.name || '데이터셋', '원문과 사람이 확인한 라벨을 함께 관리합니다.', `${published ? go('이 버전으로 공식 테스트', 'newtest', { dataset: d.id, official: '1' }, 'primary') : button('사례 추가', 'dataset-add') + button('공식 버전 만들기', 'dataset-publish', 'primary')}`) + `<div class="toolbar version-selector">${go('편집 중', 'dataset', { id: d.id }, !published ? 'primary' : 'secondary')}${hasPublished ? go(`공식 버전 ${d.version}`, 'dataset', { id: d.id, version: 'published' }, published ? 'primary' : 'secondary') : '<span class="badge">공식 버전 없음</span>'}<span class="small muted">${published ? '이 버전은 읽기 전용입니다.' : '편집 내용은 기존 공식 버전과 운영 설정에 영향을 주지 않습니다.'}</span></div><div class="callout ${published ? '' : 'callout-warning'}">${published ? '공식 테스트는 이 불변 버전의 원문과 승인된 라벨을 사용합니다.' : '포함 가능은 자동 검증을 통과했다는 뜻입니다. 정답을 사람이 승인했다는 의미가 아닙니다.'}</div><section class="panel"><div class="panel-header"><h2>사례</h2><div class="toolbar">${published ? '' : button('선택한 라벨 확인', 'dataset-approve') + button('선택한 사례 제외', 'dataset-exclude', 'ghost')}</div></div>${tableBox('dataset-cases-table')}</section>`;
    },
    mount() {
      const d = dataset(), published = query().get('version') === 'published';
      if (published && !['published', 'official'].includes(d.status)) return;
      const rows = state.datasetRows[d.id] || analyses().filter(a => a.purpose === 'test').slice(0, Number(d.total || 0)).map((a, i) => ({ ...a, validation: published || i % 4 ? '포함 가능' : '확인 필요', human: published || i % 3 !== 0 }));
      W.table('dataset-cases-table', rows, [
        ...(!published ? [{ formatter: 'rowSelection', titleFormatter: 'rowSelection', width: 48, headerSort: false, hozAlign: 'center' }] : []),
        { title: '시그니처 / 회사', field: 'signature', minWidth: 240, formatter: c => `<strong>${h(c.getValue())}</strong><div class="small muted">${h(c.getRow().getData().company)}</div>` },
        { title: '참고 라벨', field: 'reference', width: 135, formatter: c => c.getValue() ? W.verdict(c.getValue()) : W.badge('미등록', 'warning') },
        { title: '자동 검증', field: 'validation', width: 125, formatter: c => W.badge(c.getValue(), c.getValue() === '포함 가능' ? 'success' : 'warning') },
        { title: '사람 확인', field: 'human', width: 128, formatter: c => W.badge(c.getValue() ? '확인 완료' : '미확인', c.getValue() ? 'success' : 'neutral') },
        { title: '원문과 라벨', field: 'id', width: 150, formatter: c => button(published ? '보기' : '확인 / 수정', 'dataset-edit', 'ghost', `data-id="${h(c.getValue())}"`) }
      ], { pagination: 'local', paginationSize: 10, selectableRows: !published });
    },
    action: commonAction
  };
  pages.newtest = {
    render() {
      const mode = query().get('mode') || state.newMode, source = runs().find(r => r.id === query().get('from')) || runs()[0] || {}, official = query().get('official') === '1';
      return `<div class="breadcrumb">${go('테스트', 'tests', {}, 'ghost')}<span>/</span><span>새 테스트</span></div>` + W.heading('새 테스트', '현재 운영 설정을 바꾸지 않고 선택한 구성으로 실행합니다.') + `<form id="new-test-form" class="stack"><div class="split newtest-layout"><section class="panel"><div class="panel-header"><h2>테스트 문항</h2></div><div class="panel-body stack"><label class="field"><span>테스트 이름 <span class="muted small">선택</span></span><input name="name" placeholder="미입력 시 테스트 ID로 자동 생성"></label><div class="segment-control">${[['single', '단건 분석'], ['batch', '배치 파일 분석'], ['dataset', '데이터셋 분석']].map(([value, text]) => `<button type="button" class="${mode === value ? 'active' : ''}" data-action="test-new-mode" data-mode="${value}">${text}</button>`).join('')}</div>${mode === 'single' ? `<label class="field"><span>이벤트 JSON</span><textarea rows="12" name="event" class="mono" placeholder='{"event_id":"example-001","payload":"GET /example HTTP/1.1\\r\\nHost: example.test\\r\\n\\r\\n", ...}'></textarea></label><p class="small muted">운영 입력 스키마와 같은 기준으로 검증합니다. expected_verdict는 참고 라벨로 등록됩니다.</p>` : mode === 'batch' ? `<label class="upload-zone"><input type="file" accept=".json,.jsonl,.csv" name="file"><strong>분석할 파일 선택</strong><span class="small muted">JSON / JSONL / CSV · 형식과 문항 수는 실행 전에 확인</span></label><div class="callout">입력 내용은 가상 시안에서 서버에 전송하거나 저장하지 않습니다.</div>` : `${select('정답 데이터', 'dataset', datasets().map(d => [d.id, d.name]), query().get('dataset') || datasets()[0]?.id)}${select('버전', 'revision', [['published', '공식 버전'], ['draft', '편집 중']], 'published')}<label class="check-row"><input name="official" type="checkbox" ${official ? 'checked' : ''}><span><strong>공식 테스트로 실행</strong><span class="small muted">사람이 승인한 라벨이 있는 공식 버전만 사용합니다.</span></span></label>`}</div></section><section class="panel"><div class="panel-header"><h2>테스트 설정</h2>${W.badge('운영 변경 없음', 'success')}</div><div class="panel-body stack">${select('주 분석 모델 · Primary', 'primary', [[source.primary || 'Gemma 4 · 26B', source.primary || 'Gemma 4 · 26B']], source.primary)}${select('검증 모델 · Verifier', 'verifier', [[source.verifier || 'Gemma 4 · 26B', source.verifier || 'Gemma 4 · 26B'], ['same', '주 분석 모델과 동일']], source.verifier)}${select('근거 정리 모델 · Evidence Editor', 'editor', [[source.editor || 'Gemma 4 · 26B', source.editor || 'Gemma 4 · 26B']], source.editor)}<details class="configuration-disclosure"><summary>분석 지침과 입력 처리 설정</summary><div class="stack">${select('분석 지침', 'instructions', [[source.instructions || '공통 분석 지침', source.instructions || '공통 분석 지침']], source.instructions)}${select('입력 스키마', 'schema', [[source.schema || '기본 입력 스키마', source.schema || '기본 입력 스키마']], source.schema)}${select('파서', 'parser', [[source.parser || '범용 HTTP 파서', source.parser || '범용 HTTP 파서']], source.parser)}${select('시그니처 설명', 'explanations', [[source.explanations || '기본 설명집', source.explanations || '기본 설명집']], source.explanations)}</div></details><p class="small muted">검증을 통과한 프로필만 선택할 수 있습니다. Primary와 Verifier는 같은 공통 지침으로 독립 분석합니다.</p></div></section></div><div class="form-footer"><p class="small muted">시안에서는 모델 호출과 비용이 발생하지 않습니다.</p>${go('취소', 'tests', {}, 'secondary')}<button class="button button-primary" type="submit">실행 전 확인</button></div></form>`;
    },
    mount() { document.getElementById('new-test-form')?.addEventListener('submit', e => { e.preventDefault(); const f = new FormData(e.target), mode = query().get('mode') || state.newMode; if (mode === 'single' && !String(f.get('event') || '').trim()) { W.toast('분석할 이벤트 JSON을 입력하세요.'); return; } if (f.get('official') && f.get('revision') !== 'published') { W.toast('공식 테스트는 정답 데이터의 공식 버전으로만 실행할 수 있습니다.'); return; } W.dialog({ title: '테스트 실행 전 확인', body: `<div class="callout">테스트 이름: ${h(f.get('name') || '접수 시 테스트 ID 자동 생성')}</div><p>실제 서비스에서는 모델 호출과 비용이 발생할 수 있습니다. 외부 OpenAI 프로필을 선택했다면 원문이 승인된 외부 서버로 전송됩니다.</p><p class="small muted">이 HTML은 시안입니다. 테스트를 접수하거나 모델을 호출하지 않습니다.</p>`, footer: button('닫기', 'dialog-close', 'secondary') }); }); },
    action: commonAction
  };
  pages.compare = {
    render() {
      return W.heading('테스트 비교', '동일한 정답 데이터 공식 버전으로 실행한 테스트를 비교합니다.', go('테스트 목록', 'tests')) + `<section class="panel"><div class="panel-body"><form id="compare-form" class="toolbar">${select('기준 테스트', 'baseline', runs().map(r => [r.id, r.name]), query().get('baseline') || runs()[0]?.id)}${select('비교 테스트', 'candidate', runs().map(r => [r.id, r.name]), query().get('candidate') || runs()[1]?.id)}<button class="button button-primary" type="submit">비교</button></form></div></section><div id="comparison-content">${comparisonContent()}</div>`;
    },
    mount() { document.getElementById('compare-form')?.addEventListener('submit', e => { e.preventDefault(); W.go('compare', Object.fromEntries(new FormData(e.target))); }); },
    action: commonAction
  };
  function comparisonContent() {
    const a = runs().find(r => r.id === query().get('baseline')) || runs()[0], b = runs().find(r => r.id === query().get('candidate')) || runs()[1];
    if (!a || !b) return empty('비교할 테스트가 2개 이상 필요합니다.');
    const compatible = a.purpose === 'official' && b.purpose === 'official' && a.dataset === b.dataset;
    return `<div class="callout ${compatible ? '' : 'callout-warning'}">${compatible ? '같은 평가 기준입니다. 실제 구현에서는 정답 데이터 버전과 포함 문항 및 기준 해시까지 확인합니다.' : '평가 기준이 다릅니다. 수치는 나란히 볼 수 있지만 성능 개선으로 단정하거나 운영 반영 근거로 사용할 수 없습니다.'}</div><div class="split">${[a, b].map((r, index) => `<section class="panel"><div class="panel-header"><div><span class="eyebrow">${index ? '비교 테스트' : '기준 테스트'}</span><h2>${h(r.name)}</h2></div>${go('결과 보기', 'test', { id: r.id })}</div><div class="panel-body stack">${context(r)}<div class="metrics">${metric('정확도', percent(r.accuracy), r.dataset)}${metric('F1', percent(r.f1), '정탐 기준')}${metric('실행 실패', number(r.failed), '전체 문항 기준')}</div>${config(r)}</div></section>`).join('')}</div>`;
  }
  pages.promotion = {
    render() {
      const r = run(), isOfficial = r.purpose === 'official', ready = isOfficial && !Number(r.failed || 0) && ['completed', 'done'].includes(r.status);
      const current = W.data.production || {}, fields = [['primary', '주 분석 모델'], ['verifier', '검증 모델'], ['editor', '근거 정리 모델'], ['instructions', '분석 지침'], ['schema', '입력 스키마'], ['parser', '파서'], ['explanations', '시그니처 설명']];
      return `<div class="breadcrumb">${go('테스트', 'tests', {}, 'ghost')}<span>/</span>${go(r.name || '테스트 상세', 'test', { id: r.id }, 'ghost')}<span>/</span><span>운영 반영 검토</span></div>` + W.heading('운영 반영 검토', '공식 테스트 결과와 설정 변경 영향을 확인한 뒤 관리자가 승인합니다.') + `<div class="callout callout-warning">이 HTML에서는 운영 설정을 변경하지 않습니다. 실제 반영은 승인된 정답 데이터의 공식 평가와 적용 전 점검을 통과해야 합니다.</div><div class="split"><section class="panel"><div class="panel-header"><h2>적용 전 점검</h2>${W.badge(ready ? '검토 가능' : '확인 필요', ready ? 'success' : 'warning')}</div><div class="panel-body stack">${[[isOfficial, '공식 테스트', '개발 테스트는 운영 반영 근거로 사용할 수 없습니다.'], [!Number(r.failed || 0), '실행 실패 없음', `실행 실패 ${number(r.failed)}건`], [['completed', 'done'].includes(r.status), '테스트 처리 완료', '처리 중이거나 중지한 테스트는 사용할 수 없습니다.']].map(([ok, title, note]) => `<div class="readiness-item"><span class="readiness-mark ${ok ? 'ready' : ''}">${ok ? '✓' : '!'}</span><div><strong>${title}</strong><p class="small muted">${h(note)}</p></div></div>`).join('')}<div class="callout">추가 필수 확인: 정답의 사람 승인, 불변 공식 버전, 설정 해시 일치, 프로필의 최신 검증 및 egress, 근거 응답 준비 상태. 실제 API에서 확인하기 전에는 통과로 간주하지 않습니다.</div></div></section><section class="panel"><div class="panel-header"><h2>평가 결과</h2>${go('평가 상세', 'test', { id: r.id, tab: 'evaluation' })}</div><div class="panel-body"><h3>${h(r.name || '')}</h3><p class="small muted">${h(r.dataset || '')}</p><div class="metrics">${metric('정확도', percent(r.accuracy), '고정 합격선 없음')}${metric('F1', percent(r.f1), '관리자 검토 필요')}${metric('실행 실패', number(r.failed), '0건 필수')}</div></div></section></div><section class="panel"><div class="panel-header"><h2>설정 변경 비교</h2><span class="small muted">구성 전체를 하나의 버전으로 반영합니다.</span></div><div class="panel-body"><div class="configuration-comparison"><div class="comparison-cell comparison-label">구성</div><div class="comparison-cell comparison-label">현재 운영 설정</div><div class="comparison-cell comparison-label">테스트한 설정</div>${fields.map(([key, title]) => `<div class="comparison-cell"><strong>${title}</strong></div><div class="comparison-cell muted">${h(current[key] || '실제 운영 API에서 확인')}</div><div class="comparison-cell">${h(r[key] || '기록 없음')}</div>`).join('')}</div></div></section><section class="panel"><div class="panel-header"><h2>관리자 확인</h2></div><div class="panel-body stack"><label class="check-row"><input id="promotion-impact" type="checkbox"><span>변경 내용과 입력 스키마의 요청 거절 가능성을 확인했습니다.</span></label><label class="field"><span>검토 의견</span><textarea id="promotion-comment" rows="3" placeholder="주요 변경 내용과 운영 반영 판단 이유"></textarea></label><div class="row">${button('적용 전 점검 보기', 'promotion-check', 'primary')}<span class="small muted">시안에서는 실제 승인이나 반영을 수행하지 않습니다.</span></div></div></section>`;
    },
    mount() {},
    action: commonAction
  };
  function selectedRows() { return W.tables['test-cases-table']?.getSelectedData?.() || []; }
  function askLabel() {
    const selected = selectedRows(); if (!selected.length) { W.toast('참고 라벨을 입력할 문항을 먼저 선택하세요.'); return; }
    W.dialog({ title: `${selected.length}건 참고 라벨 입력`, body: `${select('참고 라벨', 'reference-label', verdictOptions.slice(1), 'true_positive')}<label class="field"><span>의견 <span class="muted">선택</span></span><textarea id="reference-comment" rows="3" placeholder="라벨을 선택한 이유"></textarea></label><div class="callout">분석 결과는 바꾸지 않습니다. 이 참고 라벨은 정답 데이터의 공식 승인과 별개입니다.</div>`, footer: button('취소', 'dialog-close') + button('시안에 반영', 'test-label-save', 'primary') });
  }
  function exportDialog() {
    const rows = filtered(run());
    const fields = [['event_id', '이벤트 ID', true], ['company_name', '회사', true], ['signature', '시그니처', true], ['waf_action', 'WAF 조치', true], ['payload', 'HTTP 원문', true], ['verdict', '심층 판정', true], ['expected_verdict', '참고 라벨', true], ['summary_ko', '분석 요약', true], ['severity', '심각도', true], ['explanation_ko', '세부 분석', false], ['attack_evidence', '주요 공격 요청 근거', true], ['benign_evidence', '주요 정상 요청 근거', true], ['context_evidence', '주요 참고 내용', true], ['source_ip', '출발지 IP', false], ['total_elapsed_ms', '접수→완료 시간(ms, 큐 포함)', false], ['queue_wait_ms', '큐 대기 시간(ms)', false], ['processing_duration_ms', '분석 소요 시간(ms, 최초 큐 대기 제외)', false]];
    W.dialog({ title: 'Excel 다운로드', body: `<div class="export-count"><strong>${number(rows.length)}건</strong><span>현재 검색 조건 전체 · 현재 페이지만이 아닙니다</span></div><p class="small muted">분석결과와 컬럼안내 2개 시트로 구성합니다. 다운로드 컬럼명은 영어입니다.</p><div class="export-fields">${fields.map(([en, ko, checked]) => `<label class="check-row"><input type="checkbox" name="export-field" value="${en}" ${checked ? 'checked' : ''}><span><strong>${en}</strong><span class="small muted">${ko}</span></span></label>`).join('')}</div><div class="callout callout-warning">payload를 선택하면 민감한 HTTP 원문이 포함됩니다. 각 근거 분류는 최대 5개를 줄바꿈과 ---로 구분합니다.</div>`, footer: button('취소', 'dialog-close') + button('다운로드 구성 확인', 'test-export-preview', 'primary') });
  }
  function commonAction(action, event) {
    const el = target(event), r = run();
    if (action === 'dialog-close') { W.closeDialog ? W.closeDialog() : document.querySelector('dialog[open]')?.close(); return true; }
    if (action === 'test-navigate') { try { W.go(el.dataset.route, JSON.parse(el.dataset.params || '{}')); } catch (_) {} return true; }
    if (action === 'test-open') { W.go('test', { id: el.dataset.id, tab: 'cases' }); return true; }
    if (action === 'test-tab') { W.go('test', { id: el.dataset.id, tab: el.dataset.tab }); return true; }
    if (action === 'test-case-open') { W.go('analysis', { id: el.dataset.id, from: 'test', test: r.id }); return true; }
    if (action === 'test-matrix') { state.filters[r.id] = { verdict: el.dataset.verdict, reference: el.dataset.reference }; W.go('test', { id: r.id, tab: 'cases', verdict: el.dataset.verdict, reference: el.dataset.reference }); return true; }
    if (action === 'test-filter-clear') { state.filters[r.id] = {}; W.go('test', { id: r.id, tab: 'cases' }); return true; }
    if (action === 'test-label') { askLabel(); return true; }
    if (action === 'test-label-save') { const v = document.querySelector('[name="reference-label"]')?.value; selectedRows().forEach(a => { const original = analyses().find(row => row.id === a.id); if (original) original.reference = v; }); document.querySelector('dialog[open]')?.close(); W.go('test', { id: r.id, tab: 'cases' }); W.toast('선택한 문항의 참고 라벨을 시안에 반영했습니다. 실제 저장은 하지 않았습니다.'); return true; }
    if (action === 'test-export') { exportDialog(); return true; }
    if (action === 'test-export-preview') { const fields = [...document.querySelectorAll('[name="export-field"]:checked')].map(c => c.value); if (!fields.length) { W.toast('다운로드할 필드를 1개 이상 선택하세요.'); return true; } document.querySelector('dialog[open]')?.close(); W.toast(`${filtered(r).length}건 / ${fields.length}개 컬럼 구성입니다. 시안에서는 파일을 생성하지 않습니다.`); return true; }
    if (action === 'test-retry') { W.dialog({ title: '실패 항목 재실행', body: `<p>실패한 ${number(r.failed)}건을 실패 당시 모델과 지침으로 다시 실행합니다.</p><div class="callout">기존 실패 이력은 보존하며, 새 실행이 성공하면 문항 결과와 평가에 반영합니다. 실제 서비스에서는 모델 호출과 비용이 발생합니다.</div><p class="small muted">시안에서는 모델을 호출하지 않습니다.</p>`, footer: button('닫기', 'dialog-close') }); return true; }
    if (action === 'test-stop') { W.dialog({ title: '테스트 중지', body: '<p>새 모델 호출을 중지합니다. 이미 완료한 결과와 실행 이력은 보존합니다.</p><p class="small muted">시안에서는 실제 작업 상태를 변경하지 않습니다.</p>', footer: button('닫기', 'dialog-close') }); return true; }
    if (action === 'test-add-dataset') { if (!selectedRows().length) { W.toast('정답 데이터에 추가할 문항을 먼저 선택하세요.'); return true; } W.dialog({ title: '테스트 사례를 정답 데이터에 추가', body: `${select('데이터셋', 'destination-dataset', datasets().map(d => [d.id, d.name]), datasets()[0]?.id)}<p>선택한 ${selectedRows().length}건의 입력과 명시적으로 등록된 참고 라벨을 복사합니다. 같은 사례는 중복 제외합니다.</p><div class="callout">심층 판정은 정답으로 복사하지 않습니다. 라벨이 없는 사례는 확인 필요 상태로 추가합니다.</div>`, footer: button('취소', 'dialog-close') + button('시안에서 추가', 'dataset-copy-confirm', 'primary') }); return true; }
    if (action === 'dataset-copy-confirm') { document.querySelector('dialog[open]')?.close(); W.toast('추가 흐름을 확인했습니다. 시안에서는 실제 데이터를 저장하지 않습니다.'); return true; }
    if (action === 'test-new-mode') { state.newMode = el.dataset.mode; W.go('newtest', { ...Object.fromEntries(query()), mode: el.dataset.mode }); return true; }
    if (action === 'test-stats-preset') { saveStatistics(document.getElementById('statistics-form'), r); statisticsState(r).preset = el.dataset.value; statisticsNavigate(r); return true; }
    if (action === 'test-condition-add' || action === 'test-condition-remove') { saveStatistics(document.getElementById('statistics-form'), r); const f = statisticsState(r); if (action.endsWith('add')) f.conditions.push({ field: 'verdict', operator: 'equals', value: '' }); else f.conditions.splice(Number(el.dataset.index), 1); statisticsNavigate(r); return true; }
    if (action === 'dataset-open') { W.go('dataset', { id: el.dataset.id }); return true; }
    if (action === 'dataset-create') { W.dialog({ title: '데이터셋 만들기', body: '<label class="field"><span>데이터셋 이름</span><input id="dataset-name" placeholder="예: 웹 요청 검증 세트"></label><label class="field"><span>설명 <span class="muted">선택</span></span><textarea rows="3" placeholder="용도와 포함할 사례"></textarea></label>', footer: button('취소', 'dialog-close') + button('시안에서 만들기', 'dataset-create-confirm', 'primary') }); return true; }
    if (action === 'dataset-create-confirm') { const name = document.getElementById('dataset-name')?.value.trim(); if (!name) { W.toast('데이터셋 이름을 입력하세요.'); return true; } W.data.datasets.push({ id: `demo-dataset-${Date.now()}`, name, version: '', total: 0, approved: 0, needs_review: 0, status: 'draft', updated: '방금' }); document.querySelector('dialog[open]')?.close(); W.go('datasets'); W.toast('이 브라우저의 시안 데이터에만 추가했습니다.'); return true; }
    if (action === 'dataset-publish') { W.dialog({ title: '공식 버전 만들기', body: '<p>원문과 라벨을 확인하고, 제외할 사례를 정리한 뒤 새 공식 버전을 만듭니다.</p><div class="callout callout-warning">자동 검증의 포함 가능 상태만으로 정답 승인을 대신할 수 없습니다. 승인된 라벨과 검증 기준이 고정되어야 공식 테스트에 사용할 수 있습니다.</div><p class="small muted">시안에서는 공식 버전을 발행하지 않습니다.</p>', footer: button('닫기', 'dialog-close') }); return true; }
    if (action === 'dataset-edit') { const a = analyses().find(row => row.id === el.dataset.id) || {}, readonly = query().get('version') === 'published'; W.dialog({ title: readonly ? '정답 데이터 사례' : '원문과 참고 라벨 확인', body: `<label class="field"><span>HTTP 원문</span><textarea rows="7" class="mono" ${readonly ? 'readonly' : ''}>${h(a.payload || '')}</textarea></label>${select('참고 라벨', 'dataset-reference', verdictOptions, a.reference || '', readonly ? 'disabled' : '')}<label class="field"><span>확인 의견</span><textarea rows="3" placeholder="원문과 라벨을 확인한 근거" ${readonly ? 'readonly' : ''}></textarea></label>${readonly ? '<div class="callout">공식 버전은 읽기 전용입니다.</div>' : '<label class="check-row"><input type="checkbox"><span>원문과 참고 라벨을 사람이 확인했습니다.</span></label>'}`, footer: button('닫기', 'dialog-close') + (readonly ? '' : button('시안에서 확인', 'dataset-copy-confirm', 'primary')) }); return true; }
    if (['dataset-add', 'dataset-approve', 'dataset-exclude'].includes(action)) { W.toast('데이터 편집 흐름의 시안입니다. 실제 추가, 승인, 제외는 수행하지 않습니다.'); return true; }
    if (action === 'promotion-check') { if (!document.getElementById('promotion-impact')?.checked) { W.toast('설정 변경 내용과 입력 스키마 영향을 먼저 확인하세요.'); return true; } W.dialog({ title: '적용 전 점검', body: '<div class="callout callout-warning">실제 서버 점검을 수행하지 않았으므로 운영 반영을 허용하지 않습니다.</div><p>공식 버전과 사람 승인, 실행 완료 및 실패 0건, 설정 지문, 프로필 검증, 허용 대상과 스키마 변경 영향을 서버에서 다시 확인해야 합니다.</p><p class="small muted">이 시안에는 실제 운영 반영 버튼이 없습니다.</p>', footer: button('확인', 'dialog-close', 'primary') }); return true; }
    return false;
  }
})();
