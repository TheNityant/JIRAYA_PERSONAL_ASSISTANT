const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const ts = require('typescript');
process.env.TZ = 'Asia/Kolkata';
const filename = path.resolve(__dirname, '../lib/calendar-agenda.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS}});
const loaded = new Module(filename);loaded._compile(compiled.outputText, filename);
const {calendarAgenda,localDate,addDays,eventTime} = loaded.exports;
const anchor = () => new Date(2026,9,24);
const allDay = (id,start,end,extra={}) => ({id,start:{date:start},end:{date:end},...extra});

test('tomorrow birthday belongs to Upcoming; an empty Today remains empty', () => {
 const result = calendarAgenda([allDay('birthday','2026-10-25','2026-10-26',{recurringEventId:'annual'})],anchor(),30);
 assert.equal(result.selected.length,0);
 assert.equal(result.upcoming.length,1);
 assert.equal(result.groups[0].date.getDate(),25);
 assert.equal(localDate('2026-10-25').getHours(),0);
});
test('all-day exclusive end and multi-day overlap respect the selected day', () => {
 const result = calendarAgenda([
  allDay('finished','2026-10-23','2026-10-24'),
  allDay('ongoing','2026-10-23','2026-10-26'),
  allDay('today','2026-10-24','2026-10-25')
 ],anchor(),30);
 assert.deepEqual(result.selected.map(row=>row.id),['ongoing','today']);
 assert.equal(result.upcoming.length,0);
 assert.equal(eventTime(allDay('day','2026-10-24','2026-10-25')),'All day');
 assert.match(eventTime(allDay('many','2026-10-23','2026-10-26')),/25/);
});
test('timed events use local dates, including UTC timestamps near midnight', () => {
 const result = calendarAgenda([
  {id:'early',start:{dateTime:'2026-10-24T18:00:00Z'},end:{dateTime:'2026-10-24T18:15:00Z'}},
  {id:'late',start:{dateTime:'2026-10-24T18:45:00Z'},end:{dateTime:'2026-10-24T19:00:00Z'}}
 ],anchor(),7);
 assert.deepEqual(result.selected.map(row=>row.id),['early']);
 assert.deepEqual(result.upcoming.map(row=>row.id),['late']);
});
test('sorts valid occurrences, removes duplicate IDs and cancellations, and bounds the range', () => {
 const result = calendarAgenda([
  allDay('next-year','2027-10-25','2027-10-26'),
  allDay('later','2026-10-27','2026-10-28'),
  allDay('first','2026-10-25','2026-10-26',{recurringEventId:'same-series'}),
  allDay('later','2026-10-27','2026-10-28',{recurringEventId:'same-series'}),
  allDay('removed','2026-10-25','2026-10-26'),
  {id:'removed',status:'cancelled'},
  {id:'invalid',start:{date:'not-a-date'}},
  allDay('boundary','2026-10-31','2026-11-01')
 ],anchor(),7);
 assert.deepEqual(result.upcoming.map(row=>row.id),['first','later']);
 assert.equal(result.groups.length,2);
});
test('date navigation classifies the chosen day independently of actual today', () => {
 const event=allDay('birthday','2026-10-25','2026-10-26');
 assert.equal(calendarAgenda([event],new Date(2026,9,25),7).selected.length,1);
 assert.equal(calendarAgenda([event],new Date(2026,9,26),7).selected.length,0);
});
test('adding calendar days across daylight saving preserves local midnight', () => {
 const previous=process.env.TZ;process.env.TZ='America/New_York';
 try {
  const start=new Date(2026,2,8),end=addDays(start,1);
  assert.equal(end.getHours(),0);assert.equal(end.getDate(),9);
  assert.equal((end-start)/3600000,23);
 }finally{process.env.TZ=previous;}
});
