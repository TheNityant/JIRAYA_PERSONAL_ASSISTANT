// End-to-end local test: real Next + FastAPI, isolated SQLite and HTTP provider fixtures.
// Requires project Python dependencies and Playwright/Chromium. Never calls real services.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const {spawn}=require('node:child_process'),{randomBytes}=require('node:crypto');
const assert=require('node:assert/strict'),{chromium}=require('playwright');
async function main(){
 const web=path.resolve(__dirname,'..'),route=path.join(web,'app','briefing-regression-harness');
 const state=fs.mkdtempSync(path.join(os.tmpdir(),'jiraya-briefing-'));fs.mkdirSync(route);
 const processes=[],calls=[],turns=[];let browser,server,logs='',fail=false;
 const repos=['owner/jiraya','owner/notebook','owner/voice','owner/portfolio'].map((full_name,index)=>({id:index+1,full_name,html_url:`https://github.com/${full_name}`,default_branch:'main',private:false}));
 function start(command,args,cwd,extra={}){
  const child=spawn(command,args,{cwd,env:{...process.env,...extra},detached:process.platform!=='win32',stdio:['ignore','pipe','pipe']});processes.push(child);
  child.stdout.on('data',data=>logs+=data);child.stderr.on('data',data=>logs+=data);return child;
 }
 try{
  server=http.createServer((req,res)=>{
   let body='';req.on('data',data=>body+=data);req.on('end',()=>{
    calls.push({method:req.method,path:req.url});res.setHeader('Content-Type','application/json');
    if(req.url.startsWith('/gh/')){
     assert.equal(req.method,'GET');assert.equal(req.headers.authorization,'Bearer fixture-github-token');
     const item={id:42,number:7,title:'Review test change',body:'PRIVATE-BODY-NOT-FOR-HERMES',state:'open',name:'Test CI',status:'completed',conclusion:'failure'};
     res.end(JSON.stringify(req.url.includes('/actions/runs')?{workflow_runs:[item]}:[item]));return;
    }
    assert.equal(req.headers.authorization,'Bearer fixture-hermes-key');
    if(req.url==='/hermes/v1/toolsets'){res.end('[]');return;}
    assert.equal(req.url,'/hermes/v1/chat/completions');assert.equal(req.method,'POST');
    turns.push(JSON.parse(body));
    if(fail){res.statusCode=503;res.end('{"detail":"PRIVATE-UPSTREAM-ERROR"}');return;}
    res.end(JSON.stringify({choices:[{finish_reason:'stop',message:{content:'Needs attention: inspect the failed CI [S3].\nSuggested next steps: review the linked sources.\n<script>window.briefingInjected=true</script>'}}]}));
   });
  });
  await new Promise(resolve=>server.listen(8997,'127.0.0.1',resolve));
  fs.writeFileSync(path.join(route,'page.tsx'),`"use client";\nimport DeveloperBriefing from "@/components/DeveloperBriefing";\nexport default function Page(){return <div className="service-workspace"><DeveloperBriefing repositories={${JSON.stringify(repos)}}/></div>;}\n`);
  const env={DATABASE_URL:`sqlite+pysqlite:///${path.join(state,'app.sqlite')}`,AUTH_REQUIRED:'false',APP_ENV:'development',DEV_USER_ID:'briefing-browser-user',DEV_USER_EMAIL:'briefing@example.test',TOKEN_ENCRYPTION_KEY:randomBytes(32).toString('base64url')+'=',HERMES_BRIEFING_ENABLED:'true',HERMES_BASE_URL:'http://127.0.0.1:8997/hermes/v1',HERMES_API_KEY:'fixture-hermes-key',HERMES_OWNER_USER_ID:'briefing-browser-user',GITHUB_API_BASE_URL:'http://127.0.0.1:8997/gh',GMAIL_BACKGROUND_MONITOR_ENABLED:'false',CALENDAR_BACKGROUND_MONITOR_ENABLED:'false',GITHUB_BACKGROUND_MONITOR_ENABLED:'false',ENABLE_EXTERNAL_ACTIONS:'false',FRONTEND_ORIGINS:'http://127.0.0.1:3119',ALL_PROXY:'',HTTP_PROXY:'',HTTPS_PROXY:'',all_proxy:'',http_proxy:'',https_proxy:''};
  const python=`import json\nfrom app.db import init_db,SessionLocal\nfrom app.config import get_settings\nfrom app.security.tokens import TokenCipher\nfrom app.services.connection_store import ConnectionStore\ninit_db()\nsettings=get_settings()\nwith SessionLocal() as db:\n store=ConnectionStore(db)\n store.ensure_user(user_id=settings.dev_user_id,email=settings.dev_user_email)\n store.upsert(user_id=settings.dev_user_id,provider="github",provider_account_id="fixture",display_name="Fixture",scopes=["repo"],credential_ciphertext=TokenCipher(settings.token_encryption_key).encrypt(json.dumps({"access_token":"fixture-github-token"})),token_expires_at=None)\nimport uvicorn\nuvicorn.run("app.main:app",host="127.0.0.1",port=8000)\n`;
  start(process.env.PYTHON||'python',['-c',python],path.dirname(web),env);
  start(process.execPath,[path.join(web,'node_modules/next/dist/bin/next'),'dev','--hostname','127.0.0.1','--port','3119'],web,{NEXT_PUBLIC_JIRAYA_API_URL:'http://127.0.0.1:8000'});
  const url='http://127.0.0.1:3119/briefing-regression-harness';
  for(let attempt=0;;attempt++){
   try{const [front,back]=await Promise.all([fetch(url),fetch('http://127.0.0.1:8000/github/briefing/status')]);if(front.ok&&back.ok)break;}catch{}
   if(attempt>120)throw Error(`Test services failed to start:\n${logs}`);await new Promise(resolve=>setTimeout(resolve,500));
  }
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{})});
  const page=await browser.newPage({viewport:{width:1280,height:1000}});await page.goto(url);
  await page.getByText('Hermes is configured.',{exact:false}).waitFor();
  assert.equal(turns.length,0,'Opening the panel must not invoke inference');
  const generate=page.getByRole('button',{name:'Generate developer briefing',exact:true});assert.equal(await generate.isDisabled(),true);
  await page.getByLabel('owner/jiraya',{exact:true}).check();await page.getByLabel('owner/notebook',{exact:true}).check();
  await page.getByLabel('owner/voice',{exact:true}).check();assert.equal(await page.getByLabel('owner/portfolio',{exact:true}).isDisabled(),true);
  await page.getByLabel('owner/voice',{exact:true}).uncheck();
  await generate.click();await page.getByRole('heading',{name:'Hermes briefing',exact:true}).waitFor();
  assert.equal(turns.length,1);
  const evidence=JSON.parse(turns[0].messages[1].content);
  assert.deepEqual(evidence.repositories,['owner/jiraya','owner/notebook']);assert.equal(evidence.sources.length,6);
  assert(!JSON.stringify(turns[0]).includes('PRIVATE-BODY'));assert(!JSON.stringify(turns[0]).includes('fixture-github-token'));
  assert.equal(await page.evaluate(()=>window.briefingInjected),undefined,'Model text must not execute HTML');
  await page.getByText('Sources and coverage',{exact:true}).click();
  assert.equal(await page.locator('.briefing-result a').count(),6);
  assert.equal(calls.filter(call=>call.path.startsWith('/gh/')&&call.method!=='GET').length,0);
  console.log('PASS: real frontend/backend, encrypted GitHub connection, two-repository snapshot and Hermes HTTP adapter');
  await page.screenshot({path:'/tmp/jiraya-briefing-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'/tmp/jiraya-briefing-mobile.png',fullPage:true});
  fail=true;await generate.click();await page.getByRole('alert').filter({hasText:'Hermes request was rejected'}).waitFor();
  assert.equal(await page.getByRole('heading',{name:'Hermes briefing',exact:true}).count(),0);
  assert.equal(await page.getByText('PRIVATE-UPSTREAM-ERROR',{exact:false}).count(),0);
  assert.equal(turns.length,2,'No automatic model retries');
  console.log('PASS: selection limit, no inference on page load, safe rendering, sources, mobile and error recovery');
 }finally{
  fs.rmSync(route,{recursive:true,force:true});fs.rmSync(state,{recursive:true,force:true});
  fs.rmSync(path.join(web,'.next','types','app','briefing-regression-harness'),{recursive:true,force:true});
  if(browser)await browser.close();
  for(const child of processes){try{if(process.platform==='win32')spawn('taskkill',['/PID',String(child.pid),'/T','/F'],{stdio:'ignore'});else process.kill(-child.pid,'SIGTERM');}catch{}child.stdout.destroy();child.stderr.destroy();child.unref();}
  if(server)await new Promise(resolve=>server.close(resolve));
 }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
