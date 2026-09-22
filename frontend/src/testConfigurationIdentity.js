// Identity only; a difference is not evidence that a change caused a score.
export function configurationChanges(snapshot, current) {
  if (!snapshot || !current) return null;
  return ["primary", "verifier", "evidence_editor", "prompt", "input_schema"].filter(key => JSON.stringify(snapshot[key]) !== JSON.stringify(current[key]));
}
export function comparisonContext(run, current) {
  if (!run.ground_truth?.published) return "not_official";
  if (!run.ground_truth?.comparison_key || !current?.comparison_key) return "no_baseline";
  return run.ground_truth.comparison_key === current.comparison_key ? "same" : "different";
}
