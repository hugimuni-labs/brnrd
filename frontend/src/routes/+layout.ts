// Static SPA build (see vite.config.ts's adapter-static + fallback config):
// no server to render on, so SSR is off project-wide. The FastAPI backend
// (src/brnrd_web/) owns auth and serves this build's output as static
// assets; pages fetch JSON from its existing routes client-side.
export const ssr = false;
