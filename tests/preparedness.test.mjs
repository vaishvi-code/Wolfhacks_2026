import test from 'node:test';
import assert from 'node:assert/strict';
import {alertTiming,checklist} from '../public/preparedness.js';
import {normalizeWeather} from '../lib/model.mjs';
const now=Date.parse('2026-10-04T00:00:00Z');
test('future onset produces a countdown; issued/effective time is never substituted for onset',()=>{
  assert.deepEqual(alertTiming({onset:'2026-10-04T03:10:00Z',expires:'2026-10-05T00:00:00Z'},now),{status:'upcoming',label:'Expected in 3h 10m'});
  assert.equal(alertTiming({effective:'2026-10-04T02:00:00Z'},now).status,'unknown');
  assert.equal(alertTiming({onset:'invalid'},now).status,'unknown');
  assert.equal(alertTiming({onset:'2026-10-03T23:00:00Z'},now).status,'started');
  assert.equal(alertTiming({expires:'2026-10-03T23:00:00Z'},now).status,'ended');
});
test('NWS timing fields remain distinct for upcoming alerts',()=>{
  const properties={status:'Actual',event:'Excessive Heat Watch',onset:'2026-10-04T06:00:00Z',effective:'2026-10-04T00:00:00Z',ends:'2026-10-05T06:00:00Z',expires:'2026-10-04T18:00:00Z'};
  const [alert]=normalizeWeather({features:[{id:'test',properties}]},now);
  assert.equal(alert.onset,properties.onset);assert.equal(alert.effective,properties.effective);
  assert.equal(alert.ends,properties.ends);assert.equal(alert.alertExpires,properties.expires);
});
test('checklists include shared supplies and distinct preparation for each hazard',()=>{
  for(const hazard of ['flood','hurricane','heat']){
    const items=checklist(hazard);assert.equal(items.length,8);assert.equal(new Set(items.map(([id])=>id)).size,8);assert.ok(items.some(([id])=>id==='supplies'));
  }
  assert.ok(checklist('flood').some(([id])=>id==='higher'));
  assert.ok(checklist('hurricane').some(([id])=>id==='zone'));
  assert.ok(checklist('heat').some(([id])=>id==='cooling'));
});
