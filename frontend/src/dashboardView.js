export function dashboardAnalysisQuery(summary, serviceApiKeyId = "") {
  if (!summary?.window?.created_from || !summary?.window?.created_to) return null;
  return { analysis_purpose: "production", limit: 10, offset: 0, created_from: summary.window.created_from, created_to: summary.window.created_to, ...(serviceApiKeyId ? { service_api_key_id: serviceApiKeyId } : {}) };
}

// Missing/undefined metrics split the line. They are not zero-percent results.
export function metricSegments(trend, metric, width = 600, height = 160) {
  const segments = []; let points = [];
  for (let index = 0; index < trend.length; index += 1) {
    const value = trend[index].evaluation_summary?.metrics?.[metric];
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) {
      if (points.length) segments.push(points); points = []; continue;
    }
    points.push({ x: 36 + (trend.length > 1 ? index / (trend.length - 1) : 0.5) * (width - 50), y: 10 + (1 - value) * (height - 30), value, date: trend[index].date });
  }
  if (points.length) segments.push(points);
  return segments;
}
