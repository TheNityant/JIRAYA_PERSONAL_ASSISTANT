const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const {execFile, spawn} = require('node:child_process');
const {promisify} = require('node:util');
const execute = promisify(execFile);

function parseWebPairing(uri) {
  if (uri.path !== '/connect') throw Error('Unknown Jiraya connection request.');
  const query = new URLSearchParams(uri.query);
  const nonce = query.get('nonce') || '';
  const url = new URL(query.get('origin') || '');
  if (!/^[a-f0-9]{64}$/.test(nonce) || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash ||
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost','127.0.0.1'].includes(url.hostname)))) {
    throw Error('Use a trusted HTTPS Jiraya website or HTTP localhost.');
  }
  const port = Number(query.get('port') || 8765);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw Error('Invalid companion port.');
  return {origin:url.origin, nonce, port};
}

function createManager(vscode, context, call) {
  let starting, ownedProcess;
  const stateDirectory = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(),'.local','share'),'Jiraya','companion');
  const readToken = async () => JSON.parse(await fs.readFile(path.join(stateDirectory,'companion.json'),'utf8')).token;
  const assertDesktop = () => {
    if (!vscode.workspace.isTrusted || vscode.env.remoteName) throw Error('Open a trusted local desktop workspace to connect this computer.');
  };
  async function setup() {
    assertDesktop();
    const existing = context.globalState?.get('jiraya.managedPython');
    if (existing) return existing;
    const answer = await vscode.window.showInformationMessage(
      'Set up the local Git helper once? Jiraya will use Python 3.12+ and, if needed, install helper dependencies in a separate environment. Git credentials stay on this computer.',
      {modal:true},'Set up helper');
    if (answer !== 'Set up helper') throw Error('Local helper setup was cancelled.');
    let python = 'python';
    const extension = vscode.extensions?.getExtension('ms-python.python');
    if (extension) {
      const api = await extension.activate();
      python = api.environments?.getActiveEnvironmentPath()?.path || python;
    }
    try {
      await execute(python,['-c','import sys; assert sys.version_info >= (3, 12)'],{timeout:15000,windowsHide:true});
    } catch {
      const picked = await vscode.window.showOpenDialog({title:'Choose Python 3.12+ (python.exe on Windows)',canSelectFiles:true,canSelectFolders:false,canSelectMany:false});
      if (!picked?.length) throw Error('Python 3.12+ is required for the local helper.');
      python = picked[0].fsPath;
      await execute(python,['-c','import sys; assert sys.version_info >= (3, 12)'],{timeout:15000,windowsHide:true});
    }
    await vscode.window.withProgress({location:vscode.ProgressLocation.Notification,title:'Setting up Jiraya local helper'},async () => {
      try {
        await execute(python,['-c','import fastapi, uvicorn, pydantic'],{timeout:15000,windowsHide:true});
      } catch {
        const directory = path.join(context.globalStorageUri.fsPath,'python');
        await fs.mkdir(context.globalStorageUri.fsPath,{recursive:true});
        await execute(python,['-m','venv',directory],{timeout:60000,windowsHide:true});
        python = path.join(directory,process.platform === 'win32' ? 'Scripts' : 'bin',process.platform === 'win32' ? 'python.exe' : 'python');
        await execute(python,['-m','pip','install','--disable-pip-version-check','-r',path.join(context.extensionPath,'companion-requirements.txt')],{timeout:240000,windowsHide:true});
      }
    });
    await context.globalState.update('jiraya.managedPython',python);
    return python;
  }
  async function start() {
    assertDesktop();
    if (starting) return starting;
    starting = (async () => {
      const check = async () => {
        const token = await readToken();
        const health = await call('/health','GET',undefined,token);
        if (health.service !== 'jiraya-local-companion') throw Error('This port is occupied by another service.');
        if (!health.pairing_handoff) throw Error('An older Jiraya companion is running. Stop it in its original terminal with Ctrl+C, then connect again.');
        await context.secrets.store('jiraya.token',token);
        return token;
      };
      try {return await check();} catch (error) {
        if (/older Jiraya|another service/.test(error.message)) throw error;
      }
      const python = await setup();
      const root = path.join(context.extensionPath,'companion');
      await fs.access(path.join(root,'app','local_companion','server.py')).catch(() => {throw Error('Install the packaged Jiraya 0.4.0 VSIX to get the bundled helper.');});
      const child = spawn(python,['-m','app.local_companion','serve','--port',String(vscode.workspace.getConfiguration('jiraya').get('companionPort',8765)),'--config-dir',stateDirectory],{
        cwd:root,env:{...process.env,PYTHONPATH:root},detached:true,windowsHide:true,stdio:'ignore',
      });
      ownedProcess = child;
      let launchError;
      child.on('error',error => {launchError = error;}); child.unref();
      for (let attempt=0;attempt<80;attempt++) {
        if (launchError) throw Error('Could not start Python. Run Jiraya: Set Up Local Companion again.');
        await new Promise(resolve=>setTimeout(resolve,150));
        try {return await check();} catch (error) {
          if (/older Jiraya|another service/.test(error.message)) throw error;
        }
      }
      throw Error('The local helper did not start. Check Python, Git, and whether the configured port is already in use.');
    })();
    try {return await starting;} finally {starting=undefined;}
  }
  async function connect(uri) {
    try {
      assertDesktop();
      const request = parseWebPairing(uri);
      const confirmed = await vscode.window.showInformationMessage(
        `Allow ${request.origin} to connect to this computer's Git helper? You will still choose and enable each repository before pushing.`,
        {modal:true},'Connect this website');
      if (confirmed !== 'Connect this website') return;
      await vscode.workspace.getConfiguration('jiraya').update('companionPort',request.port,vscode.ConfigurationTarget.Global);
      await setup();
      const token = await start();
      await call('/pairing/requests','POST',{nonce:request.nonce,origin:request.origin},token);
      vscode.window.showInformationMessage('Device approved. Return to your Jiraya browser tab; it will finish pairing automatically.');
    } catch (error) {
      // Raw child-process errors can include command lines; show safe setup guidance.
      vscode.window.showErrorMessage(error.code ? 'Jiraya helper setup failed. Check Python 3.12+, internet access and the interpreter, then retry.' : `Jiraya: ${error.message}`);
    }
  }
  async function stop() {
    if (!ownedProcess || ownedProcess.exitCode !== null) throw Error('This window did not start the running helper. Stop it from its original window or terminal.');
    const job = await call('/checkpoints/latest');
    if (job?.status === 'RUNNING') throw Error('Wait for the current checkpoint to finish before stopping the helper.');
    const child = ownedProcess;
    await new Promise(resolve => {child.once('exit',resolve);child.kill();});
    ownedProcess = undefined;
  }
  return {start,stop,setup,connect,configured:()=>Boolean(context.globalState?.get('jiraya.managedPython')),
    reset:async()=>context.globalState.update('jiraya.managedPython',undefined)};
}

module.exports = {createManager,parseWebPairing};
