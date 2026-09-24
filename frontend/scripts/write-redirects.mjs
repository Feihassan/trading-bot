// Writes dist/_redirects so Netlify proxies /api/* to the backend's
// current public URL. That URL is a Cloudflare quick tunnel, which changes
// on every restart. scripts/watchdog.ps1 (repo root) keeps the
// VITE_BACKEND_URL Netlify env var up to date and triggers a rebuild,
// which is why the URL can't be hardcoded in netlify.toml. Netlify
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
