import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./ux.css";

// Help is available by pointer, keyboard and touch. Actions belong in a dialog,
// not in a tooltip: the tooltip itself deliberately has no focusable children.
export default function HelpTooltip({ label, children }) {
  const id = useId(); const [open, setOpen] = useState(false); const [position, setPosition] = useState(null);
  const trigger = useRef(null); const tooltip = useRef(null); const closing = useRef(null);
  const show = () => { clearTimeout(closing.current); setOpen(true); };
  const hide = () => { clearTimeout(closing.current); setOpen(false); setPosition(null); };
  const leave = () => { clearTimeout(closing.current); closing.current = setTimeout(() => { if (document.activeElement !== trigger.current) hide(); }, 140); };
  useEffect(() => () => clearTimeout(closing.current), []);
  useLayoutEffect(() => {
    if (!open) return undefined;
    const place = () => {
      if (!trigger.current || !tooltip.current) return;
      const anchor = trigger.current.getBoundingClientRect(); const box = tooltip.current.getBoundingClientRect();
      const left = Math.max(8, Math.min(anchor.left + anchor.width / 2 - box.width / 2, window.innerWidth - box.width - 8));
      const below = anchor.bottom + 8;
      const top = below + box.height <= window.innerHeight - 8 ? below : anchor.top - box.height - 8;
      setPosition({ left, top: Math.max(8, Math.min(top, window.innerHeight - box.height - 8)) });
    };
    const dismiss = event => { if (event.key === "Escape") event.preventDefault(); if (event.key === "Escape" || (event.type === "pointerdown" && !trigger.current?.contains(event.target) && !tooltip.current?.contains(event.target))) hide(); };
    place(); window.addEventListener("resize", place); window.addEventListener("scroll", place, true); document.addEventListener("keydown", dismiss); document.addEventListener("pointerdown", dismiss);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); document.removeEventListener("keydown", dismiss); document.removeEventListener("pointerdown", dismiss); };
  }, [open]);
  return <span className="metric-help" onMouseEnter={show} onMouseLeave={leave}><button ref={trigger} type="button" className="metric-help-trigger" aria-label={`${label} 설명`} aria-describedby={open ? id : undefined} onFocus={show} onBlur={hide} onClick={show} onKeyDown={event => { if (event.key === "Escape" && open) { event.preventDefault(); hide(); event.stopPropagation(); } }}>?</button>{open && createPortal(<span ref={tooltip} className="metric-help-tooltip" style={{ ...(position || {}), visibility: position ? "visible" : "hidden" }} id={id} role="tooltip" onMouseEnter={show} onMouseLeave={leave}>{children}</span>, trigger.current?.closest("dialog[open]") || document.body)}</span>;
}
