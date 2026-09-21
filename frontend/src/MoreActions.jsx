import { ConsolePopover } from "./ConsoleShell.jsx";
import { Icon } from "./Icon.jsx";
import { useConsolePreferences } from "./consolePreferences.jsx";

// Keep dialogs outside the popover so closing it never destroys an open editor.
export default function MoreActions({ children, label, disabled = false }) {
  const { locale } = useConsolePreferences();
  return <ConsolePopover label={label || (locale === "en" ? "More actions" : "더보기")} trigger={<Icon name="more" size={18} />} className="more-actions" disabled={disabled}>
    {close => <div onClick={event => { if (event.target.closest("button:not(:disabled)")) close(); }}>{children}</div>}
  </ConsolePopover>;
}
