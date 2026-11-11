// Keep the previous command working while checking the current contract:
// analysis screens use only API evidence, never a second full-source mapping.
if (process.env.WAF_RELATIONS_SMOKE_QUICK === "1") process.env.WAF_DISPLAY_SMOKE_QUICK = "1";
await import("./evidence-display-smoke.mjs");
