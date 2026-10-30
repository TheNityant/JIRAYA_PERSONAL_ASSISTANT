// Run from web with Playwright + Chromium installed: node tests/calendar-browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
async function main() {
 const web=path.resolve(__dirname,'..'),route=path.join(web,'app','calendar-regression-harness');
 fs.mkdirSync(route); // Never overwrite an existing application route.
 let server,browser,logs='';
 try {
  fs.writeFileSync(path.join(route,'page.tsx'),'"use client";\nimport CalendarWorkspace from "@/components/CalendarWorkspace";\nexport default function Page(){return <CalendarWorkspace onCreateAutomation={()=>{}}/>;}\n');
  server=spawn(process.execPath,[path.join(web,'node_modules/next/dist/bin/next'),'dev','--hostname','127.0.0.1','--port','3118'],{cwd:web,detached:process.platform!=='win32',stdio:['ignore','pipe','pipe']});
  server.stdout.on('data',data=>logs+=data);server.stderr.on('data',data=>logs+=data);
  const url='http://127.0.0.1:3118/calendar-regression-harness';
  for(let attempt=0;;attempt++) {
   try {if((await fetch(url)).ok)break;}catch{}
   if(attempt>120)throw Error(`Test frontend did not start:\n${logs}`);
   await new Promise(resolve=>setTimeout(resolve,500));
  }
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{})});
  const context=await browser.newContext({timezoneId:'Asia/Kolkata',viewport:{width:1280,height:1000}}),page=await context.newPage();
  await page.clock.setFixedTime(new Date('2026-10-24T04:30:00Z'));
  let items=[{id:'birthday',summary:'Happy birthday!',recurringEventId:'annual',start:{date:'2026-10-25'},end:{date:'2026-10-26'},htmlLink:'https://calendar.google.com/'}];
  const requests=[];let fail=false;
  await page.route('http://127.0.0.1:8000/**',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(request.method()==='OPTIONS')return route.fulfill({status:204,headers:{'Access-Control-Allow-Origin':'http://127.0.0.1:3118','Access-Control-Allow-Credentials':'true','Access-Control-Allow-Headers':'content-type'}});
   const status={connected:true,account:'calendar-test@example.test',scopes:[],health:{},monitor:{last_decision:'NO_AUTOMATIONS'},background_monitor_configured:false};
   if(url.pathname==='/calendar/events')requests.push(url);
   await route.fulfill({status:fail&&url.pathname==='/calendar/events'?503:200,contentType:'application/json',headers:{'Access-Control-Allow-Origin':'http://127.0.0.1:3118','Access-Control-Allow-Credentials':'true'},body:JSON.stringify(url.pathname==='/calendar/status'?status:fail?{detail:'Calendar test failure'}:{items,limit_reached:false})});
  });
  await page.goto(url);
  const today=page.getByRole('region',{name:'Today events',exact:true}),upcoming=page.getByRole('region',{name:'Upcoming events',exact:true});
  await today.getByText('Nothing scheduled today',{exact:true}).waitFor();
  await upcoming.getByRole('heading',{name:'Happy birthday!',exact:true}).waitFor();
  assert.equal(await today.getByText('Happy birthday!',{exact:true}).count(),0);
  assert.equal(await page.locator('.service-stats').getByText('Happy birthday!',{exact:true}).count(),0);
  await upcoming.getByRole('heading',{name:/Tomorrow/}).waitFor();
  assert.equal(new Date(requests[0].searchParams.get('time_min')).toISOString(),'2026-10-23T18:30:00.000Z');
  console.log('PASS: birthday only in Upcoming, empty Today, browser-local midnight query');
  items=[...items,{id:'today',summary:'Review Jiraya',start:{dateTime:'2026-10-24T11:00:00+05:30'},end:{dateTime:'2026-10-24T11:30:00+05:30'}},{id:'next-year',summary:'Next year birthday',start:{date:'2027-10-25'},end:{date:'2027-10-26'}}];
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await today.getByRole('heading',{name:'Review Jiraya',exact:true}).waitFor();
  assert.equal(await upcoming.getByText('Review Jiraya',{exact:true}).count(),0);
  assert.equal(await page.getByText('Next year birthday',{exact:true}).count(),0);
  await page.getByLabel('Calendar date range').selectOption('7');
  await page.waitForFunction(()=>!document.querySelector('.service-header-actions button').disabled);
  const last=requests.at(-1);
  assert.equal((new Date(last.searchParams.get('time_max'))-new Date(last.searchParams.get('time_min')))/86400000,7);
  await page.getByRole('button',{name:'Next date window',exact:true}).click();
  await page.getByRole('heading',{name:'Nothing scheduled on this day',exact:true}).waitFor();
  assert.equal(await page.getByText('Happy birthday!',{exact:true}).count(),0);
  await page.getByRole('button',{name:'Today',exact:true}).click();
  await today.getByRole('heading',{name:'Review Jiraya',exact:true}).waitFor();
  console.log('PASS: today/upcoming separation, range navigation, bounded recurrence');
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:process.env.CALENDAR_MOBILE_SCREENSHOT || '/tmp/jiraya-calendar-mobile.png',fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile page must not overflow horizontally');
  await page.setViewportSize({width:1280,height:1000});
  await page.screenshot({path:process.env.CALENDAR_SCREENSHOT || '/tmp/jiraya-calendar-desktop.png',fullPage:true});
  fail=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'Calendar test failure'}).waitFor();
  assert.equal(await page.getByText('Happy birthday!',{exact:true}).count(),0);
  console.log('PASS: mobile layout and failed refresh clears stale event rows');
  fail=false;await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await today.getByRole('heading',{name:'Review Jiraya',exact:true}).waitFor();
  await page.clock.setFixedTime(new Date('2026-10-25T04:30:00Z'));
  // Fast-forward timer execution while preserving the fixed date.
  await page.clock.runFor(30001);
  await today.getByRole('heading',{name:'Happy birthday!',exact:true}).waitFor();
  console.log('PASS: Today automatically rolls over at local midnight');
 } finally {
  fs.rmSync(route,{recursive:true,force:true});
  if(browser)await browser.close();
  if(server){try{if(process.platform==='win32')spawn('taskkill',['/PID',String(server.pid),'/T','/F'],{stdio:'ignore'});else process.kill(-server.pid,'SIGTERM');}catch{}server.stdout.destroy();server.stderr.destroy();server.unref();}
 }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
