const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const http=require('node:http');
const path=require('node:path');

function windowContext(port, folder, dirty=false, trusted=true) {
 const commands={},secretCalls=[],handlers=[];
 const vscode={workspace:{isTrusted:trusted,workspaceFolders:[{uri:{scheme:'file',fsPath:folder}},{uri:{scheme:'vscode-remote',fsPath:'/remote/skip'}}],textDocuments:dirty?[{isDirty:true,uri:{scheme:'file',fsPath:folder+'/file.js'}}]:[],getWorkspaceFolder:()=>({uri:{fsPath:folder}}),getConfiguration:()=>({get:()=>port}),onDidChangeWorkspaceFolders:fn=>handlers.push(fn),onDidSaveTextDocument:fn=>handlers.push(fn),onDidCloseTextDocument:fn=>handlers.push(fn),onDidChangeTextDocument:fn=>handlers.push(fn),saveAll:async()=>true},commands:{registerCommand:(name,fn)=>{commands[name]=fn;return {dispose(){}};}},window:{showInputBox:async()=> 'paired-token',showInformationMessage(){},showErrorMessage(){},showWarningMessage(){},withProgress:async(_opts,fn)=>fn({report(){}})},ProgressLocation:{Notification:1}};
 const context={subscriptions:[],secrets:{get:async()=> 'paired-token',store:async(...args)=>secretCalls.push(args)}};
 const module={exports:{}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../extension.js'),'utf8'),{require:name=>name==='vscode'?vscode:require(name),module,Buffer,setInterval:()=>1,clearInterval(){},setTimeout,clearTimeout});
 module.exports.activate(context);
 return {api:module.exports,commands,secretCalls};
}
async function until(check) {for(let i=0;i<100;i++){if(check())return;await new Promise(r=>setTimeout(r,10));}throw Error('Timed out');}

test('separate IDE windows register local folders and unsaved state; closure unregisters',async()=>{
 const calls=[];const server=http.createServer((req,res)=>{let body='';req.on('data',chunk=>body+=chunk);req.on('end',()=>{calls.push({path:req.url,method:req.method,auth:req.headers.authorization,body:body?JSON.parse(body):null});res.setHeader('Content-Type','application/json');res.end('{}');});});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try {
  const port=server.address().port;
  const first=windowContext(port,'/projects/jiraya');const second=windowContext(port,'/projects/notebook',true);
  await until(()=>calls.length>=2);
  assert.deepEqual(calls[0].body.folders,['/projects/jiraya']);
  assert.deepEqual(calls[1].body.folders,['/projects/notebook']);
  assert.deepEqual(calls[1].body.unsaved_folders,['/projects/notebook']);
  assert.notEqual(calls[0].body.session_id,calls[1].body.session_id);
  assert(calls.every(call=>call.auth==='Bearer paired-token'));
  await first.commands['jiraya.pair']();assert.deepEqual(first.secretCalls,[['jiraya.token','paired-token']]);
  await first.api.deactivate();await second.api.deactivate();
  assert.equal(calls.filter(call=>call.method==='DELETE').length,2);
  const before=calls.length;
  const untrusted=windowContext(port,'/projects/untrusted',false,false);
  await new Promise(resolve=>setTimeout(resolve,40));assert.equal(calls.length,before);
  await untrusted.api.deactivate();
 } finally {await new Promise(resolve=>server.close(resolve));}
});
