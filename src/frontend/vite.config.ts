import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// Static SPA build: the FastAPI backend (src/brnrd/routers/) stays the JSON
			// API + session-cookie auth boundary; this build's output is mounted as
			// static assets behind it, not served by its own Node process. See
			// frontend/README.md for the integration plan.
			//
			// Mounted at domain root ("/", see .upsun/config.yaml) — briefly
			// previewed under "/app/" first (2026-07-06), which needed an
			// explicit `paths.base` override since every emitted asset URL
			// is absolute; root needs no override (default base is '').
			adapter: adapter({
				pages: 'build',
				assets: 'build',
				fallback: 'index.html',
				precompress: false,
				strict: true
			})
		})
	],
	server: {
		// Dev-only: `npm run dev` serves this app on its own port, so JSON
		// fetches to the FastAPI backend (`/v1/dashboard/quota` etc.) need a
		// proxy to a locally-running `brnrd` instance.
		//
		// This list used to claim it mirrored `.upsun/config.yaml`'s passthru
		// rule; it had already drifted from it (no `logout`, no
		// `terms/accept`) and nothing noticed, because nothing checked. There
		// is no production copy to mirror any more (#847) — the boundary is
		// derived from the route table in `src/brnrd/spa.py`, and
		// `tests/test_spa_serving.py` asserts this pattern reaches every
		// namespace the backend declares and steals none of the SPA's own.
		// Adding a backend prefix without adding it here now fails the suite.
		proxy: {
			'^/(api|auth|healthz|logout|r|static|v1)(/|$)': {
				target: process.env.BRNRD_DEV_TARGET ?? 'http://localhost:8000',
				changeOrigin: true
			}
		}
	}
});
