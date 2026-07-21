import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { extname, join, relative, sep } from 'node:path';

const root = new URL('../dist/', import.meta.url);
const site = new URL('https://hugimuni-labs.github.io/brnrd/');

function walk(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? walk(path) : [path];
  });
}

function routeFor(file) {
  const path = relative(root.pathname, file).split(sep).join('/');
  if (path === 'index.html') return '/brnrd/';
  if (path.endsWith('/index.html')) return `/brnrd/${path.slice(0, -10)}`;
  return `/brnrd/${path}`;
}

function targetExists(pathname) {
  const relativePath = decodeURIComponent(pathname.slice('/brnrd/'.length));
  const target = join(root.pathname, relativePath);
  if (existsSync(target)) return true;
  if (!extname(target) && existsSync(join(target, 'index.html'))) return true;
  if (!extname(target) && existsSync(`${target}.html`)) return true;
  return false;
}

const failures = [];
const htmlFiles = walk(root.pathname).filter((file) => file.endsWith('.html'));

for (const file of htmlFiles) {
  const sourceRoute = routeFor(file);
  const html = readFileSync(file, 'utf8');
  const hrefs = [...html.matchAll(/<a\b[^>]*\bhref=["']([^"']+)["']/gi)].map(
    (match) => match[1].replaceAll('&amp;', '&'),
  );

  for (const href of hrefs) {
    if (href.startsWith('#')) continue;
    const target = new URL(href, new URL(sourceRoute, site));
    if (target.origin !== site.origin || !target.pathname.startsWith('/brnrd/')) continue;
    if (!targetExists(target.pathname)) failures.push(`${sourceRoute} → ${href}`);
  }
}

if (failures.length) {
  console.error(`Broken internal links:\n${failures.map((item) => `- ${item}`).join('\n')}`);
  process.exitCode = 1;
} else {
  console.log(`Internal links: ${htmlFiles.length} generated pages checked`);
}
