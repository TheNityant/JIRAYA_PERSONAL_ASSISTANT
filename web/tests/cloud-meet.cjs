// Controlled provider fixtures; no external writes or live meeting joins.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
async function main(){
 const web=path.resolve(__dirname,'..'),harness=path.join(web,'app','cloud-meet-harness');
 fs.mkdirSync(harness);fs.writeFileSync(path.join(harness,'page.tsx'),`"use client";import {useState} from "react";import CloudMeetWorkspace from "@/components/CloudMeetWorkspace";export default function Page(){const [connected,setConnected]=useState(false);return <CloudMeetWorkspace calendarConnected={connected} onConnectCalendar={()=>setConnected(true)} onLocal={()=>{}}/>}`);
 let server,browser,logs='';
 try{
  server=spawn(process.execPath,[path.join(web,'node_modules/next/dist/bin/next'),'dev','--hostname','127.0.0.1','--port','3126'],{cwd:web,detached:true,env:{...process.env,NEXT_PUBLIC_JIRAYA_API_URL:'/api/jiraya'},stdio:['ignore','pipe','pipe']});server.stdout.on('data',v=>logs+=v);server.stderr.on('data',v=>logs+=v);
  const url='http://127.0.0.1:3126/cloud-meet-harness';
  for(let n=0;;n++){try{if((await fetch(url)).ok)break;}catch{}if(n>100)throw Error(logs);await new Promise(r=>setTimeout(r,300));}
  const launch={headless:true};if(process.env.JIRAYA_SPARTICUZ){const {default:binary}=await import(process.env.JIRAYA_SPARTICUZ);launch.executablePath=await binary.executablePath();launch.args=binary.args;}else if(process.env.CHROMIUM_EXECUTABLE_PATH)launch.executablePath=process.env.CHROMIUM_EXECUTABLE_PATH;
  browser=await chromium.launch(launch);const page=await browser.newPage({viewport:{width:1440,height:1000}});
  let status={configured:false,worker_online:false,enabled:false,google_transcript_authorized:false,preferences:{}},writes=[],rows=[];
  await page.route('**/api/jiraya/meet/cloud/**',async route=>{
   const req=route.request(),relative=new URL(req.url()).pathname.split('/meet/cloud/')[1];
   if(relative==='status')return route.fulfill({json:status});
   if(relative==='diagnostics')return route.fulfill({json:{worker_online:true,worker_last_seen:new Date().toISOString(),identity_mode:'anonymous_guest',display_name:'Jiraya · AI meeting notes',checks:[{name:'Calendar connected and readable',ok:true}],calendar_status:'READY',eligible_events:1,events_in_join_window:0,next_start:new Date().toISOString(),message:'Cloud attendance uses an anonymous guest, not your Google account.'}});
   if(relative==='settings'){const body=req.postDataJSON();writes.push(body);assert(body.enabled===false||body.consent===true);status={...status,enabled:body.enabled,preferences:body};return route.fulfill({json:status});}
   if(relative==='sessions')return route.fulfill({json:{items:rows}});
   if(req.method()==='DELETE'){rows=[];return route.fulfill({json:{deleted:true}});}
   if(relative.endsWith('/stop')){writes.push('stop');rows[0].stop_requested=true;return route.fulfill({json:{stop_requested:true}});}
   if(relative.endsWith('/summary')){writes.push('summary');return route.fulfill({json:{queued:true}});}
   return route.fulfill({json:{...rows[0],prejoin_debug:rows[0].status==='FAILED'?{captured_at:new Date().toISOString(),controls:['Use microphone and camera'],capture_note:'Controlled screenshot fixture'}:undefined,summary:'{"overview":"We agreed to ship the fix."}',transcript:[{speaker:'unknown',text:'Ship the fix',at:'fixture'}]}});
  });
  await page.goto(url);await page.getByRole('heading',{name:'Cloud Meet attendance · beta',exact:true}).waitFor();
  await page.getByRole('button',{name:'Run Meet checks',exact:true}).click();
  await page.getByText('Ready: Calendar connected and readable',{exact:true}).waitFor();
  assert.equal(writes.length,0,'Diagnostics must not enable or join a meeting');
  const enable=page.getByRole('button',{name:'Enable cloud attendance',exact:true});assert.equal(await enable.isEnabled(),false);assert.equal(writes.length,0,'Viewing cloud controls must not enable attendance');
  await page.getByText('Cloud attendance is not configured.',{exact:false}).waitFor();
  status.configured=true;await page.getByRole('button',{name:'Refresh cloud status',exact:true}).click();await page.getByText('The cloud worker is offline.',{exact:false}).waitFor();
  status.worker_online=true;await page.getByRole('button',{name:'Refresh cloud status',exact:true}).click();await page.getByText('Cloud worker online',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Connect Google Calendar',exact:true}).click();assert.equal(await enable.isEnabled(),false);
  status.identity_mode='google_profile';status.browser_connection={state:'SETUP_REQUIRED',last_verified_at:null,requires_interactive_setup:true};
  await page.getByRole('button',{name:'Refresh cloud status',exact:true}).click();await page.getByRole('heading',{name:'Meeting browser setup required',exact:true}).waitFor();
  await page.getByRole('checkbox',{name:/I have permission to add a transcription bot/}).check();assert.equal(await enable.isEnabled(),false,'OAuth and online worker cannot substitute for browser setup');
  status.browser_connection={state:'CONNECTED',last_verified_at:new Date().toISOString(),requires_interactive_setup:false};
  await page.getByRole('button',{name:'Refresh cloud status',exact:true}).click();await page.getByRole('heading',{name:'Meeting browser connected',exact:true}).waitFor();assert.equal(await enable.isEnabled(),true);
  status.browser_connection.state='RECONNECT_REQUIRED';
  await page.getByRole('button',{name:'Refresh cloud status',exact:true}).click();await page.getByRole('heading',{name:'Reconnect your meeting browser',exact:true}).waitFor();assert.equal(await enable.isEnabled(),false);
  status.identity_mode='guest';await page.getByRole('button',{name:'Refresh cloud status',exact:true}).click();
  await page.getByRole('checkbox',{name:/I have permission to add a transcription bot/}).check();await enable.click();
  await page.getByRole('button',{name:'Pause cloud attendance and leave',exact:true}).waitFor();assert.equal(writes.length,1);
  rows=[{id:'fixture',title:'Test meeting',start:'2026-11-08T10:00:00Z',end:'2026-11-08T11:00:00Z',status:'JOINED',stop_requested:false,has_transcript:true,details:{summary_status:'PENDING'}}];
  await page.getByRole('button',{name:'Refresh cloud status',exact:true}).click();await page.getByRole('button',{name:'Leave now',exact:true}).click();assert(writes.includes('stop'));
  await page.getByRole('button',{name:'Pause cloud attendance and leave',exact:true}).click();await enable.waitFor();assert.equal(await page.getByRole('checkbox',{name:/I have permission/}).isChecked(),false);
  rows[0].status='COMPLETED';rows[0].details={summary_status:'READY',summary_source:'assemblyai',coverage:'captured while admitted'};
  await page.getByRole('button',{name:'Refresh cloud status',exact:true}).click();await page.getByRole('button',{name:'View notes',exact:true}).click();await page.getByText('We agreed to ship the fix.',{exact:false}).waitFor();
  await page.getByRole('button',{name:'Retry summary / check Google transcript',exact:true}).click();assert(writes.includes('summary'));
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.getByRole('button',{name:'Delete stored notes',exact:true}).click();await page.getByText('No cloud sessions yet.',{exact:false}).waitFor();
  assert.equal(await page.getByText('We agreed to ship the fix.',{exact:false}).count(),0);
  rows=[{id:'failed',title:'Failed media fixture',start:new Date().toISOString(),end:new Date().toISOString(),status:'FAILED',stop_requested:false,has_transcript:false,details:{reason:'MEDIA_NOT_CONFIRMED_OFF',summary_status:'NO_ATTENDANCE',media_checks:['mic_muted_confirmed_off','camera_off_not_confirmed_off']}}];
  await page.getByRole('button',{name:'Refresh cloud status',exact:true}).click();
  await page.getByText('Notes: No notes — assistant did not join',{exact:true}).waitFor();
  await page.getByText('Media checks: mic muted confirmed off; camera off not confirmed off',{exact:true}).waitFor();
  await page.getByRole('button',{name:'View notes',exact:true}).click();
  await page.getByText('Failed pre-join screen · private diagnostic',{exact:true}).waitFor();
  await page.getByText('Controlled screenshot fixture',{exact:true}).waitFor();
  console.log('PASS: cloud readiness, Calendar/consent gates, enable/pause/stop, notes, summary retry, deletion, mobile');
 }finally{if(browser)await browser.close();if(server){try{process.kill(-server.pid,'SIGTERM');}catch{}server.stdout.destroy();server.stderr.destroy();server.unref();}fs.rmSync(harness,{recursive:true,force:true});fs.rmSync(path.join(web,'.next','types','app','cloud-meet-harness'),{recursive:true,force:true});}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
