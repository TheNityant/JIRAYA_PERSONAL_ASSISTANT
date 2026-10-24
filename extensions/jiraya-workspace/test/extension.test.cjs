const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const http=require('node:http');
const path=require('node:path');

function windowContext(port, folder, dirty=false, trusted=true, options={}) {
 const commands={},secretCalls=[],handlers={},messages={info:[],warning:[],error:[]},lines=[];
 const status={show(){},dispose(){}};
 let stored=options.storedToken ?? 'paired-token';
 const subscribe=name=>fn=>{handlers[name]=fn;return {dispose(){}};};
 const vscode={env:{remoteName:options.remoteName},StatusBarAlignment:{Left:1},workspace:{isTrusted:trusted,workspaceFolders:folder?[{uri:{scheme:'file',fsPath:folder}},{uri:{scheme:'vscode-remote',fsPath:'/remote/skip'}}]:[],textDocuments:dirty?[{isDirty:true,uri:{scheme:'file',fsPath:folder+'/file.js'}}]:[],getWorkspaceFolder:()=>({uri:{fsPath:folder}}),getConfiguration:()=>({get:()=>port}),onDidChangeWorkspaceFolders:subscribe('folders'),onDidSaveTextDocument:subscribe('save'),onDidCloseTextDocument:subscribe('close'),onDidChangeTextDocument:subscribe('edit'),onDidChangeConfiguration:subscribe('configuration'),saveAll:async()=>true},commands:{registerCommand:(name,fn)=>{commands[name]=fn;return {dispose(){}};}},window:{createOutputChannel:()=>({appendLine:line=>lines.push(line),show(){},dispose(){}}),createStatusBarItem:()=>status,showInputBox:async()=>options.input ?? 'paired-token',showInformationMessage:text=>messages.info.push(text),showErrorMessage:text=>messages.error.push(text),showWarningMessage:text=>messages.warning.push(text),withProgress:async(_opts,fn)=>fn({report(){}})},ProgressLocation:{Notification:1}};
 const context={subscriptions:[],secrets:{onDidChange:subscribe('secret'),get:async()=>stored,store:async(...args)=>{secretCalls.push(args);stored=args[1];}}};
 const module={exports:{}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../extension.js'),'utf8'),{require:name=>name==='vscode'?vscode:require(name),module,Buffer,setInterval:()=>1,clearInterval(){},setTimeout,clearTimeout});
 module.exports.activate(context);
 return {api:module.exports,commands,secretCalls,status,messages,lines,handlers};
}
async function until(check) {for(let i=0;i<200;i++){if(check())return;await new Promise(r=>setTimeout(r,10));}throw Error('Timed out');}
async function companion(fn, custom) {
 const calls=[];
 const server=http.createServer((req,res)=>{let body='';req.on('data',chunk=>body+=chunk);req.on('end',()=>{
  const call={path:req.url,method:req.method,auth:req.headers.authorization,body:body?JSON.parse(body):null};calls.push(call);
  res.setHeader('Content-Type','application/json');
  if(custom && custom(call,res))return;
  const value=req.url==='/health'?{service:'jiraya-local-companion'}:req.url==='/workspaces/heartbeat'?{registered:true,project_count:call.body.folders.length,issues:[]}:{items:[]};
  res.end(JSON.stringify(value));
 });});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try {await fn(server.address().port,calls);}finally{await new Promise(resolve=>server.close(resolve));}
}

test('separate IDE windows register local folders and unsaved state; closure unregisters',async()=>{
 await companion(async(port,calls)=>{
  const first=windowContext(port,'/projects/jiraya');const second=windowContext(port,'/projects/notebook',true);
  await until(()=>calls.length>=2);
  const firstCall=calls.find(call=>call.body.folders[0]==='/projects/jiraya');
  const secondCall=calls.find(call=>call.body.folders[0]==='/projects/notebook');
  assert.deepEqual(firstCall.body.folders,['/projects/jiraya']);
  assert.deepEqual(secondCall.body.unsaved_folders,['/projects/notebook']);
  assert.notEqual(firstCall.body.session_id,secondCall.body.session_id);
  assert(calls.every(call=>call.auth==='Bearer paired-token'));
  await first.commands['jiraya.pair']();assert.deepEqual(first.secretCalls,[['jiraya.token','paired-token']]);
  assert.match(first.messages.info[0],/1 Git projects registered/);
  await first.commands['jiraya.diagnose']();assert.match(first.lines.join('\n'),/Companion port/);
  await first.api.deactivate();await second.api.deactivate();
  assert.equal(calls.filter(call=>call.method==='DELETE').length,2);
 });
});

test('invalid replacement token preserves working stored credentials',async()=>{
 await companion(async(port,calls)=>{
  const window=windowContext(port,'/projects/jiraya',false,true,{input:'bad-token'});
  await until(()=>/registered/.test(window.status.text||''));
  await window.commands['jiraya.pair']();
  assert.equal(window.secretCalls.length,0);
  assert.match(window.messages.error[0],/token rejected/);
  assert.equal(window.messages.info.length,0);
  await window.commands['jiraya.diagnose']();
  assert.equal(calls.at(-1).auth,'Bearer paired-token');
  assert(!window.lines.join('\n').includes('bad-token'));
  await window.api.deactivate();
 },(call,res)=>{if(call.auth==='Bearer bad-token'){res.statusCode=401;res.end('{"detail":"Pairing token rejected"}');return true;}});
});

test('untrusted and remote workspaces cannot falsely report successful registration',async()=>{
 await companion(async(port,calls)=>{
  for(const options of [{trusted:false},{trusted:true,remoteName:'wsl'}]) {
   const window=windowContext(port,'/projects/jiraya',false,options.trusted,options);
   await until(()=>/Restricted Mode|local desktop/.test(window.status.text||''));
   await window.commands['jiraya.pair']();
   assert.equal(window.secretCalls.length,0);
   assert.equal(window.messages.info.length,0);
   assert.equal(calls.filter(call=>call.method==='POST').length,0);
   await window.api.deactivate();
  }
 });
});

test('empty windows and rejected folders show actionable diagnostics rather than success',async()=>{
 await companion(async(port)=>{
  const empty=windowContext(port,null);
  await empty.commands['jiraya.pair']();
  assert.match(empty.status.text,/Open a local project folder/);
  assert.match(empty.messages.warning[0],/no Git projects/);
  const rejected=windowContext(port,'/projects/parent');
  await rejected.commands['jiraya.pair']();
  assert.match(rejected.status.text,/0 Git projects registered; 1 folders need attention/);
  assert.match(rejected.lines.join('\n'),/Open the repository folder/);
  assert(!rejected.lines.join('\n').includes('paired-token'));
  await empty.api.deactivate();await rejected.api.deactivate();
 },(call,res)=>{
  if(call.path==='/workspaces/heartbeat') {
   const folders=call.body.folders;
   res.end(JSON.stringify({registered:true,project_count:0,issues:folders.map(folder=>({path:folder,error:'Open the repository folder'}))}));return true;
  }
 });
});

test('background connection errors remain visible in the status bar and output',async()=>{
 const server=http.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const port=server.address().port;await new Promise(resolve=>server.close(resolve));
 const window=windowContext(port,'/projects/jiraya');
 await until(()=>/Companion unavailable/.test(window.status.text||''));
 assert.match(window.lines.join('\n'),/Start it on the configured local port/);
 await window.api.deactivate();
});
