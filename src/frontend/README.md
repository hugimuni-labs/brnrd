# brnrd dashboard — next (frontend scaffold)

SvelteKit + Tailwind, picked 2026-07-05 to replace the zero-JS Jinja
dashboard (`src/brnrd_web/`) — see `kb/design-dashboard-live-surface.md`
(main repo) for why and `kb/log.md` §2026-07-06 for the decision record.

**Integration model:** static SPA, not a Node server of its own.
`vite.config.ts` uses `adapter-static` with `fallback: 'index.html'` and
project-wide `ssr = false` (`src/routes/+layout.ts`) — `npm run build`
writes plain HTML/JS/CSS to `build/`, which the existing FastAPI app
(`src/brnrd_web/`) will mount as static assets. Auth stays FastAPI's
session cookie; this app fetches the same JSON endpoints
(`dashboard_stats`/`_quota_views` etc.) client-side rather than
duplicating auth or data access here.

**Status:** scaffold only, not wired into `brnrd_web` yet and not linked
from the live dashboard nav. The first real screen (the window-track
live-quota view, dual time+%-remaining axis) lands as its own reviewable
PR on top of this.

---

Everything below is the unmodified `sv create` scaffold boilerplate.

# sv

Everything you need to build a Svelte project, powered by [`sv`](https://github.com/sveltejs/cli).

## Creating a project

If you're seeing this, you've probably already done this step. Congrats!

```sh
# create a new project
npx sv create my-app
```

To recreate this project with the same configuration:

```sh
# recreate this project
npx sv@0.16.2 create --template minimal --types ts --add tailwindcss="plugins:none" prettier eslint --install npm frontend
```

## Developing

Once you've created a project and installed dependencies with `npm install` (or `pnpm install` or `yarn`), start a development server:

```sh
npm run dev

# or start the server and open the app in a new browser tab
npm run dev -- --open
```

## Building

To create a production version of your app:

```sh
npm run build
```

You can preview the production build with `npm run preview`.

> To deploy your app, you may need to install an [adapter](https://svelte.dev/docs/kit/adapters) for your target environment.
