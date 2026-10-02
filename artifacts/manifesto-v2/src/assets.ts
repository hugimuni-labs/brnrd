import {staticFile} from 'remotion';
// shell logos (acceptable per brief); no brnrd logo anywhere — brnrd is only ever type.
export const IMG: Record<string, HTMLImageElement> = {};
const load = (n: string) => new Promise<HTMLImageElement>((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = staticFile(n); });
export const IMGS_READY = Promise.all(['codex', 'clawd'].map(async (n) => { IMG[n] = await load(n + '.png'); }));
