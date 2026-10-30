// Run after `next build`. Verify runtime diagnostics without real credentials.
const assert = require('node:assert/strict');
const path = require('node:path');
const {spawn} = require('node:child_process');

async function check(backend, status, detail) {
  const web = path.resolve(__dirname,'..');
  const frontend = 'http://127.0.0.1:3124';
  let logs = '';
  const server = spawn(process.execPath,[path.join(web,'node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','--port','3124'],{
    cwd:web,detached:process.platform !== 'win32',stdio:['ignore','pipe','pipe'],
    env:{...process.env,JIRAYA_BACKEND_URL:backend,NEXT_PUBLIC_JIRAYA_API_URL:'/api/jiraya'},
  });
  server.stdout.on('data', data => logs += data);
  server.stderr.on('data', data => logs += data);
  try {
    let response;
    for (let attempt = 0; attempt < 80; attempt++) {
      try { response = await fetch(frontend + '/api/jiraya/health'); break; } catch {}
      if (server.exitCode !== null) throw Error(logs);
      await new Promise(resolve => setTimeout(resolve,100));
    }
    assert(response,logs);
    assert.equal(response.status,status,`Backend setting ${JSON.stringify(backend)}; response headers ${JSON.stringify(Object.fromEntries(response.headers))}; ${logs}`);
    assert.equal(response.headers.get('x-jiraya-proxy'),'route-v1');
    assert.match((await response.json()).detail,detail);
  } finally {
    try {process.kill(-server.pid,'SIGTERM');} catch {}
    await new Promise(resolve => server.exitCode !== null ? resolve() : server.once('exit',resolve));
  }
}

(async () => {
  await check('',503,/set JIRAYA_BACKEND_URL/);
  await check('http://127.0.0.1:3124',503,/backend, not this frontend/);
  await check('http://127.0.0.1:1',502,/could not reach its backend/);
  console.log('PASS: built proxy diagnoses missing settings, self-routing and unreachable backend with JSON instead of a Next HTML 404');
})().catch(error => {console.error(error);process.exitCode=1;});
