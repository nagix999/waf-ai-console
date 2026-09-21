import { useConsolePreferences } from "./consolePreferences.jsx";

// Shared, in-memory registry filters. No search text is persisted in the URL.
export default function RegistryFilters({ search, onSearch, placeholder, filters = [], summary }) {
  const { locale } = useConsolePreferences();
  return <div className="registry-filters">
    <label className="registry-search">{locale === "en" ? "Search" : "검색"}<input type="search" value={search} maxLength={200} placeholder={placeholder} onChange={event => onSearch(event.target.value)} /></label>
    {filters.map(filter => <label key={filter.label}>{filter.label}<select value={filter.value} onChange={event => filter.onChange(event.target.value)}>{filter.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>)}
    {summary && <span className="ux-muted" aria-live="polite">{summary}</span>}
  </div>;
}
