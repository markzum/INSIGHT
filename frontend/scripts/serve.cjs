const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..', process.argv.includes('--dist') ? 'dist' : '.');
const mime = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json; charset=utf-8','.svg':'image/svg+xml'};
const server = http.createServer((req,res) => {
  try {
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    if (pathname === '/assets/config.js' && process.env.ALFA_API_BASE) {
      const payload = { apiBaseUrl: process.env.ALFA_API_BASE, requestTimeoutMs: Number(process.env.ALFA_TIMEOUT_MS || 180000) };
      res.writeHead(200, {'Content-Type': mime['.js'], 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'});
      res.end('window.ALFA_CONFIG = ' + JSON.stringify(payload) + ';'); return;
    }
    if (pathname === '/__local/status') {
      res.writeHead(200, {'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});
      res.end(JSON.stringify({app:'alfa-business-pulse',projectRoot:path.resolve(__dirname,'..'),pid:process.pid,mode:process.argv.includes('--dist')?'dist':'source'})); return;
    }
    if (pathname !== '/' && pathname !== '/index.html' && !pathname.startsWith('/assets/') && !pathname.startsWith('/data/')) {res.writeHead(404).end('Not found'); return;}
    const file = path.resolve(root, '.' + (pathname === '/' ? '/index.html' : pathname));
    if (file !== root && !file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    // Source preview also supports the generated file fallback from the canonical JSON.
    if (pathname === '/data/demo-data.js' && !fs.existsSync(file)) {
      res.writeHead(200, {'Content-Type':mime['.js'], 'Cache-Control':'no-store'});
      res.end('window.ALFA_DEMO = ' + fs.readFileSync(path.join(root, 'data/demo.json'), 'utf8') + ';'); return;
    }
    fs.stat(file, (error,stat) => {
      if (error || !stat.isFile()) {res.writeHead(404).end('Not found'); return;}
      res.writeHead(200, {'Content-Type':mime[path.extname(file)] || 'application/octet-stream', 'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
      fs.createReadStream(file).pipe(res);
    });
  } catch {res.writeHead(400).end('Bad request');}
});
const port = Number(process.env.PORT || 4173);
const host = process.env.HOST || '127.0.0.1';
server.on('error', error => {console.error(`Unable to start local preview: ${error.message}`);process.exitCode=1;});
server.listen(port, host, () => console.log(`Local: http://${host}:${port}`));
