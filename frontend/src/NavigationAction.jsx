import "./navigationAction.css";

// Navigation is quieter than a mutation. Preserve native link/button semantics.
export default function NavigationAction({ href, children, className = "", ...props }) {
  const Tag = href ? "a" : "button";
  return <Tag {...(href ? { href } : { type: "button" })} {...props} className={`navigation-action ${className}`}>
    <span>{children}</span><span className="navigation-action-arrow">→</span>
  </Tag>;
}
