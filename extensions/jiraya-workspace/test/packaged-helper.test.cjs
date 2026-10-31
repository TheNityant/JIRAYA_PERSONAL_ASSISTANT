const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {execFile} = require('node:child_process');
const {promisify} = require('node:util');
const execute = promisify(execFile);

test('packaged helper starts, hands off once, reuses its token and restarts with persisted policies', {skip:!process.env.JIRAYA_TEST_PYTHON,timeout:40000}, async () => {
  const python = process.env.JIRAYA_TEST_PYTHON;
  const root = path.resolve(__dirname,'..');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(),'jiraya-packaged-'));
  const previous = process.env.LOCALAPPDATA;
  process.env.LOCALAPPDATA = directory;
  let manager;
  try {
    await execute(python,[path.join(root,'build_vsix.py')]);
    const archive = path.join(root,'jiraya-workspace-0.5.0.vsix');
    await execute(python,['-c',`import sys, zipfile; z=zipfile.ZipFile(sys.argv[1]); assert len(z.namelist()) == 23; assert not any('.env' in n or 'config.py' in n for n in z.namelist()); z.extractall(sys.argv[2])`,archive,directory]);
    const extensionPath = path.join(directory,'extension');
    const {createManager} = require(path.join(extensionPath,'managed-companion.js'));
    const listener=net.createServer();await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
    let port=listener.address().port;await new Promise(resolve=>listener.close(resolve));
    const globalValues = new Map([['jiraya.managedPython',python],['jiraya.meetRuntimeReady',true]]), secrets = new Map(), messages=[];
    const context = {extensionPath,globalStorageUri:{fsPath:path.join(directory,'storage')},globalState:{get:key=>globalValues.get(key),update:async(key,value)=>globalValues.set(key,value)},secrets:{store:async(key,value)=>secrets.set(key,value)}};
    const vscode = {workspace:{isTrusted:true,getConfiguration:()=>({get:()=>port,update:async(key,value)=>{port=value;}})},env:{},ConfigurationTarget:{Global:1},window:{showInformationMessage:async(message,options,...choices)=>{messages.push(message);return choices[0];},showErrorMessage:message=>{throw Error(message);}}};
    const call=async(endpoint,method='GET',body,token=secrets.get('jiraya.token'))=>{
      const response=await fetch(`http://127.0.0.1:${port}${endpoint}`,{method,headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
      const data=await response.json();if(!response.ok)throw Error(`Helper HTTP ${response.status}`);return data;
    };
    manager=createManager(vscode,context,call);
    const token=await manager.start();assert.equal(secrets.get('jiraya.token'),token);
    assert.equal((await call('/health')).pairing_handoff,true);
    assert.equal(await manager.start(),token);
    const nonce='a'.repeat(64), origin='https://fixture-jiraya.example';
    await manager.connect({path:'/connect',query:new URLSearchParams({nonce,origin,port:String(port)}).toString()});
    const claim=()=>fetch(`http://127.0.0.1:${port}/pairing/claim`,{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({nonce})});
    const approved=await claim();assert.equal(approved.status,200);assert.equal(approved.headers.get('cache-control'),'no-store');assert.equal((await approved.json()).token,token);assert.equal((await claim()).status,404);
    const stateFile=path.join(directory,'Jiraya','companion','companion.json');
    await manager.stop();
    const state=JSON.parse(await fs.readFile(stateFile,'utf8'));state.projects={fixture:{enabled:true,branch:'fixture-branch'}};await fs.writeFile(stateFile,JSON.stringify(state));
    manager=createManager(vscode,context,call); // A fresh extension session uses saved settings.
    assert(manager.configured());
    assert.equal(await manager.start(),token);
    const retained=JSON.parse(await fs.readFile(stateFile,'utf8'));assert.deepEqual(retained.projects,state.projects);assert(retained.origins.includes(origin));
    assert(messages.every(message=>!message.includes(token)));
  } finally {
    if(manager)await manager.stop().catch(()=>{});
    if(previous===undefined)delete process.env.LOCALAPPDATA;else process.env.LOCALAPPDATA=previous;
    await fs.rm(directory,{recursive:true,force:true});
  }
});
