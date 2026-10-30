const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
async function main() {
  const {backendOrigin} = await import(pathToFileURL(path.resolve(__dirname,'../lib/backend-origin.mjs')).href);
  assert.equal(backendOrigin(undefined),null);
  assert.equal(backendOrigin('  '),null);
  assert.equal(backendOrigin(' https://api.example.test/ '),'https://api.example.test');
  assert.equal(backendOrigin('http://127.0.0.1:8000'),'http://127.0.0.1:8000');
  for (const target of ['not-a-url','http://remote.example.test','https://user:pass@api.example.test','https://api.example.test/path','https://api.example.test/?target=x','https://api.example.test/#x']) {
    assert.throws(() => backendOrigin(target));
  }
  console.log('PASS: trusted backend origin, HTTPS/loopback validation and invalid destination rejection');
}
main().catch(error => {console.error(error);process.exitCode=1;});
