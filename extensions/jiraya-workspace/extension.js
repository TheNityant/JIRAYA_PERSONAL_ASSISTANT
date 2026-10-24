const vscode = require('vscode');
const http = require('node:http');
const {randomUUID} = require('node:crypto');
let timer, pending, session, context, output, status, lastStatus;

function reportStatus(text, problem=false) {
  status.text = `$(${problem ? 'warning' : 'repo'}) Jiraya: ${text}`;
  status.tooltip = 'Open Jiraya workspace registration diagnostics';
  if (text !== lastStatus) output.appendLine(`${new Date().toISOString()} ${text}`);
  lastStatus = text;
}

function call(path, method='GET', body, explicitToken) {
  return Promise.resolve(explicitToken ?? context.secrets.get('jiraya.token')).then(token => {
    if (!token) throw new Error('Run “Jiraya: Pair Local Companion” first.');
    const data = body === undefined ? null : JSON.stringify(body);
    return new Promise((resolve, reject) => {
      const req = http.request({hostname:'127.0.0.1', port:vscode.workspace.getConfiguration('jiraya').get('companionPort',8765), path, method, headers:{Authorization:`Bearer ${token}`, 'Content-Type':'application/json', ...(data ? {'Content-Length':Buffer.byteLength(data)} : {})}}, res => {
        let text='';res.setEncoding('utf8');res.on('data',chunk=>text+=chunk);res.on('end',()=>{
          try {const value=JSON.parse(text);if(res.statusCode>=400) reject(new Error(value.detail || 'Companion request failed.'));else resolve(value);} catch {reject(new Error('Invalid companion response.'));}
        });
      });
      req.setTimeout(10000,()=>req.destroy(new Error('Local companion timed out.')));
      req.on('error',error=>reject(new Error(error.code === 'ECONNREFUSED' ? 'Companion unavailable. Start it on the configured local port.' : error.message)));if(data)req.write(data);req.end();
    });
  });
}

async function heartbeat(token, verbose=false) {
  if (!vscode.workspace.isTrusted) throw new Error('Workspace is in Restricted Mode. Trust it before registering local projects.');
  if (vscode.env?.remoteName) throw new Error('This extension supports local desktop folders. Remote SSH, WSL and container workspaces need a companion in that environment.');
  const folders=(vscode.workspace.workspaceFolders || []).filter(f=>f.uri.scheme==='file');
  const unsaved=new Set();
  for(const document of vscode.workspace.textDocuments) {
    if(document.isDirty && document.uri.scheme==='file') {
      const folder=vscode.workspace.getWorkspaceFolder(document.uri);if(folder)unsaved.add(folder.uri.fsPath);
    }
  }
  const result=await call('/workspaces/heartbeat','POST',{session_id:session,folders:folders.map(f=>f.uri.fsPath),unsaved_folders:[...unsaved]},token);
  if (!folders.length) reportStatus('Open a local project folder',true);
  else if (result.project_count === undefined) reportStatus('Connected; restart an updated companion for registration details',true);
  else {
    const issues=result.issues || [];
    const summary=`${result.project_count} Git projects registered${issues.length ? `; ${issues.length} folders need attention` : ''}`;
    const changed=summary !== lastStatus;
    reportStatus(summary,issues.length>0 || !result.project_count);
    if(changed || verbose) for(const issue of issues) output.appendLine(`${issue.path}: ${issue.error}`);
  }
  return result;
}

function activate(extensionContext) {
  context=extensionContext;session=randomUUID();
  output=vscode.window.createOutputChannel('Jiraya Workspace');
  status=vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 10);
  status.command='jiraya.diagnose';status.show();
  context.subscriptions.push(output,status);
  const report=()=>heartbeat().catch(error=>reportStatus(error.message,true));
  context.subscriptions.push(vscode.commands.registerCommand('jiraya.diagnose',async()=>{
    output.show(true);
    output.appendLine(`Companion port: ${vscode.workspace.getConfiguration('jiraya').get('companionPort',8765)}; trusted: ${vscode.workspace.isTrusted}`);
    try {
      await call('/health');
      await heartbeat(undefined,true);
      const result=await call('/projects');
      for(const project of result.items || []) output.appendLine(`${project.path}: ${project.error || `branch ${project.branch}; ${project.enabled ? 'enabled' : 'not enabled'}`}`);
    } catch(error) {reportStatus(error.message,true);output.appendLine(error.message);}
  }));
  context.subscriptions.push(vscode.commands.registerCommand('jiraya.pair',async()=>{
    const token=await vscode.window.showInputBox({title:'Pair Jiraya Local Companion',prompt:'Paste the pairing token printed by jiraya-companion serve.',password:true,ignoreFocusOut:true});
    if(!token)return;
    try {
      // Verify before replacing working credentials; a failed pairing preserves them.
      const normalized=token.trim();
      const health=await call('/health','GET',undefined,normalized);
      if(health.service!=='jiraya-local-companion')throw new Error('This port is not a Jiraya companion.');
      const registration=await heartbeat(normalized);
      await context.secrets.store('jiraya.token',normalized);
      const count=registration.project_count;
      const message=count ? `Jiraya paired. ${count} Git projects registered. Enable checkpoints in the GitHub workspace.` : 'Jiraya connected, but no Git projects were confirmed. Open a local repository folder and run “Jiraya: Show Workspace Diagnostics”.';
      if(count)vscode.window.showInformationMessage(message);else vscode.window.showWarningMessage(message);
    } catch(e){reportStatus(e.message,true);vscode.window.showErrorMessage(`Jiraya: ${e.message}`);}
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
  context.subscriptions.push(vscode.workspace.onDidChangeWorkspaceFolders(report),vscode.workspace.onDidSaveTextDocument(report),vscode.workspace.onDidCloseTextDocument(report),vscode.workspace.onDidChangeTextDocument(()=>{clearTimeout(pending);pending=setTimeout(report,500);}),vscode.workspace.onDidChangeConfiguration(event=>{if(event.affectsConfiguration('jiraya.companionPort'))report();}));
  context.subscriptions.push(context.secrets.onDidChange(event=>{if(event.key==='jiraya.token')report();}));
  timer=setInterval(report,15000);report();
  context.subscriptions.push({dispose:()=>{clearInterval(timer);clearTimeout(pending);}});
}
function deactivate() {clearInterval(timer);clearTimeout(pending);return call(`/workspaces/${session}`,'DELETE').catch(()=>{});}
module.exports={activate,deactivate};
