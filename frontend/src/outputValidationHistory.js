import { parseJsonRecord } from "./jsonInspection.js";

const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const boundedText = (value, limit) => typeof value === "string" && value.length > 0 && value.length <= limit ? value : null;
const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
const commonFields = ["evidence_id", "source_id", "supports", "interpretation_ko"];
const versions = new Set(["rejected-evidence-v1", "rejected-evidence-v2"]);

// Display only the explicit encrypted step-output diagnostic. Never infer
// rejected evidence from a successful output, metadata or an earlier attempt.
export function outputValidationHistory(output) {
  const document = typeof output === "string" ? parseJsonRecord(output)?.value : output;
  if (!record(document) || !Array.isArray(document.output_validation_history)) return [];
  return document.output_validation_history.slice(0, 12).filter(item => record(item) && (item.version === undefined || versions.has(item.version))).map(item => {
    const contributionLinks = item.version === "rejected-evidence-v2";
    const linkField = contributionLinks ? "contributes_to_evidence_ids" : "related_evidence_ids";
    const fields = new Set([...commonFields, linkField]);
    const source = Array.isArray(item.evidence) ? item.evidence : [];
    let limited = item.truncated === true || count(item.omitted_evidence_count) > 0 || source.length > 10 || document.output_validation_history.length > 12;
    const evidence = source.slice(0, 10).filter(record).map(value => {
      const interpretation = typeof value.interpretation_ko === "string" ? value.interpretation_ko : null;
      const links = Array.isArray(value[linkField])
        ? value[linkField].slice(0, 10).map(key => boundedText(key, 64)).filter(key => key !== null) : null;
      const interpretationTruncated = value.interpretation_truncated === true;
      const interpretationDisplayTruncated = (interpretation?.length || 0) > 4096;
      const linksOmitted = value.links_omitted === true || (Array.isArray(value[linkField]) && (value[linkField].length > 10 || links.length !== value[linkField].length));
      const omittedFields = Array.isArray(value.omitted_fields) ? value.omitted_fields.filter(field => fields.has(field)) : [];
      limited ||= interpretationTruncated || interpretationDisplayTruncated || linksOmitted || omittedFields.length > 0;
      return {
        position: count(value.position),
        evidenceId: boundedText(value.evidence_id, 64),
        sourceId: boundedText(value.source_id, 64),
        supports: boundedText(value.supports, 64),
        interpretation: interpretation === null ? null : interpretation.slice(0, 4096),
        relatedIds: contributionLinks ? null : links,
        contributesToIds: contributionLinks ? links : null,
        linkedIds: links,
        interpretationTruncated,
        interpretationDisplayTruncated,
        linksOmitted,
        omittedFields,
      };
    });
    limited ||= evidence.length !== Math.min(source.length, 10);
    return {
      version: contributionLinks ? "rejected-evidence-v2" : "rejected-evidence-v1",
      attempt: count(item.attempt) > 0 && item.attempt <= 4 ? item.attempt : null,
      groundingAttempt: [1, 2, 3].includes(item.grounding_attempt) ? item.grounding_attempt : null,
      issues: Array.isArray(item.validation_issues) ? item.validation_issues.slice(0, 16).filter(record).map(issue => ({ field: boundedText(issue.field, 160), type: boundedText(issue.type, 96) })).filter(issue => issue.field || issue.type) : [],
      evidence,
      limited,
      omittedEvidenceCount: count(item.omitted_evidence_count),
    };
  });
}
