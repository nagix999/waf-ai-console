import { useEffect, useId, useRef } from "react";

// Manual activation: arrow keys move focus, Enter/Space opens the tab. Merely
// focusing Input must not fetch sensitive data or create an audit event.
export default function DetailTabs({ label, items, value, onChange, children, focusRequest }) {
  const id = useId(); const tabs = useRef(null);
  useEffect(() => {
    if (focusRequest) tabs.current?.querySelector('[role="tab"][aria-selected="true"]')?.focus({ preventScroll: true });
  }, [focusRequest]);
  function navigate(event) {
    const buttons = [...event.currentTarget.querySelectorAll('[role="tab"]')];
    const index = buttons.indexOf(document.activeElement);
    if (index < 0) return;
    const next = event.key === "ArrowRight" ? (index + 1) % buttons.length
      : event.key === "ArrowLeft" ? (index + buttons.length - 1) % buttons.length
        : event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : null;
    if (next !== null) { event.preventDefault(); buttons[next].focus(); }
  }
  return <>
    <div ref={tabs} className="tabs detail-tabs" role="tablist" aria-label={label} onKeyDown={navigate}>
      {items.map(([key, title]) => <button type="button" key={key} id={`${id}-${key}`} role="tab" aria-selected={value === key} aria-controls={`${id}-${key}-panel`} tabIndex={value === key ? 0 : -1} onClick={() => onChange(key)}>{title}</button>)}
    </div>
    {items.map(([key]) => <div key={key} id={`${id}-${key}-panel`} role="tabpanel" aria-labelledby={`${id}-${key}`} hidden={value !== key} tabIndex={0}>{children(key)}</div>)}
  </>;
}
