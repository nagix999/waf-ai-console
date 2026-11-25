import { Icon } from "./Icon.jsx";
import { useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useConsolePreferences } from "./consolePreferences.jsx";
import "./settingsWorkspaceDesign.css";

// Page-family building blocks. Counts and state are always supplied by live
// controllers; the design preview's example data never enters these components.
export function SettingsPageActions({ children }) {
  const [host, setHost] = useState(null);
  useLayoutEffect(() => { setHost(document.getElementById("console-page-actions")); }, []);
  if (!children) return null;
  const actions = <div className="settings-page-actions">{children}</div>;
  return host ? createPortal(actions, host) : actions;
}

export function SettingsSectionHeader({ title, actions, children, className = "" }) {
  // The shell already owns the page title and introduction. This component only
  // places actions beside it and renders optional, compact contextual metadata.
  return <><SettingsPageActions>{actions}</SettingsPageActions>{children && <div className={`settings-page-context ${className}`} aria-label={title}>{children}</div>}</>;
}

export function SettingsSummary({ label, items, className = "" }) {
  return <dl className={`settings-summary ${className}`} aria-label={label}>{items.map(({ key, label: name, value, note, tone = "accent", icon = "chart" }) => <div key={key || name} className={`settings-summary-item settings-summary-${tone}`}><dt>{name}<span className="settings-summary-icon"><Icon name={icon} size={17} /></span></dt><dd>{value ?? "—"}</dd>{note && <small>{note}</small>}</div>)}</dl>;
}

export function SettingsWorkflow({ className = "" }) {
  const { locale } = useConsolePreferences();
  const labels = locale === "en" ? ["Official Test", "Production Review", "Apply to Production"] : ["공식 테스트", "운영 반영 검토", "운영에 반영"];
  return <ol className={`settings-workflow ${className}`} aria-label={locale === "en" ? "Production change workflow" : "운영 변경 절차"}>{labels.map((text, index) => <li key={text}><span aria-hidden="true">{index + 1}</span><strong>{text}</strong></li>)}</ol>;
}
