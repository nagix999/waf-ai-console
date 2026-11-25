import { useEffect } from "react";

export default function useWorkspaceHeight(reference, active = true) {
  useEffect(() => {
    const element = reference.current;
    if (!element || !active) return;
    // Focus and browser Back may restore a nonzero page scroll. Size from the
    // stable document position so that restoring it cannot enlarge the page.
    const update = () => element.style.setProperty("--workspace-height", `${Math.max(320, window.innerHeight - (element.getBoundingClientRect().top + window.scrollY) - 24)}px`);
    update(); window.addEventListener("resize", update);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(element.parentElement);
    return () => { window.removeEventListener("resize", update); observer?.disconnect(); };
  }, [reference, active]);
}
