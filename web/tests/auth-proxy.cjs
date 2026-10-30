// Exercise the real AccountGate and explicit Next API route with isolated HTTP fixtures.
// No real accounts, credentials, database, emails or OAuth providers are contacted.
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');

async function main() {
  const web = path.resolve(__dirname, '..');
  let server, browser, logs = '', issueCookie = true;
  const calls = [];
  const sessions = new Set();
  const frontend = 'http://127.0.0.1:3123';
  const backend = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://fixture');
    calls.push({path: url.pathname, method: req.method, origin: req.headers.origin});
    res.setHeader('Cache-Control', 'private, no-store');
    const token = /(?:^|;\s*)jiraya_session=([^;]+)/.exec(req.headers.cookie || '')?.[1];
    const user = sessions.has(token) ? {id:'fixture-user', display_name:'Fixture User', email:'fixture@example.test'} : null;
    const json = (status, data) => {res.writeHead(status, {'Content-Type':'application/json'}); res.end(JSON.stringify(data));};
    if (url.pathname === '/health') return json(200, {status:'ok'});
    if (url.pathname === '/auth/me') return json(200, {user, auth_required:true, registration_enabled:true});
    if (url.pathname === '/automations') return json(200, []);
    if (url.pathname === '/auth/login') {
      if (req.headers.origin !== frontend) return json(403, {detail:'Request origin is not allowed.'});
      let body = ''; for await (const chunk of req) body += chunk;
      const data = JSON.parse(body);
      if (data.password !== 'fixture-password-only') return json(401, {detail:'Email or password is incorrect.'});
      if (issueCookie) {
        const token = 'fixture-' + sessions.size;
        sessions.add(token);
        // HTTP fixture only; the real production backend sets Secure automatically.
        res.setHeader('Set-Cookie', `jiraya_session=${token}; Path=/; HttpOnly; SameSite=Lax`);
      }
      return json(200, {user:{id:'fixture-user'}});
    }
    if (url.pathname === '/auth/logout') {
      sessions.delete(token); res.setHeader('Set-Cookie','jiraya_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax');
      return json(200, {signed_out:true});
    }
    if (url.pathname === '/connections') return json(user ? 200 : 401, user ? {catalog:[]} : {detail:'Sign in required'});
    if (['/connections/google/callback','/connections/google/calendar/callback','/connections/github/callback'].includes(url.pathname)) {
      if (!user || url.searchParams.get('state') !== 'fixture-state') return json(401, {detail:'Session or query missing'});
      res.writeHead(302, {Location:frontend + '/?fixture_callback=ok'}); return res.end();
    }
    if (url.pathname === '/fixture/cookies') {
      res.setHeader('Set-Cookie',['first=one; Path=/; HttpOnly; Expires=Wed, 01 Jan 2031 00:00:00 GMT','second=two; Path=/; HttpOnly']);
      return json(200, {ok:true});
    }
    if (url.pathname === '/fixture/error') return json(422, {detail:'fixture validation error'});
    if (url.pathname === '/fixture/echo') {
      let body = ''; for await (const chunk of req) body += chunk;
      return json(200, {method:req.method,query:url.searchParams.get('q'),body});
    }
    return json(404, {detail:'Fixture route not found'});
  });
  await new Promise(resolve => backend.listen(0,'127.0.0.1',resolve));
  const backendUrl = `http://127.0.0.1:${backend.address().port}`;
  try {
    const next = path.join(web,'node_modules/next/dist/bin/next');
    const env = {...process.env,JIRAYA_BACKEND_URL:backendUrl,NEXT_PUBLIC_JIRAYA_API_URL:'/api/jiraya'};
    const production = process.env.AUTH_PROXY_PRODUCTION === '1';
    if (production) await new Promise((resolve,reject) => {
      const build = spawn(process.execPath,[next,'build'],{cwd:web,env,stdio:['ignore','pipe','pipe']});
      build.stdout.on('data',data => logs += data); build.stderr.on('data',data => logs += data);
      build.once('error',reject); build.once('exit',code => code === 0 ? resolve() : reject(Error(logs)));
    });
    server = spawn(process.execPath,[next,production ? 'start' : 'dev','--hostname','127.0.0.1','--port','3123'], {
      cwd:web, detached:process.platform !== 'win32', stdio:['ignore','pipe','pipe'], env,
    });
    server.stdout.on('data',data => logs += data); server.stderr.on('data',data => logs += data);
    for (let attempt=0;;attempt++) {
      try { if ((await fetch(frontend + '/api/jiraya/auth/me')).ok) break; } catch {}
      if (attempt > 120) throw Error(logs);
      await new Promise(resolve => setTimeout(resolve,500));
    }
    const health = await fetch(frontend + '/api/jiraya/health');
    assert.equal(health.status,200);
    assert.equal(health.headers.get('x-jiraya-proxy'),'route-v1');
    assert.deepEqual(await health.json(),{status:'ok'});
    assert.equal(health.headers.get('cache-control'),'private, no-store');
    const cookies = await fetch(frontend + '/api/jiraya/fixture/cookies');
    assert.equal(cookies.headers.getSetCookie().length,2);
    for (const method of ['POST','PUT','PATCH','DELETE','OPTIONS']) {
      const response = await fetch(frontend + '/api/jiraya/fixture/echo?q=one%20two',{method,body:'fixture body',headers:{'Content-Type':'text/plain','Origin':frontend}});
      assert.deepEqual(await response.json(),{method,query:'one two',body:'fixture body'});
    }
    const error = await fetch(frontend + '/api/jiraya/fixture/error');
    assert.equal(error.status,422);
    assert.deepEqual(await error.json(),{detail:'fixture validation error'});
    const head = await fetch(frontend + '/api/jiraya/health',{method:'HEAD'});
    assert.equal(head.status,200);
    assert.equal(await head.text(),'');
    browser = await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH ? {executablePath:process.env.CHROMIUM_EXECUTABLE_PATH} : {})});
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(frontend);
    await page.getByRole('heading',{name:'Welcome back',exact:true}).waitFor();
    const login = async password => {
      await page.getByLabel('Email',{exact:true}).fill('fixture@example.test');
      await page.getByLabel('Password',{exact:true}).fill(password);
      await page.getByRole('button',{name:'Sign in',exact:true}).click();
    };
    await login('wrong-password-only');
    await page.getByRole('alert').filter({hasText:'Email or password is incorrect.'}).waitFor();
    await login('fixture-password-only');
    await page.getByRole('heading',{name:'Connect your accounts',exact:true}).waitFor();
    assert.equal(await page.evaluate(() => document.cookie.includes('jiraya_session')),false);
    const state = await page.evaluate(async () => (await fetch('/api/jiraya/auth/me',{credentials:'include'})).json());
    assert.equal(state.user.id,'fixture-user');
    await page.reload();
    await page.getByRole('heading',{name:'Connect your accounts',exact:true}).waitFor();
    for (const callback of ['google/callback','google/calendar/callback','github/callback']) {
      await page.goto(frontend + '/api/jiraya/connections/' + callback + '?state=fixture-state');
      assert.match(page.url(),/fixture_callback=ok/);
    }
    await page.getByRole('button',{name:'Sign out',exact:true}).click();
    await page.getByRole('heading',{name:'Welcome back',exact:true}).waitFor();
    assert.equal((await page.evaluate(async () => (await fetch('/api/jiraya/auth/me')).json())).user,null);
    issueCookie = false;
    await login('fixture-password-only');
    await page.getByRole('alert').filter({hasText:'browser did not retain'}).waitFor();
    assert(calls.some(call => call.path === '/auth/login' && call.origin === frontend));
    await context.close();
    console.log('PASS: explicit API route, health, write verbs/query/body, backend validation, multiple cookies, login/refresh, all three OAuth callback paths, logout and missing-session error');
  } finally {
    if (browser) await browser.close();
    if (server) {try {process.kill(-server.pid,'SIGTERM');} catch {} server.stdout.destroy(); server.stderr.destroy(); server.unref();}
    backend.closeAllConnections(); await new Promise(resolve => backend.close(resolve));
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});
