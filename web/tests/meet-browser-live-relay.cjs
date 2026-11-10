// Invoked by the Python integration test with a real local API/worker and controlled Google pages.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
async function main(){
 const web=path.resolve(__dirname,'..'),harness=path.join(web,'app','meet-browser-relay-harness');
 fs.mkdirSync(harness);fs.writeFileSync(path.join(harness,'page.tsx'),`"use client";import CloudMeetWorkspace from "@/components/CloudMeetWorkspace";export default function Page(){return <CloudMeetWorkspace calendarConnected={true} onConnectCalendar={()=>{}} onLocal={()=>{}}/>}`);
 let server,browser,logs='';
 try{
  server=spawn(process.execPath,[path.join(web,'node_modules/next/dist/bin/next'),'dev','--hostname','127.0.0.1','--port','3131'],{cwd:web,detached:true,env:{...process.env,JIRAYA_BACKEND_URL:process.env.PORTAL_FIXTURE_API,NEXT_PUBLIC_JIRAYA_API_URL:'/api/jiraya'},stdio:['ignore','pipe','pipe']});server.stdout.on('data',v=>logs+=v);server.stderr.on('data',v=>logs+=v);
  const url='http://127.0.0.1:3131/meet-browser-relay-harness';
  for(let n=0;;n++){try{if((await fetch(url)).ok)break;}catch{}if(n>100)throw Error(logs);await new Promise(r=>setTimeout(r,300));}
  const binary=JSON.parse(fs.readFileSync(process.env.JIRAYA_CHROMIUM_CONFIG,'utf8'));
  browser=await chromium.launch({headless:true,executablePath:binary.executablePath,args:binary.args.filter(arg=>arg!=="--disable-web-security")});
  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  await context.addCookies([{name:'jiraya_session',value:process.env.PORTAL_FIXTURE_COOKIE,url:'http://127.0.0.1:3131',httpOnly:true,sameSite:'Lax'}]);
  const page=await context.newPage();await page.goto(url);
  await page.getByRole('heading',{name:'Meeting browser setup required',exact:true}).waitFor();console.log('fixture: frontend ready');
  const enable=page.getByRole('button',{name:'Enable cloud attendance',exact:true});assert.equal(await enable.isEnabled(),false);
  await page.getByRole('checkbox',{name:/I trust this Jiraya server/}).check();
  const started=page.waitForResponse(r=>r.url().endsWith('/meet/cloud/browser/sessions')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Open Google meeting browser',exact:true}).click();
  const startResponse=await started;assert.equal(startResponse.status(),200,await startResponse.text());
  const image=page.getByAltText('Your temporary Google sign-in browser on AWS');await image.waitFor({timeout:30000});console.log('fixture: remote image ready');
  await page.getByText('https://accounts.google.com',{exact:true}).waitFor();
  const rect=await image.boundingBox();assert(rect);
  await image.click({position:{x:150/1100*rect.width,y:125/740*rect.height}});
  await page.getByLabel('Text for the selected Google field',{exact:true}).fill('fixture-signin-only');
  await page.getByRole('button',{name:'Send text to Google field',exact:true}).click();
  assert.equal(await page.getByLabel('Text for the selected Google field',{exact:true}).inputValue(),'');
  await page.getByRole('button',{name:'Press Enter in Google browser',exact:true}).click();console.log('fixture: input submitted');
  await page.getByText('Google browser connected and reuse verified.',{exact:false}).waitFor({timeout:60000});
  await page.getByRole('heading',{name:'Meeting browser connected',exact:true}).waitFor();
  assert.equal(await image.count(),0,'Connected desktops must remove the screen from the frontend');
  await page.getByRole('checkbox',{name:/I have permission to add a transcription bot/}).check();
  await enable.click();await page.getByText('Cloud attendance enabled',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  console.log('PASS: real frontend proxy + cookie-authenticated API + encrypted database relay + worker browser input + persistent profile verification + enable + mobile');
 }finally{
  if(browser)await browser.close();if(server){try{process.kill(-server.pid,'SIGTERM');}catch{}server.stdout.destroy();server.stderr.destroy();server.unref();}
  fs.rmSync(harness,{recursive:true,force:true});fs.rmSync(path.join(web,'.next','types','app','meet-browser-relay-harness'),{recursive:true,force:true});
 }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
