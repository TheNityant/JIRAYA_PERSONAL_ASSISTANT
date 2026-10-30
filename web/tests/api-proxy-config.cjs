const assert = require('node:assert/strict');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {pathToFileURL} = require('node:url');
const configUrl = pathToFileURL(path.resolve(__dirname,'../next.config.mjs')).href;
function check(backend, api, succeeds) {
  const env = {...process.env};
  delete env.JIRAYA_BACKEND_URL; delete env.NEXT_PUBLIC_JIRAYA_API_URL;
  if (backend !== undefined) env.JIRAYA_BACKEND_URL = backend;
  if (api !== undefined) env.NEXT_PUBLIC_JIRAYA_API_URL = api;
  const result = spawnSync(process.execPath,['--input-type=module','-e',`const {default:c}=await import(${JSON.stringify(configUrl)});process.stdout.write(JSON.stringify(await c.rewrites()));`],{env,encoding:'utf8'});
  assert.equal(result.status === 0,succeeds);
  return succeeds ? JSON.parse(result.stdout) : null;
}
assert.deepEqual(check(undefined,undefined,true),[]);
assert.equal(check('https://api.example.test','/api/jiraya',true)[0].destination,'https://api.example.test/:path*');
assert.equal(check('http://127.0.0.1:8000','/api/jiraya',true)[0].destination,'http://127.0.0.1:8000/:path*');
check(undefined,'/api/jiraya',false);
for (const target of ['http://remote.example.test','https://user:pass@api.example.test','https://api.example.test/path','https://api.example.test/?target=x','https://api.example.test/#x']) check(target,'/api/jiraya',false);
console.log('PASS: optional routing, fixed destination, HTTPS/loopback validation and missing deployment settings');
