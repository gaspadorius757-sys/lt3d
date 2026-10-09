// Static host (gzip-aware, cached) + accounts + authoritative-ish multiplayer.
import { execSync } from 'node:child_process'; import http from 'node:http'; import fs from 'node:fs'; import path from 'node:path'; import crypto from 'node:crypto';
import { DatabaseSync } from 'node:sqlite'; import { WebSocketServer } from 'ws';
const ROOT = path.resolve(import.meta.dirname, '..'), PORT = process.env.PORT || 8080;
const DEV = !!process.env.DEV, CAP = DEV ? 500 : 12;           // max server-accepted speed, m/s
const DATA = process.env.DATA_DIR || path.join(ROOT, 'data'), DB = process.env.DB_PATH || path.join(ROOT, 'game.db');
// First boot (or DATA_TAG bump): pull the prebuilt country tiles from the GitHub release made by the Actions workflow.
if (process.env.DATA_URL) {
  const tag = process.env.DATA_TAG || 'latest', mark = path.join(DATA, '.tag');
  if (!fs.existsSync(path.join(DATA, 'index.json')) || fs.readFileSync(mark, 'utf8').trim() !== tag) {
    console.log('Downloading country data…'); fs.rmSync(DATA, { recursive: true, force: true }); fs.mkdirSync(DATA, { recursive: true });
    execSync(`curl -fsSL "${process.env.DATA_URL}" | tar xz -C "${DATA}"`, { stdio: 'inherit', shell: '/bin/bash' });
    fs.writeFileSync(mark, tag);
  }
}
const db = new DatabaseSync(DB);
db.exec('create table if not exists users(id integer primary key,name text,token text unique,x real,y real,h real default 0,xp integer default 0)');
let bounds = null; try { bounds = JSON.parse(fs.readFileSync(path.join(DATA, 'index.json'))).bounds; } catch {}
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.css': 'text/css' };
const players = new Map();

const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  if (req.method === 'POST' && u.pathname === '/api/register') {
    let b = ''; req.on('data', c => { b += c; if (b.length > 1024) req.destroy(); });
    req.on('end', () => { // DEV-GRADE auth: anonymous token. Replace with real login before launch.
      let name = 'player'; try { name = String(JSON.parse(b).name || name).slice(0, 24); } catch {}
      const token = crypto.randomBytes(24).toString('hex');
      db.prepare('insert into users(name,token) values(?,?)').run(name, token);
      res.setHeader('content-type', 'application/json'); res.end(JSON.stringify({ token }));
    }); return;
  }
  if (u.pathname === '/health') return res.end('ok');
  const p = u.pathname === '/' ? '/client/index.html' : u.pathname;
  const isData = p.startsWith('/data/'), base = isData ? DATA : path.join(ROOT, 'client'), rel = isData ? p.slice(6) : p.replace(/^\/client\//, '');
  const f = path.normalize(path.join(base, rel));
  if (!f.startsWith(base)) { res.writeHead(403); return res.end(); }
  fs.stat(f, (e, st) => {
    if (e || !st.isFile()) { res.writeHead(404); return res.end('not found'); }
    const gz = f.endsWith('.gz'), ext = path.extname(gz ? f.slice(0, -3) : f);
    const h = { 'content-type': MIME[ext] || 'application/octet-stream', 'content-length': st.size,
      'cache-control': p.startsWith('/data/tiles/') ? 'public,max-age=31536000,immutable' : 'no-cache' }; // tiles versioned via ?v=
    if (gz) h['content-encoding'] = 'gzip';
    res.writeHead(200, h); fs.createReadStream(f).pipe(res);
  });
});

const wss = new WebSocketServer({ server, path: '/ws' });
const save = P => db.prepare('update users set x=?,y=?,h=? where id=?').run(P.X, P.Y, P.h, P.id);
wss.on('connection', (ws, req) => {
  const tok = new URL(req.url, 'http://x').searchParams.get('token');
  const u = db.prepare('select * from users where token=?').get(tok); if (!u) return ws.close();
  const P = { id: u.id, X: u.x, Y: u.y, h: u.h || 0, ry: 0, t: Date.now(), ws };
  players.set(u.id, P); ws.send(JSON.stringify({ t: 'welcome', id: u.id, X: u.x, Y: u.y }));
  ws.on('message', m => {
    let d; try { d = JSON.parse(m); } catch { return; }
    if (d.t !== 'pos' || ![d.X, d.Y, d.h].every(Number.isFinite)) return;
    const out = bounds && (d.X < bounds[0] || d.X > bounds[2] || d.Y < bounds[1] || d.Y > bounds[3]);
    const now = Date.now(), dt = Math.max((now - P.t) / 1000, .05);
    const bad = out || (P.X != null && Math.hypot(d.X - P.X, d.Y - P.Y) > CAP * dt + 3) || d.h < -5 || d.h > 5000;
    if (bad) return ws.send(JSON.stringify({ t: 'fix', X: P.X, Y: P.Y }));
    Object.assign(P, { X: d.X, Y: d.Y, h: d.h, ry: +d.ry || 0, t: now });
  });
  ws.on('close', () => { players.delete(P.id); if (P.X != null) save(P); });
});
setInterval(() => { // interest management: only players within 5 km (use a spatial hash past ~500 players)
  for (const A of players.values()) {
    if (A.X == null || A.ws.readyState !== 1) continue;
    const pl = []; for (const B of players.values()) if (B !== A && B.X != null && Math.hypot(A.X - B.X, A.Y - B.Y) < 5000) pl.push([B.id, B.X, B.Y, B.h, B.ry]);
    A.ws.send(JSON.stringify({ t: 'snap', pl }));
  }
}, 100);
setInterval(() => { for (const P of players.values()) if (P.X != null) save(P); }, 5000);
server.listen(PORT, () => console.log(`http://localhost:${PORT}${DEV ? ' (DEV: speed cap 500 m/s)' : ''}`));
