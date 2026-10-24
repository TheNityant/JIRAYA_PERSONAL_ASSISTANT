// Run from web with Playwright installed: node tests/workspace-pairing.cjs
// Uses a temporary Next route and a real isolated Python companion; never pushes Git.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const {chromium} = require('playwright');

async function main() {
  const web = path.resolve(__dirname, '..');
  const route = path.join(web, 'app', 'pairing-regression-harness');
  const config = fs.mkdtempSync(path.join(os.tmpdir(), 'jiraya-pairing-'));
  const token = 'isolated-pairing-regression-token';
  fs.writeFileSync(path.join(config, 'companion.json'), JSON.stringify({token, projects:{}}));
  fs.mkdirSync(route); // Refuse to overwrite an existing route.
  const services = [];
  let browser;
  let logs = '';
  function start(command, args, cwd) {
    const child = spawn(command, args, {cwd, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe']});
    services.push(child);
    child.stdout.on('data', data => {logs += data;});
    child.stderr.on('data', data => {logs += data;});
    return child;
  }
  try {
    fs.writeFileSync(path.join(route, 'page.tsx'), 'import WorkspaceCheckpoint from "@/components/WorkspaceCheckpoint";\nexport default function Page() {return <WorkspaceCheckpoint/>;}\n');
    start(process.execPath, [path.join(web, 'node_modules/next/dist/bin/next'), 'dev', '--hostname', '127.0.0.1', '--port', '3117'], web);
    start(process.env.PYTHON || 'python', ['-c', 'import sys,uvicorn; from pathlib import Path; from app.local_companion.server import create_companion; uvicorn.run(create_companion(Path(sys.argv[1]), ["http://127.0.0.1:3117"]),host="127.0.0.1",port=8876)', config], path.dirname(web));
    const url = 'http://127.0.0.1:3117/pairing-regression-harness';
    for (let attempt = 0; ; attempt++) {
      try {
        const [front, companion] = await Promise.all([fetch(url), fetch('http://127.0.0.1:8876/health', {headers:{Authorization:`Bearer ${token}`}})]);
        if (front.ok && companion.ok) break;
      } catch {}
      if (attempt > 120) throw Error(`Test services did not start:\n${logs}`);
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    const launch = {headless:true};
    if (process.env.CHROMIUM_EXECUTABLE_PATH) launch.executablePath = process.env.CHROMIUM_EXECUTABLE_PATH;
    if (process.env.JIRAYA_SPARTICUZ) {
      const {default: binary} = await import(process.env.JIRAYA_SPARTICUZ);
      launch.executablePath = await binary.executablePath();
      launch.args = binary.args;
    }
    browser = await chromium.launch(launch);
    const page = await browser.newPage();
    await page.goto(url);
    const field = page.getByLabel('Pairing token', {exact:true});
    await field.fill(token);
    await page.getByLabel('Local port', {exact:true}).fill('8876');
    await page.getByRole('button', {name:'Connect companion',exact:true}).click();
    await page.getByText('Companion connected', {exact:true}).waitFor();
    await page.reload();
    await page.getByText('Companion connected', {exact:true}).waitFor();
    console.log('PASS: typed token and persisted pairing with real companion');
    await page.getByRole('button', {name:'Forget pairing'}).click();
    await field.fill('wrong-token');
    await page.getByRole('button', {name:'Connect companion',exact:true}).click();
    await page.getByRole('alert').filter({hasText:/token/i}).waitFor();
    // Password managers can update the field without firing React's change handler.
    // Make the submitted DOM value differ from the last React state value.
    await field.evaluate((input, value) => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
    }, `  ${token}  `);
    const health = page.waitForResponse(response => response.url() === 'http://127.0.0.1:8876/health' && response.request().method() === 'GET');
    await page.getByRole('button', {name:'Connect companion',exact:true}).click();
    assert.equal((await health).status(), 200, 'Submit must authenticate with the visible form value, not stale React state');
    await page.getByText('Companion connected', {exact:true}).waitFor();
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('jiraya.companion')));
    assert.equal(saved.token, token, 'Persist the same normalized token that was authenticated');
    await page.reload();
    await page.getByText('Companion connected', {exact:true}).waitFor();
    console.log('PASS: wrong-token recovery, autofill/state mismatch, whitespace and reload');
    await page.getByText('No editor has registered yet.', {exact:false}).waitFor();
    const registration = async folders => {
      const response = await fetch('http://127.0.0.1:8876/workspaces/heartbeat', {method:'POST',headers:{Authorization:`Bearer ${token}`, 'Content-Type':'application/json'},body:JSON.stringify({session_id:'browser-test-editor',folders})});
      assert.equal(response.status,200);
    };
    await registration([config]); // Existing folder with no .git must produce an explanation.
    await page.getByRole('button', {name:'Refresh projects',exact:true}).click();
    await page.getByText('Your editor is reporting, but no Git repositories were found.', {exact:false}).waitFor();
    await page.getByText('Registration details', {exact:true}).click();
    await page.getByText('Git could not identify this folder as a repository.', {exact:false}).waitFor();
    const repo = path.join(config,'repository');fs.mkdirSync(repo);
    const git = (...args) => execFileSync('git',['-C',repo,...args],{stdio:'pipe'});
    git('init','--initial-branch=main');git('config','user.name','Pairing Regression');git('config','user.email','regression@example.test');
    fs.writeFileSync(path.join(repo,'readme.txt'),'isolated browser registration fixture');
    git('add','.');git('commit','-m','Initial');git('remote','add','origin','https://github.com/jiraya-fixtures/workspace.git');
    await registration([repo]);
    await page.getByRole('button', {name:'Refresh projects',exact:true}).click();
    await page.getByText('jiraya-fixtures/workspace', {exact:true}).waitFor();
    await page.getByRole('button',{name:'Enable checkpoint',exact:true}).click();
    await page.getByRole('button',{name:'Disable checkpoint',exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Checkpoint all enabled open projects',exact:true}).isEnabled(),true);
    // Do not click checkpoint: browser tests never send Git traffic to GitHub.
    const closed = await fetch('http://127.0.0.1:8876/workspaces/browser-test-editor',{method:'DELETE',headers:{Authorization:`Bearer ${token}`}});
    assert.equal(closed.status,200);
    await page.getByRole('button',{name:'Refresh projects',exact:true}).click();
    await page.getByText('No editor has registered yet.', {exact:false}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Checkpoint all enabled open projects',exact:true}).isEnabled(),false);
    console.log('PASS: missing editor, rejected folder details, real Git registration, enable and window closure');

  } finally {
    fs.rmSync(route, {recursive:true, force:true});
    fs.rmSync(config, {recursive:true, force:true});
    if (browser) await browser.close();
    for (const child of services) {
      try {
        if (process.platform === 'win32') spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {stdio:'ignore'});
        else process.kill(-child.pid, 'SIGTERM');
      } catch {}
      child.stdout.destroy();child.stderr.destroy();child.unref();
    }
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
