import { Children, cloneElement, Fragment, isValidElement } from "react";
import { ConsolePopover } from "./ConsoleShell.jsx";
import { Icon } from "./Icon.jsx";
import { useConsolePreferences } from "./consolePreferences.jsx";

function closingActions(children, close) {
  return Children.map(children, child => {
    if (!isValidElement(child)) return child;
    if (child.type === Fragment) return cloneElement(child, {}, closingActions(child.props.children, close));
    if (child.type !== "button") return child;
    return cloneElement(child, { onClick: event => {
      if (child.props.disabled) return;
      child.props.onClick?.(event);
      close();
    } });
  });
}

// Keep dialogs outside the popover so closing it never destroys an open editor.
export default function MoreActions({ children, label, disabled = false }) {
  const { locale } = useConsolePreferences();
  return <ConsolePopover label={label || (locale === "en" ? "More actions" : "더보기")} trigger={<Icon name="more" size={18} />} className="more-actions" disabled={disabled}>
    {close => <div>{closingActions(children, close)}</div>}
  </ConsolePopover>;
}
