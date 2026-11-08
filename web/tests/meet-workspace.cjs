// Isolated browser regression: real Next component, controlled cloud/device responses.
// No Google sign-in, live meeting joins, or GitHub writes are performed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
async function main() {
  const web=path.resolve(__dirname,'..'), harness=path.join(web,'app','meet-regression-harness');
  fs.mkdirSync(harness);
  fs.writeFileSync(path.join(harness,'page.tsx'), `"use client";
import {useState} from "react";
import MeetWorkspace from "@/components/MeetWorkspace";
export default function Page(){const [connected,setConnected]=useState(true);return <><button onClick={()=>setConnected(v=>!v)}>Toggle Calendar fixture</button><MeetWorkspace accountId="meet-user" calendarConnected={connected} onConnectCalendar={()=>setConnected(true)}/></>;}`);
  let browser, server, logs='';
  try {
    server=spawn(process.execPath,[path.join(web,'node_modules/next/dist/bin/next'),'dev','--hostname','127.0.0.1','--port','3125'],{cwd:web,detached:true,env:{...process.env,NEXT_PUBLIC_JIRAYA_API_URL:'/api/jiraya'},stdio:['ignore','pipe','pipe']});
    server.stdout.on('data',v=>logs+=v);server.stderr.on('data',v=>logs+=v);
    const url='http://127.0.0.1:3125/meet-regression-harness';
    for(let n=0;;n++){try{if((await fetch(url)).ok)break;}catch{}if(n>100)throw Error(logs);await new Promise(r=>setTimeout(r,300));}
    const launch={headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{})};
    if(process.env.JIRAYA_SPARTICUZ){const {default:binary}=await import(process.env.JIRAYA_SPARTICUZ);launch.executablePath=await binary.executablePath();launch.args=binary.args;}
    browser=await chromium.launch(launch);
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    let feedStatus='READY', helperStatus=200, offline=false, gitRequests=0, registrations=0, connects=0;
    let device={configured:false,enabled:false,active:false,account_id:null,last_check:null,error:null,recent:[]};
    await page.route('**/api/jiraya/meet/**',async route=>{
      if(route.request().url().includes('/meet/cloud/status'))return route.fulfill({json:{configured:false,worker_online:false,enabled:false,preferences:{},google_transcript_authorized:false}});
      if(route.request().url().includes('/meet/cloud/sessions'))return route.fulfill({json:{items:[]}});
      if(route.request().url().endsWith('/meet/devices')){registrations++;return route.fulfill({json:{token:'scoped-fixture-token'}});}
      return route.fulfill({json:{status:feedStatus,calendar_account:'calendar@example.test',items:feedStatus==='READY'?[{id:'meeting',summary:'Fixture linked meeting',url:'https://meet.google.com/abc-defg-hij',start:'2026-11-08T12:30:00Z',end:'2026-11-08T13:30:00Z'}]:[]}});
    });
    await page.route('http://127.0.0.1:8765/**',async route=>{
      const req=route.request(), endpoint=new URL(req.url()).pathname;
      if(offline)return route.abort();
      const headers={'Access-Control-Allow-Origin':'http://127.0.0.1:3125','Access-Control-Allow-Headers':'Authorization,Content-Type','Access-Control-Allow-Methods':'GET,POST,OPTIONS','Access-Control-Allow-Private-Network':'true'};
      const respond=(json,status=200)=>route.fulfill({status,headers,json});
      if(req.method()==='OPTIONS')return respond({});
      if(endpoint.startsWith('/projects')||endpoint.startsWith('/checkpoints'))gitRequests++;
      if(req.headers().authorization!=='Bearer pairing-fixture')return respond({detail:'Rejected token'},401);
      if(endpoint==='/health')return respond({service:'jiraya-local-companion'});
      if(endpoint==='/meeting/status')return respond(helperStatus===200?device:{detail:'Old helper'},helperStatus);
      if(endpoint==='/meeting/connect'){
        connects++;assert.equal(req.postDataJSON().device_token,'scoped-fixture-token');
        assert.equal(req.postDataJSON().api_base,'http://127.0.0.1:3125/api/jiraya');
        device={...device,configured:true,enabled:true,account_id:'meet-user',calendar_account:'calendar@example.test',recent:[]};return respond(device);
      }
      if(endpoint==='/meeting/pause'){device={...device,configured:false,enabled:false};return respond(device);}
      if(endpoint==='/meeting/retry'){assert.equal(req.postDataJSON().key,'retry-key');device.recent[0].status='RETRY_REQUESTED';return respond(device);}
      return respond({detail:'Unexpected route'},404);
    });
    await page.goto(url);
    await page.getByRole('button',{name:'Use this computer instead',exact:true}).click();
    const pairing=page.getByRole('region',{name:'Meet device pairing'});
    await pairing.getByRole('button',{name:'Connect with VS Code',exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:/Pair this computer in GitHub/}).count(),0);
    assert.equal(await page.getByRole('button',{name:'Enable automatic attendance',exact:true}).isEnabled(),false);
    await pairing.getByText('Manual pairing / custom port',{exact:true}).click();
    await pairing.getByLabel('Pairing token',{exact:true}).fill('pairing-fixture');
    await pairing.getByRole('button',{name:'Connect companion',exact:true}).click();
    await pairing.getByText('Companion connected',{exact:true}).waitFor();
    await page.getByText('Fixture linked meeting',{exact:true}).waitFor();
    const consent=page.getByRole('checkbox',{name:/I authorize this computer/});
    const enable=page.getByRole('button',{name:'Enable automatic attendance',exact:true});
    await consent.check();await enable.click();
    await page.getByRole('button',{name:'Pause attendance and leave',exact:true}).waitFor();
    assert.equal(connects,1);assert.equal(registrations,1);assert.equal(gitRequests,0);
    await page.reload();await page.getByRole('button',{name:'Use this computer instead',exact:true}).click();await page.getByRole('button',{name:'Pause attendance and leave',exact:true}).waitFor();
    assert.equal(connects,1,'Reload must reuse pairing and not register another device');
    device.recent=[{key:'retry-key',event_id:'meeting',status:'CLOSED',reason:'BROWSER_WINDOW_CLOSED',start:'2026-11-08T12:30:00Z'}];
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.getByRole('button',{name:'Retry attendance',exact:true}).click();
    await page.getByText('RETRY REQUESTED',{exact:false}).waitFor();
    await page.getByRole('button',{name:'Renew device authorization',exact:true}).click();
    await consent.check();await enable.click();await page.getByRole('button',{name:'Pause attendance and leave',exact:true}).waitFor();
    await page.getByRole('button',{name:'Pause attendance and leave',exact:true}).click();await enable.waitFor();
    assert.equal(await consent.isChecked(),false);
    device={...device,configured:true,enabled:true,account_id:'meet-user',calendar_account:'old-calendar@example.test'};
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.getByRole('alert').filter({hasText:/different account. Enable attendance again/}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Pause attendance and leave',exact:true}).count(),0);
    device={...device,configured:true,enabled:true,account_id:'other-user',recent:[{status:'FAILED',start:'2026-11-08T12:30:00Z',reason:'PRIVATE-OTHER-ACCOUNT'}]};
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.getByRole('alert').filter({hasText:/another Jiraya account/}).waitFor();
    assert.equal(await page.getByText('PRIVATE-OTHER-ACCOUNT',{exact:false}).count(),0);
    assert.equal(await page.getByRole('button',{name:'Pause attendance and leave',exact:true}).count(),0);
    await consent.check();feedStatus='EXTERNAL_ACTIONS_DISABLED';
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.getByRole('alert').filter({hasText:/disabled on the server/}).waitFor();
    assert.equal(await enable.isEnabled(),false);
    feedStatus='READY';helperStatus=404;await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.getByRole('alert').filter({hasText:/does not support Meet/}).waitFor();assert.equal(await enable.isEnabled(),false);
    helperStatus=200;offline=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.getByRole('alert').filter({hasText:/Local helper is unreachable/}).waitFor();assert.equal(await enable.isEnabled(),false);
    offline=false;await page.getByRole('button',{name:'Toggle Calendar fixture',exact:true}).click();
    await page.getByRole('button',{name:'Connect Google Calendar',exact:true}).click();
    await page.getByRole('button',{name:'Connect Google Calendar',exact:true}).waitFor({state:'detached'});
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await pairing.getByRole('button',{name:'Forget pairing',exact:true}).click();
    await pairing.getByRole('button',{name:'Connect with VS Code',exact:true}).waitFor();
    await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='Enable automatic attendance')?.disabled);
    assert.equal(await enable.isEnabled(),false);
    await page.setViewportSize({width:390,height:844});
    const width=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,viewport:innerWidth}));
    assert(width.scroll<=width.viewport,'Meet setup should fit a mobile viewport');
    console.log('PASS: Meet-local pairing, no GitHub dependency, consent, enable, persistence, retry, renewal, pause, account isolation, server gate, old/offline helper, Calendar connection and mobile layout');
  } finally {
    if(browser)await browser.close();
    if(server){try{process.kill(-server.pid,'SIGTERM');}catch{}server.stdout.destroy();server.stderr.destroy();server.unref();}
    fs.rmSync(harness,{recursive:true,force:true});
    fs.rmSync(path.join(web,'.next','types','app','meet-regression-harness'),{recursive:true,force:true});
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
