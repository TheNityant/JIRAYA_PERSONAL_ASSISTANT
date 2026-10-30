// Real Next frontend + proxy, controlled provider/companion fixtures. No external writes.
const http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
async function main(){
 const web=path.resolve(__dirname,'..'),frontend='http://127.0.0.1:3124';
 const user={id:'saved-user',email:'fixture@example.test',display_name:'Fixture'};
 let signedIn=true,disconnected=false,server,browser,logs='',claims=0;
 const calls=[];
 const rule=(id,name,source)=>({id,name,description:'',source,enabled:true,trigger_type:source==='gmail'?'NEW_EMAIL':'GITHUB_PR_CHANGED',match_config:{},context_config:{},action_config:source==='gmail'?{type:'CREATE_CALENDAR_EVENT',time_zone:'Asia/Kolkata',default_event_minutes:60}:{type:'NOTIFY',repository:'fixture/repo'},approval_policy:'REQUIRED',system_pipeline:[],created_at:'2026-10-30T10:00:00Z',updated_at:'2026-10-30T10:00:00Z'});
 const rules=[rule('github-rule','Saved GitHub watcher','github'),rule('calendar-rule','Saved email to Calendar','gmail')];
 let execution={id:'saved-execution',automation_id:'calendar-rule',source:'gmail',source_event_id:'prior-email',action_type:'CREATE_CALENDAR_EVENT',approval_policy:'REQUIRED',status:'FALLBACK_NOTIFICATION',draft_body:'',output:{stage:'EVENT_VALIDATION',reason_code:'EVENT_DETAILS_REQUIRED',reason:'Fixture: missing meeting start time'},created_at:'2026-10-30T10:00:00Z'};
 const backend=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://fixture');let body='';for await(const chunk of req)body+=chunk;
  const json=(data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  if(url.pathname==='/auth/me')return json({user:signedIn?user:null,auth_required:true,registration_enabled:true});
  if(url.pathname==='/auth/logout'){signedIn=false;return json({signed_out:true});}
  if(url.pathname==='/auth/login'){signedIn=true;return json({user});}
  if(!signedIn)return json({detail:'Sign in'},401);
  if(url.pathname==='/connections')return json({catalog:['gmail','google_calendar','github'].map(id=>({id,provider:id,label:id,status:disconnected?'AVAILABLE':'CONNECTED',account:'fixture@example.test',connect_path:'/fixture'}))});
  if(url.pathname==='/automations')return json(rules);
  if(url.pathname==='/automation-executions')return json(url.searchParams.get('automation_id')==='calendar-rule'?[execution]:[]);
  if(url.pathname==='/gmail/inbox/recent')return json({items:[{id:'chosen-email',subject:'Chosen interview',sender:'sender@example.test'}]});
  if(url.pathname.endsWith('/preview/gmail')){calls.push({path:url.pathname,body:JSON.parse(body)});return json({message:{id:'chosen-email',subject:'Chosen interview'},match:{matches:true,confidence:1,reason:'Fixture match'}});}
  if(url.pathname.endsWith('/run/gmail')){calls.push({path:url.pathname,body:JSON.parse(body)});execution={...execution,status:'AWAITING_APPROVAL',source_event_id:JSON.parse(body).message_id,output:{proposal:{summary:'Fixture interview',start:'2026-11-02T15:00:00+05:30',end:'2026-11-02T16:00:00+05:30'}}};return json({decision:'AWAITING_APPROVAL',execution});}
  if(url.pathname==='/notifications')return json({items:[],unread_count:0});
  if(url.pathname==='/obligations')return json([]);
  if(url.pathname==='/runtime/gmail/status')return json({background_monitor_enabled:true,background_monitor_configured:true,poll_seconds:15,mailbox:user.email});
  if(url.pathname==='/github/status')return json({connected:true,account:'fixture',scopes:[],health:{}});
  if(url.pathname==='/github/briefing/status')return json({configured:false,message:'Not configured'});
  if(url.pathname==='/github/repos')return json({items:[]});
  return json({detail:'Unknown fixture route'},404);
 });
 const deviceToken='isolated-browser-test-device';
 const companion=http.createServer(async(req,res)=>{
  res.setHeader('Access-Control-Allow-Origin',frontend);res.setHeader('Access-Control-Allow-Headers','Authorization,Content-Type');res.setHeader('Access-Control-Allow-Methods','GET,POST,OPTIONS');res.setHeader('Access-Control-Allow-Private-Network','true');
  if(req.method==='OPTIONS'){res.writeHead(200);return res.end();}
  const json=(data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  if(req.url==='/pairing/claim'){claims++;return json({service:'jiraya-local-companion',token:deviceToken});}
  if(req.headers.authorization!==`Bearer ${deviceToken}`)return json({detail:'Bad fixture token'},401);
  if(req.url==='/projects')return json({items:['first','second'].map((id,index)=>({id,path:`/fixture/${id}`,repository:`fixture/${id}`,branch:index?'develop':'main',target_branch:index?'develop':'main',enabled:true,files:[]})),registration:{session_count:2,items:[]}});
  if(req.url==='/checkpoints/latest')return json(null);
  return json({service:'jiraya-local-companion'});
 });
 await new Promise(resolve=>backend.listen(0,'127.0.0.1',resolve));await new Promise(resolve=>companion.listen(0,'127.0.0.1',resolve));
 try{
  server=spawn(process.execPath,[path.join(web,'node_modules/next/dist/bin/next'),'dev','--hostname','127.0.0.1','--port','3124'],{cwd:web,detached:true,env:{...process.env,JIRAYA_BACKEND_URL:`http://127.0.0.1:${backend.address().port}`,NEXT_PUBLIC_JIRAYA_API_URL:'/api/jiraya'},stdio:['ignore','pipe','pipe']});
  server.stdout.on('data',v=>logs+=v);server.stderr.on('data',v=>logs+=v);
  for(let i=0;;i++){try{if((await fetch(frontend+'/api/jiraya/auth/me')).ok)break;}catch{}if(i>100)throw Error(logs);await new Promise(r=>setTimeout(r,300));}
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  await page.goto(frontend);await page.getByRole('button',{name:/Saved email to Calendar/}).click();
  await page.getByRole('button',{name:'Debug · Run latest',exact:true}).waitFor();
  await page.getByText('Fixture: missing meeting start time',{exact:true}).waitFor();
  if(process.env.JIRAYA_UI_SCREENSHOT)await page.screenshot({path:process.env.JIRAYA_UI_SCREENSHOT});
  await page.getByRole('button',{name:'Retry this email',exact:true}).click();
  await page.getByRole('button',{name:'Approve & Create event',exact:true}).waitFor();
  assert.deepEqual(calls.at(-1).body,{message_id:'prior-email',force:true});
  await page.getByRole('button',{name:'Choose inbox email',exact:true}).click();
  await page.getByRole('combobox',{name:'Debug source email'}).selectOption('chosen-email');
  await page.getByRole('button',{name:'Debug · Test match',exact:true}).click();await page.getByText('Fixture match',{exact:false}).waitFor();
  await page.getByRole('button',{name:'Debug · Run selected email',exact:true}).click();
  await page.getByRole('button',{name:'Approve & Create event',exact:true}).waitFor();assert.deepEqual(calls.at(-1).body,{message_id:'chosen-email',force:false});
  await page.reload();await page.getByRole('heading',{name:'Saved email to Calendar',exact:true}).waitFor();await page.getByRole('button',{name:'Approve & Create event',exact:true}).waitFor();
  disconnected=true;await page.reload();await page.getByRole('heading',{name:'Saved email to Calendar',exact:true}).waitFor(); // Saved rules survive connector attention.
  disconnected=false;
  await page.evaluate(({port})=>sessionStorage.setItem('jiraya.companion.pending:saved-user',JSON.stringify({port:String(port),nonce:'a'.repeat(64),started:Date.now()})),{port:companion.address().port});
  await page.getByRole('button',{name:'GitHub',exact:true}).click();
  const panel=page.getByRole('region',{name:'Local workspace checkpoint'});
  await panel.getByText('Companion connected',{exact:true}).waitFor();await panel.getByText('2 open Git projects · 2 enabled',{exact:true}).waitFor();
  assert.equal(claims,1);assert.equal(await page.evaluate(()=>!!localStorage.getItem('jiraya.companion:saved-user')),true);
  await page.getByRole('button',{name:'Sign out',exact:true}).click();await page.getByRole('heading',{name:'Welcome back',exact:true}).waitFor();
  await page.getByLabel('Email',{exact:true}).fill(user.email);await page.getByLabel('Password',{exact:true}).fill('fixture-password-only');await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await page.getByRole('heading',{name:'Saved email to Calendar',exact:true}).waitFor();await page.getByRole('button',{name:'GitHub',exact:true}).click();
  await panel.getByText('2 open Git projects · 2 enabled',{exact:true}).waitFor();assert.equal(claims,1);
  user.id='another-user';await page.reload();await page.getByRole('button',{name:'GitHub',exact:true}).click();await panel.getByRole('button',{name:'Connect with VS Code',exact:true}).waitFor();assert.equal(claims,1);
  await context.close();console.log('PASS: visible debug, saved failure, exact-email retry/preview/run, approval, refresh, disconnected-account access, handoff, two repositories and pairing retained across logout/login and isolated from another account');
 }finally{
  if(browser)await browser.close();if(server){try{process.kill(-server.pid,'SIGTERM');}catch{}server.stdout.destroy();server.stderr.destroy();server.unref();}
  for(const service of [backend,companion]){service.closeAllConnections();await new Promise(resolve=>service.close(resolve));}
 }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
