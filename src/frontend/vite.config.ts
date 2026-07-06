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

			// Static SPA build: the FastAPI backend (src/brnrd_web/) stays the JSON
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
	]
});
