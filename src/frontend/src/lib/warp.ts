import {
	parseBackchannelPage,
	type AuthoredBackchannelItem,
	type WarpHeat
} from './backchannelPage.ts';
import type { SurfaceFile } from './surface.ts';

// The warp (design-work-layers.md, taken 2026-08-01): the standing intent
// surface — account-global work layers whose items ripen into runs. A layer
// is one authored markdown file under `surface/layers/`, call-signed by its
// basename, opening with the work stream's definition and carrying items in
// the backchannel grammar extended with `state:` (heat) and `needs:` rows.
//
// This module only *reads* that convention: layers are authored, never
// derived (decision-drop-streams.md is the grave this rule keeps closed) —
// no code names a layer, so everything here is discovery over the files the
// surface already ships.

const LAYERS_PREFIX = 'surface/layers/';

export interface WarpLayer {
	/** The call sign: the file's basename without extension (`the-loom`). */
	callSign: string;
	/** Corpus path, for the corpus browser link. */
	path: string;
	/** Everything above the first `## ` heading, minus the `# ` title line —
	 *  the work stream's definition, rendered as markdown. */
	definitionMarkdown: string;
	items: AuthoredBackchannelItem[];
	/** Item counts by heat. `unstated` counts items with no usable `state:`
	 *  row — semantically cold (cold = undefined), but kept distinct so the
	 *  renderer can show never-stated as such rather than claiming the
	 *  author chose cold. */
	counts: { ember: number; banked: number; cold: number; unstated: number };
}

/** True for a corpus file that is a warp layer page. The index page of the
 * directory (if one ever exists) is not a layer. */
export function isLayerFile(path: string): boolean {
	if (!path.startsWith(LAYERS_PREFIX)) return false;
	const rest = path.slice(LAYERS_PREFIX.length);
	return rest.endsWith('.md') && !rest.includes('/') && rest !== 'index.md';
}

export function layerCallSign(path: string): string {
	const base = path.slice(path.lastIndexOf('/') + 1);
	return base.endsWith('.md') ? base.slice(0, -3) : base;
}

/** The definition block: page content above the first `## ` heading, with
 * the `# ` title line dropped (the call sign already names the layer; the
 * title would render twice). */
export function layerDefinition(markdown: string): string {
	const normalized = (markdown ?? '').replace(/\r\n/g, '\n');
	const lines = normalized.split('\n');
	const head: string[] = [];
	for (const line of lines) {
		if (/^##[ \t]/.test(line)) break;
		head.push(line);
	}
	while (head.length && /^#[ \t]/.test(head[0].trim())) head.shift();
	while (head.length && head[0].trim() === '') head.shift();
	while (head.length && head[head.length - 1].trim() === '') head.pop();
	return head.join('\n');
}

function heatCounts(items: AuthoredBackchannelItem[]): WarpLayer['counts'] {
	const counts = { ember: 0, banked: 0, cold: 0, unstated: 0 };
	for (const item of items) {
		if (item.state === null) counts.unstated += 1;
		else counts[item.state] += 1;
	}
	return counts;
}

/** Discover and parse every layer page in a surface file set. Order is the
 * files' own order in the surface response (the daemon ships them sorted by
 * path), so the stack is alphabetical by call sign — deterministic without
 * inventing a priority field the design doesn't have. */
export function buildWarpLayers(files: SurfaceFile[]): WarpLayer[] {
	return files
		.filter((f) => isLayerFile(f.path))
		.map((f) => {
			const items = parseBackchannelPage(f.markdown);
			return {
				callSign: layerCallSign(f.path),
				path: f.path,
				definitionMarkdown: layerDefinition(f.markdown),
				items,
				counts: heatCounts(items)
			};
		});
}

/** Total ember count across the warp — the queued draw the control room's
 * capacity strip can price (design-work-layers.md §Foundation). */
export function emberCount(layers: WarpLayer[]): number {
	return layers.reduce((sum, layer) => sum + layer.counts.ember, 0);
}

// ── multi-repo: repos are derived, never declared ──────────────────────────
//
// The multi-repo ground state (design-work-layers.md §Open forks 2): a layer
// is account-global by construction, an item names its repo(s) in `refs:`,
// and a repo view is a heddle, never a directory. So there is no `repo:` row
// to parse — an item's repo set is a *structural property* of the refs it
// already carries: the qualified forge shorthand (`owner/repo#N`) and
// explicit forge hrefs both name one. An item whose refs name no repo
// belongs to every view (surface work, cross-cutting decisions) rather than
// to a guessed one.

/** Forge object hrefs that name a repo: issues, PRs, commits, trees, blobs.
 * Conservative on purpose — `github.com/orgs/...` and friends are not repo
 * coordinates and must not parse as one. */
const FORGE_HREF_RE =
	/^https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/(?:issues|pull|commit|tree|blob)\//;

const FORGE_LABEL_RE = /^([\w.-]+\/[\w.-]+)#\d+$/;

/** The repos an item's refs name, deduplicated, in first-mention order. */
export function itemRepos(item: AuthoredBackchannelItem): string[] {
	const repos: string[] = [];
	const add = (repo: string) => {
		if (!repos.includes(repo)) repos.push(repo);
	};
	for (const ref of item.refs) {
		const label = FORGE_LABEL_RE.exec(ref.label.trim());
		if (label) add(label[1]);
		if (ref.href) {
			const href = FORGE_HREF_RE.exec(ref.href);
			if (href) add(href[1]);
		}
	}
	return repos;
}

/** Every repo the warp's items touch — the option set a repo heddle offers. */
export function warpRepos(layers: WarpLayer[]): string[] {
	const repos: string[] = [];
	for (const layer of layers) {
		for (const item of layer.items) {
			for (const repo of itemRepos(item)) {
				if (!repos.includes(repo)) repos.push(repo);
			}
		}
	}
	return repos;
}

export type { WarpHeat };
