const vscode = require('vscode');
const http = require('node:http');
const {randomUUID} = require('node:crypto');
let timer, pending, session, context;

function call(path, method='GET', body) {
  return context.secrets.get('jiraya.token').then(token => {
    if (!token) throw new Error('Run “Jiraya: Pair Local Companion” first.');
    const data = body === undefined ? null : JSON.stringify(body);
    return new Promise((resolve, reject) => {
      const req = http.request({hostname:'127.0.0.1', port:vscode.workspace.getConfiguration('jiraya').get('companionPort',8765), path, method, headers:{Authorization:`Bearer ${token}`, 'Content-Type':'application/json', ...(data ? {'Content-Length':Buffer.byteLength(data)} : {})}}, res => {
        let text='';res.setEncoding('utf8');res.on('data',chunk=>text+=chunk);res.on('end',()=>{
          try {const value=JSON.parse(text);if(res.statusCode>=400) reject(new Error(value.detail || 'Companion request failed.'));else resolve(value);} catch {reject(new Error('Invalid companion response.'));}
        });
      });
      req.setTimeout(10000,()=>req.destroy(new Error('Local companion timed out.')));
      req.on('error',reject);if(data)req.write(data);req.end();
    });
  });
}

async function heartbeat() {
  if (!vscode.workspace.isTrusted) return;
  const folders=(vscode.workspace.workspaceFolders || []).filter(f=>f.uri.scheme==='file');
  const unsaved=new Set();
  for(const document of vscode.workspace.textDocuments) {
    if(document.isDirty && document.uri.scheme==='file') {
      const folder=vscode.workspace.getWorkspaceFolder(document.uri);if(folder)unsaved.add(folder.uri.fsPath);
    }
  }
  await call('/workspaces/heartbeat','POST',{session_id:session,folders:folders.map(f=>f.uri.fsPath),unsaved_folders:[...unsaved]});
}

function activate(extensionContext) {
  context=extensionContext;session=randomUUID();
  const report=()=>heartbeat().catch(()=>{});
  context.subscriptions.push(vscode.commands.registerCommand('jiraya.pair',async()=>{
    const token=await vscode.window.showInputBox({title:'Pair Jiraya Local Companion',prompt:'Paste the pairing token printed by jiraya-companion serve.',password:true,ignoreFocusOut:true});
    if(!token)return;
    await context.secrets.store('jiraya.token',token.trim());
    try {await heartbeat();vscode.window.showInformationMessage('Jiraya paired. Enable project checkpoints in the GitHub workspace.');} catch(e){vscode.window.showErrorMessage(`Jiraya: ${e.message}`);}
  }));
  context.subscriptions.push(vscode.commands.registerCommand('jiraya.checkpoint',async()=>{
    try {
      // This saves this window; other windows report any unsaved files and are skipped.
      await vscode.workspace.saveAll(false);await heartbeat();
      const job=await call('/checkpoints','POST',{message:`Jiraya workspace checkpoint ${new Date().toISOString().slice(0,10)}`});
      await vscode.window.withProgress({location:vscode.ProgressLocation.Notification,title:'Jiraya workspace checkpoint'},async progress=>{
        for(let attempt=0;attempt<240;attempt++) {
          const current=await call('/checkpoints/latest');
          if(current?.id!==job.id)throw new Error('Checkpoint job changed. Review results in Jiraya.');
          progress.report({message:`${current.results.length} projects checked`});
          if(current.status==='COMPLETE') {
            const pushed=current.results.filter(row=>row.status==='PUSHED').length;
            const attention=current.results.filter(row=>!['PUSHED','UP_TO_DATE'].includes(row.status)).length;
            const summary=`Jiraya: ${pushed} projects pushed; ${attention} need attention. Review per-project results in the Jiraya GitHub page.`;
            if(attention)vscode.window.showWarningMessage(summary);else vscode.window.showInformationMessage(summary);
            return;
          }
          await new Promise(resolve=>setTimeout(resolve,2000));
        }
        vscode.window.showInformationMessage('Checkpoint continues locally. Open Jiraya to review its results.');
      });
    } catch(e){vscode.window.showErrorMessage(`Jiraya: ${e.message}`);}
  }));
  context.subscriptions.push(vscode.workspace.onDidChangeWorkspaceFolders(report),vscode.workspace.onDidSaveTextDocument(report),vscode.workspace.onDidCloseTextDocument(report),vscode.workspace.onDidChangeTextDocument(()=>{clearTimeout(pending);pending=setTimeout(report,500);}));
  timer=setInterval(report,15000);report();
  context.subscriptions.push({dispose:()=>{clearInterval(timer);clearTimeout(pending);}});
}
function deactivate() {clearInterval(timer);clearTimeout(pending);return call(`/workspaces/${session}`,'DELETE').catch(()=>{});}
module.exports={activate,deactivate};
