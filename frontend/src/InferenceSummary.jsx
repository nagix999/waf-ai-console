import { formatDuration } from "./analysisView.js";
import { recordedProfiles } from "./inferenceDetail.js";
import { useConsolePreferences } from "./consolePreferences.jsx";

export default function InferenceSummary({ detail, runs, children, onMetadata }) {
  const { t } = useConsolePreferences();
  const profiles = recordedProfiles(detail, runs);
  const missing = t("detail.missing");
  const schema = detail.input_schema_metadata || detail.result?.agent?.input_schema;
  const cells = [
    [t("detail.purpose"), t(`detail.${detail.analysis_purpose || "legacy_unknown"}`)],
    [t("detail.status"), t(`detail.${detail.status}`)],
    ["Primary", profiles.primary || missing],
    ["Verifier", profiles.verifier || missing],
    [t("detail.editor"), profiles.editor || missing],
    [t("detail.prompt"), detail.prompt_version || missing],
    [t("schema"), schema?.version_number != null ? `v${schema.version_number}` : missing],
    [t("detail.queue"), formatDuration(detail.queue_wait_ms, t("unmeasured"))],
    [t("detail.processingTime"), formatDuration(detail.processing_duration_ms, t("unmeasured"))],
    [t("detail.total"), formatDuration(detail.total_elapsed_ms, t("unmeasured"))],
  ];
  return <div className="inference-overview">
    <section className="panel inference-execution">
      <div className="panel-head-inline"><h2>{t("detail.execution")}</h2><button type="button" className="text-button" onClick={onMetadata}>{t("detail.metadata")}</button></div>
      <dl className="inference-facts">{cells.map(([label, value], index) => <div key={label}><dt>{label}</dt><dd>{index === 1 ? <span className={`runtime-status-text outcome-${detail.status}`}><i />{value}</span> : value}</dd></div>)}</dl>
      <p className="ux-muted inference-record-note">{t("detail.recordNote")}</p>
    </section>
    <section className="panel inference-result-snapshot">{children}</section>
  </div>;
}
