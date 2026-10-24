// Browser check for the opt-in settings and Calendar context node; no provider calls.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
async function main(){
 const web=path.resolve(__dirname,'..'),route=path.join(web,'app','scheduling-regression-harness');fs.mkdirSync(route);
 let server,browser,logs='';
 try{
  fs.writeFileSync(path.join(route,'page.tsx'),`"use client";
import {useState} from "react";
import SchedulingSettings from "@/components/SchedulingSettings";
import AutomationCanvas from "@/components/AutomationCanvas";
import {routeReady} from "@/lib/automation-graph";
import type {AutomationInput,ConnectionCatalogItem} from "@/lib/api";
export default function Page(){
 const [value,setValue]=useState<Record<string,unknown>>({});const [connected,setConnected]=useState(true);
 const draft:AutomationInput={name:"Scheduling",description:"",enabled:true,source:"gmail",trigger_type:"NEW_EMAIL",approval_policy:"DRAFT_ONLY",match_config:{},context_config:{},action_config:{type:"DRAFT_REPLY",calendar_scheduling:value}};
 const connections=[{id:"gmail",status:"CONNECTED"},...(connected?[{id:"google_calendar",status:"CONNECTED"}]:[])] as ConnectionCatalogItem[];
 return <main className="service-workspace"><div style={{maxWidth:390}}><SchedulingSettings value={value} connected={connected} onChange={setValue}/><button onClick={()=>setConnected(!connected)}>Toggle Calendar connection</button><button disabled={!routeReady(draft,connections)}>Save fixture</button><output aria-label="Saved scheduling settings">{JSON.stringify(value)}</output></div><div style={{height:700}}><AutomationCanvas draft={draft} connections={connections} onSource={()=>{}} onAction={()=>{}}/></div></main>;
}
`);
  server=spawn(process.execPath,[path.join(web,'node_modules/next/dist/bin/next'),'dev','--hostname','127.0.0.1','--port','3120'],{cwd:web,detached:process.platform!=='win32',stdio:['ignore','pipe','pipe']});server.stdout.on('data',data=>logs+=data);server.stderr.on('data',data=>logs+=data);
  const url='http://127.0.0.1:3120/scheduling-regression-harness';
  for(let attempt=0;;attempt++){try{if((await fetch(url)).ok)break;}catch{}if(attempt>120)throw Error(logs);await new Promise(resolve=>setTimeout(resolve,500));}
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{})});
  const context=await browser.newContext({timezoneId:'Asia/Kolkata',viewport:{width:1280,height:1100}}),page=await context.newPage();await page.goto(url);
  const toggle=page.getByRole('checkbox',{name:'Check availability and suggest meeting times',exact:true});
  assert.equal(await toggle.isChecked(),false);await toggle.check();
  await page.getByLabel('Scheduling timezone',{exact:true}).fill('Asia/Kolkata');
  await page.getByLabel('Working day starts',{exact:true}).fill('10:00');
  await page.getByLabel('Default meeting minutes',{exact:true}).fill('45');
  await page.getByLabel('Scheduling calendar IDs',{exact:true}).fill('primary, work@example.test');
  assert.equal(await page.getByRole('checkbox',{name:'Use my scheduling timezone when the email omits one',exact:true}).isChecked(),false);
  const saved=JSON.parse(await page.getByLabel('Saved scheduling settings').textContent());assert.equal(saved.enabled,true);assert.equal(saved.duration_minutes,45);assert.equal(saved.work_start,'10:00');assert.deepEqual(saved.calendar_ids,['primary','work@example.test']);
  await page.getByText('Check Calendar availability',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Toggle Calendar connection',exact:true}).click();assert.equal(await page.getByRole('button',{name:'Save fixture',exact:true}).isDisabled(),true);
  await toggle.uncheck();assert.equal(await page.getByRole('button',{name:'Save fixture',exact:true}).isEnabled(),true);
  assert.equal(await page.getByText('Check Calendar availability',{exact:true}).count(),0);
  console.log('PASS: scheduling opt-in, explicit preferences, default timezone review, required Calendar and context graph');
 }finally{
  fs.rmSync(route,{recursive:true,force:true});fs.rmSync(path.join(web,'.next','types','app','scheduling-regression-harness'),{recursive:true,force:true});
  if(browser)await browser.close();if(server){try{if(process.platform==='win32')spawn('taskkill',['/PID',String(server.pid),'/T','/F'],{stdio:'ignore'});else process.kill(-server.pid,'SIGTERM');}catch{}server.stdout.destroy();server.stderr.destroy();server.unref();}
 }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
