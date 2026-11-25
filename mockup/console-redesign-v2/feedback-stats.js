/* Pure aggregation helpers for the offline wireframe. No service or model calls. */
(() => {
  "use strict";
  const W = window.WAF = window.WAF || {};
  const UNRECORDED = "__unrecorded__";
  const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
  const text = value => typeof value === "string" ? value : "";
  const categoriesOf = row => [...new Set((Array.isArray(row.feedback_categories) ? row.feedback_categories : Array.isArray(row.categories) ? row.categories : []).filter(value => typeof value === "string" && value.length))];

  function validDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const instant = Date.parse(`${value}T00:00:00.000Z`);
    return Number.isFinite(instant) && new Date(instant).toISOString().slice(0, 10) === value;
  }

  // A display-only local time has no reliable timezone and is not guessed.
  function recordDate(row) {
    const value = row && row.created_at;
    if (typeof value !== "string") return null;
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
    if (!match || !validDate(match[1]) || Number(match[2]) > 23 || Number(match[3]) > 59 || Number(match[4]) > 59) return null;
    const instant = Date.parse(value);
    if (!Number.isFinite(instant)) return null;
    return new Date(instant + KST_OFFSET_MS).toISOString().slice(0, 10);
  }

  function normalize(rows, analyses = []) {
    const byId = new Map(analyses.map(analysis => [analysis.id, analysis]));
    return rows.map(row => {
      const analysisId = row.analysis_id || row.analysisId;
      const analysis = byId.get(analysisId) || {};
      const purpose = row.purpose || analysis.purpose || "unrecorded";
      const rawVersion = row.production_version_id ?? analysis.production_version_id;
      const productionVersion = purpose === "production" && analysis.purpose !== "test" && typeof rawVersion === "string" && rawVersion.trim() ? rawVersion.trim() : null;
      const company = row.company ?? row.company_name ?? analysis.company ?? analysis.company_name;
      const companyName = row.company_name ?? row.company ?? analysis.company_name ?? analysis.company;
      const verdict = Object.prototype.hasOwnProperty.call(row, "verdict") ? row.verdict : analysis.verdict;
      return {
        ...row,
        analysis_id: analysisId,
        event_id: row.event_id ?? analysis.event_id,
        company,
        company_name: companyName,
        signature: row.signature ?? analysis.signature,
        verdict,
        purpose,
        date: recordDate(row),
        production_version_id: productionVersion,
        categories: categoriesOf(row),
      };
    });
  }

  function filter(rows, filters = {}) {
    const { start = "", end = "", purpose = "", version = "", category = "", analysis = "", query = "" } = filters;
    if ((start && !validDate(start)) || (end && !validDate(end)) || (start && end && start > end)) {
      throw new RangeError("feedback_period_invalid");
    }
    const search = text(query).trim().toLocaleLowerCase();
    const labels = W.data && W.data.feedbackLabels || {};
    return rows.filter(row => {
      const date = recordDate(row);
      if ((start || end) && (!date || (start && date < start) || (end && date > end))) return false;
      if (purpose && purpose !== "all" && row.purpose !== purpose) return false;
      if (version && (row.purpose !== "production" || (version === UNRECORDED ? row.production_version_id !== null : row.production_version_id !== version))) return false;
      const categories = categoriesOf(row);
      if (category && !categories.includes(category)) return false;
      if (analysis && row.analysis_id !== analysis) return false;
      if (search) {
        const haystack = [row.event_id, row.company, row.company_name, row.signature, row.comment, row.author, row.analyst_id, ...categories, ...categories.map(value => labels[value] || value)].map(text).join(" ").toLocaleLowerCase();
        if (!haystack.includes(search)) return false;
      }
      return true;
    });
  }

  function summarize(rows) {
    return {
      feedbackCount: rows.length,
      analysisCount: new Set(rows.map(row => row.analysis_id).filter(Boolean)).size,
      multiCategoryCount: rows.filter(row => categoriesOf(row).length > 1).length,
      commentOnlyCount: rows.filter(row => !categoriesOf(row).length && text(row.comment).trim()).length,
    };
  }

  function categoryCounts(rows) {
    const counts = new Map(Object.keys(W.data && W.data.feedbackLabels || {}).map(category => [category, 0]));
    rows.forEach(row => categoriesOf(row).forEach(category => counts.set(category, (counts.get(category) || 0) + 1)));
    return [...counts].map(([category, count]) => ({ category, count, percent: rows.length ? count * 100 / rows.length : 0 }));
  }

  function versionCounts(rows) {
    const groups = new Map();
    rows.filter(row => row.purpose === "production").forEach(row => {
      const id = row.production_version_id || UNRECORDED;
      if (!groups.has(id)) groups.set(id, []);
      groups.get(id).push(row);
    });
    return [...groups].map(([id, feedback]) => ({ id, count: feedback.length, analysisCount: new Set(feedback.map(row => row.analysis_id).filter(Boolean)).size }));
  }

  W.feedbackStats = { normalize, filter, summarize, categoryCounts, versionCounts, recordDate, validDate };
})();
