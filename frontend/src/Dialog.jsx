import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import "./ux.css";

// Native modal supplies focus trapping, Escape and background inertness. Keep
// this component mounted to retain form drafts when closing and reopening it.
export default function Dialog({ open, title, onClose, children, className = "" }) {
  const ref = useRef(null); const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  if (typeof document === "undefined") return null;
  return createPortal(<dialog ref={ref} className={`ux-dialog ${className}`} aria-labelledby={titleId} onCancel={event => { event.preventDefault(); event.stopPropagation(); onClose(); }}>
    <div className="ux-dialog-head"><h2 id={titleId}>{title}</h2><button type="button" className="secondary" aria-label={`${title} 닫기`} onClick={onClose}>닫기</button></div>
    <div className="ux-dialog-body">{children}</div>
  </dialog>, document.body);
}
