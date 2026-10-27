// A loopback loom made of recorded parts: GET /v1/loom/{index,thread/<id>,beads} from a directory of
// <name>.json files (a thread's slash is `__`), /v1/loom/events as an SSE stream that names the index hash,
// POST /v1/loom/letters kept in memory. `node e2e/parts-server.mjs <dir> <port>`.
import { createServer } from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

export function partsServer(dir, port = 0) {
	const sent = [];
	const read = (name) => {
		const f = join(dir, name.replaceAll('/', '__') + '.json');
		return existsSync(f) ? readFileSync(f) : null;
	};
	const server = createServer((req, res) => {
		const url = new URL(req.url, 'http://x');
		const path = url.pathname.replace(/^\/v1\/loom\//, '');
		if (req.method === 'POST' && path === 'letters') {
			let body = '';
			req.on('data', (c) => (body += c));
			req.on('end', () => {
				const data = JSON.parse(body || '{}');
				sent.push(data);
				res.writeHead(200, { 'content-type': 'application/json' });
				res.end(JSON.stringify({ id: `letter:test:${sent.length}`, thread: 'main' }));
			});
			return;
		}
		if (path === 'events') {
			res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store' });
			const index = JSON.parse(read('index') || '{}');
			res.write(`event: index\ndata: ${index.parts?.index ?? 'fixture'}\n\n`);
			const t = setInterval(() => res.write(': keepalive\n\n'), 15000);
			req.on('close', () => clearInterval(t));
			return;
		}
		const raw = path === 'index' || path === 'beads' || path.startsWith('thread/') ? read(path) : null;
		if (!raw) {
			res.writeHead(404, { 'content-type': 'application/json' });
			res.end(JSON.stringify({ error: `no part ${path}` }));
			return;
		}
		res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-cache' });
		res.end(raw);
	});
	return new Promise((ok) => server.listen(port, '127.0.0.1', () => ok({ server, sent, port: server.address().port })));
}

if (import.meta.url === `file://${process.argv[1]}`) {
	const { port } = await partsServer(process.argv[2], Number(process.argv[3] || 0));
	console.log(`parts on http://127.0.0.1:${port}`);
}
