import { useEffect, useLayoutEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./Icon.jsx";
import Dialog from "./Dialog.jsx";
import { useConsolePreferences } from "./consolePreferences.jsx";
import { consoleGroups, consoleLocation, globalSearchFields } from "./consoleNavigation.js";
import packageInfo from "../package.json";

// A disclosure, not an ARIA menu: its native buttons retain normal Tab order.
export function ConsolePopover({ label, trigger, children, className = "", disabled = false }) {
  const [open, setOpen] = useState(false); const ref = useRef(null); const button = useRef(null); const content = useRef(null); const id = useId();
  const [position, setPosition] = useState({ left: 8, top: 8 });
  // A portal keeps row actions usable inside horizontally scrolling tables.
  // Within a modal it stays in that dialog, retaining native focus containment.
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = button.current?.getBoundingClientRect(), popup = content.current?.getBoundingClientRect();
      if (!anchor || !popup) return;
      setPosition({ left: Math.max(8, Math.min(anchor.right - popup.width, innerWidth - popup.width - 8)),
        top: Math.max(8, Math.min(anchor.bottom + 8 + popup.height > innerHeight ? anchor.top - popup.height - 8 : anchor.bottom + 8, innerHeight - popup.height - 8)) });
    };
    place(); window.addEventListener("resize", place); window.addEventListener("scroll", place, true);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    content.current?.querySelector("button:not(:disabled)")?.focus();
    const outside = event => { if (!ref.current?.contains(event.target) && !content.current?.contains(event.target)) setOpen(false); };
    const escape = event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); button.current?.focus(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("focusin", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("focusin", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  const close = () => { setOpen(false); button.current?.focus(); };
  return <div className={`console-popover ${className}`} ref={ref}>
    <button ref={button} className="console-control" type="button" disabled={disabled} aria-label={label} aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>{trigger}</button>
    {open && createPortal(<div ref={content} className={`console-popover-content console-floating-popover ${className ? `${className}-content` : ""}`} style={position} id={id} aria-label={label}>{children(close)}</div>, ref.current?.closest("dialog") || document.body)}
  </div>;
}

export function ConsoleAppearance() {
  const { locale, setLocale, theme, setTheme, t } = useConsolePreferences();
  return <div className="r3-appearance">
    <div className="console-choice-row console-locale" role="group" aria-label={t("language")}>{[["ko", "KR"], ["en", "EN"]].map(([value, label]) => <button type="button" key={value} aria-pressed={locale === value} onClick={() => setLocale(value)}>{label}</button>)}</div>
    <ConsolePopover label={t("theme")} trigger={<Icon name={theme === "dark" ? "moon" : "sun"} size={18} />}>{close => <div className="console-theme-choices">{[["light", "themeLight"], ["sk", "themeSk"], ["dark", "themeDark"]].map(([value, key]) => <button type="button" key={value} aria-pressed={theme === value} onClick={() => { setTheme(value); close(); }}><span className={`console-theme-swatch swatch-${value}`} />{t(key)}{theme === value && <Icon name="check" size={16} />}</button>)}</div>}</ConsolePopover>
  </div>;
}

function Navigation({ screen, onNavigate }) {
  const { t } = useConsolePreferences(); const { item, group: activeGroup } = consoleLocation(screen);
  return <nav className="console-navigation" aria-label={t("navigation")}>{consoleGroups.map(group =>
    <button type="button" key={group.key} className="v5-workspace-link" aria-current={activeGroup === group.key ? "page" : undefined} onClick={() => onNavigate(group.items.includes(item) ? item : group.items[0])}>
      <Icon name={group.icon} size={18} /><span>{t(group.key)}</span>
    </button>)}</nav>;
}

export default function ConsoleShell({ screen, principal, healthError, onNavigate, onSearch, logoutState, onLogout, children }) {
  const { t } = useConsolePreferences();
  const location = consoleLocation(screen);
  const [mobileOpen, setMobileOpen] = useState(false), [accountOpen, setAccountOpen] = useState(false);
  const [field, setField] = useState("event_id"), [query, setQuery] = useState(""), [searchError, setSearchError] = useState("");
  const searchKeys = { analysis_id: "analysisId", event_id: "eventId", src_ip: "sourceIp", company_name: "company", signature: "signature" };
  const username = principal?.username || "Admin";
  function navigate(key) { setMobileOpen(false); onNavigate(key); }
  function search(event) { event.preventDefault(); const error = onSearch(field, query); setSearchError(error || ""); }
  return <div className="app-shell console-shell">
    <a className="skip-link" href="#workspace-content" onClick={event => { event.preventDefault(); const target = document.getElementById("workspace-content"); target?.focus({ preventScroll: true }); target?.scrollIntoView({ block: "start" }); }}>{t("skip")}</a>
    <aside className="sidebar console-sidebar">
      <div className="console-brand"><span className="console-brand-mark"><Icon name="shield" size={23} /></span><strong>WAF AI Console</strong></div>
      <Navigation screen={screen} onNavigate={navigate} />
      <div className="console-sidebar-footer"><span className="console-footer-mark"><Icon name="shield" size={23} /></span><div><span>WAF AI Console</span><span>v{packageInfo.version}</span></div></div>
    </aside>
    <main className="workspace">
      <header className="app-header console-header">
        <button type="button" className="icon-button console-mobile-toggle" aria-label={t("openNav")} onClick={() => setMobileOpen(true)}><Icon name="menu" /></button>
        <form role="search" className="console-search" onSubmit={search}>
          <div className="console-search-fields"><select aria-label={t("searchField")} value={field} onChange={event => { setField(event.target.value); setSearchError(""); }}>{globalSearchFields.map(value => <option key={value} value={value}>{t(searchKeys[value])}</option>)}</select>
            <input aria-label={t("searchPlaceholder")} placeholder={t("searchPlaceholder")} value={query} maxLength={500} onChange={event => { setQuery(event.target.value); setSearchError(""); }} />
            <button type="submit" className="icon-button" aria-label={t("search")} disabled={!query.trim()}><Icon name="search" size={18} /></button></div>
          {searchError && <span className="console-search-error" role="alert">{t(searchError)}</span>}
        </form>
        <div className="header-actions console-header-actions">
          <button type="button" className={`console-health ${healthError ? "has-error" : ""}`} title={t("healthNote")} onClick={() => navigate("status")}><Icon name={healthError ? "alert" : "server"} size={15} /><span>{t(healthError ? "healthError" : "healthUnknown")}</span></button>
          <ConsoleAppearance />

          <ConsolePopover label={t("account")} trigger={<><span className="admin-avatar" aria-hidden="true">{Array.from(username)[0].toUpperCase()}</span><span className="console-admin-label">{username}</span><Icon name="chevronDown" size={14} /></>}>{close => <><button type="button" onClick={() => { close(); setAccountOpen(true); }}>{t("account")}</button><button type="button" disabled={logoutState.busy} onClick={() => { close(); onLogout(); }}><Icon name="logout" size={16} />{t(logoutState.busy ? "loggingOut" : "logout")}</button></>}</ConsolePopover>
        </div>
      </header>
      <div className="content console-content" id="workspace-content" tabIndex={-1}>
        <div className="console-page-heading"><p className="console-breadcrumb">WAF AI Console<span aria-hidden="true">/</span>{t(location.group || location.title)}</p><h1>{t(location.title)}</h1><p className="page-description">{t(`${location.title}.description`)}</p></div>
        {consoleGroups.find(group => group.key === location.group)?.items.length > 1 && <nav className="v5-workspace-tabs" aria-label={t(location.group)}>{consoleGroups.find(group => group.key === location.group).items.map(key => <button type="button" key={key} aria-current={location.item === key ? "page" : undefined} onClick={() => navigate(key)}>{t(key)}</button>)}</nav>}
        {children}
      </div>
    </main>
    <Dialog open={mobileOpen} title={t("navigation")} onClose={() => setMobileOpen(false)} className="console-mobile-dialog"><Navigation screen={screen} onNavigate={navigate} /></Dialog>
    <Dialog open={accountOpen} title={t("account")} onClose={() => setAccountOpen(false)} className="console-account-dialog"><p className="ux-muted">{t("accountDescription")}</p><p>{username}</p><span className="status">Admin</span></Dialog>
  </div>;
}
