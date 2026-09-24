// Writes dist/_redirects so Netlify proxies /api/* to the backend's
// public URL (its Tailscale Funnel address), taken from the
// VITE_BACKEND_URL Netlify env var that scripts/watchdog.ps1 (repo root)
// maintains, so the URL isn't hardcoded in netlify.toml. Netlify
// evaluates _redirects before netlify.toml's rules, so the SPA fallback
// in netlify.toml still applies to everything else.
import { writeFileSync } from "node:fs";

const backend = (process.env.VITE_BACKEND_URL || "").replace(/\/+$/, "");

if (!backend) {
  if (process.env.NETLIFY) {
    console.error("VITE_BACKEND_URL is not set - /api would fall through to index.html");
    process.exit(1);
  }
  console.log("VITE_BACKEND_URL not set, skipping _redirects (local build)");
  process.exit(0);
}

writeFileSync("dist/_redirects", `/api/*  ${backend}/api/:splat  200!\n`);
console.log(`dist/_redirects -> ${backend}`);
