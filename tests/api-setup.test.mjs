import test from 'node:test';
import assert from 'node:assert/strict';
import {checkApis} from '../scripts/check-apis.mjs';
const publicData={mode:'live',sources:['nws-','weather-','usgs-','osm-'].map(id=>({id:id+'raleigh',status:'cached'}))};
const response=data=>({ok:true,json:async()=>data});
const get=(results,name)=>results.find(r=>r.service===name);

test('setup checker separates missing credentials from verified public feeds without paid calls',async()=>{
  const urls=[],results=await checkApis({env:{},fetchImpl:async url=>{urls.push(String(url));return response(publicData);}});
  assert.equal(urls.length,1);assert.ok(urls[0].startsWith('http://127.0.0.1:4173/'));
  assert.equal(get(results,'Public data').status,'verified');
  for(const name of ['Gemini','ElevenLabs','Census','Tiger Data'])assert.equal(get(results,name).status,'needs-key');
  assert.equal(get(results,'Databricks bridge').status,'not-configured');
});

test('credential probes use metadata only and never claim generation was tested',async()=>{
  const env={GEMINI_API_KEY:'gemini-secret',ELEVENLABS_API_KEY:'eleven-secret',ELEVENLABS_VOICE_ID:'voice-123',CENSUS_API_KEY:'census-secret'},requests=[];
  const results=await checkApis({env,fetchImpl:async(url,options)=>{
    requests.push({url:String(url),options});
    if(String(url).includes('/models/')){assert.equal(options.headers['x-goog-api-key'],env.GEMINI_API_KEY);return response({supportedGenerationMethods:['generateContent']});}
    if(String(url).includes('/voices/')){assert.equal(options.headers['xi-api-key'],env.ELEVENLABS_API_KEY);return response({voice_id:env.ELEVENLABS_VOICE_ID});}
    if(String(url).includes('census.gov'))return response([['NAME','B01003_001E','B01003_001M'],['Wake County, North Carolina','1000000','1000']]);
    return response(publicData);
  }});
  assert.equal(get(results,'Gemini').status,'verified-access');assert.equal(get(results,'ElevenLabs').status,'verified-access');assert.equal(get(results,'Census').status,'verified');
  assert.ok(requests.every(r=>!r.options.method&&!r.options.body));assert.ok(requests.every(r=>r.options.redirect==='error'));
  for(const secret of [env.GEMINI_API_KEY,env.ELEVENLABS_API_KEY,env.CENSUS_API_KEY])assert.ok(!JSON.stringify(results).includes(secret));
});

test('provider errors and database errors cannot echo secrets into check output',async()=>{
  const secret='PRIVATE-SENTINEL',env={GEMINI_API_KEY:secret,TIGER_DATABASE_URL:`postgresql://user:${secret}@db.example/test?sslmode=require`};
  class FakePool {async query(){throw new Error(`HTTP 403. ${secret}`);}async end(){}}
  const results=await checkApis({env,poolFactory:FakePool,fetchImpl:async url=>String(url).includes('googleapis.com')?{ok:false,status:403,json:async()=>{throw new Error(secret);}}:response(publicData)});
  assert.equal(get(results,'Gemini').status,'failed');assert.match(get(results,'Gemini').detail,/HTTP 403/);assert.equal(get(results,'Tiger Data').status,'failed');assert.ok(!JSON.stringify(results).includes(secret));
});

test('missing voice ID gets a specific next step after key lookup succeeds',async()=>{
  const results=await checkApis({env:{ELEVENLABS_API_KEY:'valid-key'},fetchImpl:async url=>response(String(url).includes('elevenlabs.io')?{voices:[]}:publicData)});
  assert.equal(get(results,'ElevenLabs').status,'needs-setting');assert.match(get(results,'ElevenLabs').detail,/ELEVENLABS_VOICE_ID/);
});

test('a stock voice can be selected in the app without a configured default voice ID',async()=>{
  const results=await checkApis({env:{ELEVENLABS_API_KEY:'valid-key'},fetchImpl:async url=>response(String(url).includes('elevenlabs.io')?{voices:[{voice_id:'stock1',name:'Stock'}]}:publicData)});
  assert.equal(get(results,'ElevenLabs').status,'verified-access');assert.match(get(results,'ElevenLabs').detail,/My guidance/);
});

test('Tiger checker uses only SELECT and closes the connection',async()=>{
  const statements=[];let ended=false;
  class FakePool {async query(sql){statements.push(sql);if(sql.includes('pg_extension'))return {rows:[{extversion:'2'}]};if(sql.includes('to_regclass'))return {rows:[{readings:null,hourly:null}]};throw new Error('Unexpected query');}async end(){ended=true;}}
  const results=await checkApis({env:{TIGER_DATABASE_URL:'postgresql://user:secret@db.example/test?sslmode=require'},poolFactory:FakePool,fetchImpl:async()=>response(publicData)});
  assert.equal(get(results,'Tiger Data').status,'verified-access');assert.match(get(results,'Tiger Data').detail,/Restart the app/);assert.ok(statements.every(s=>s.startsWith('SELECT ')));assert.equal(ended,true);
});

test('insecure database settings and unverified bridges are not reported connected',async()=>{
  const results=await checkApis({env:{TIGER_DATABASE_URL:'postgresql://user:secret@db.example/test?sslmode=disable',DATABRICKS_INGEST_URL:'https://bridge.example/ingest',DATABRICKS_INGEST_TOKEN:'bridge-secret'},poolFactory:class{constructor(){throw new Error('Must not connect');}},fetchImpl:async()=>response(publicData)});
  assert.equal(get(results,'Tiger Data').status,'failed');assert.equal(get(results,'Databricks bridge').status,'unverified');assert.ok(!JSON.stringify(results).includes('bridge-secret'));
});

test('free Tiger service can be reported as encrypted but certificate-unverified',async()=>{
  const statements=[];let ended=false;
  class FakePool {async query(sql){statements.push(sql);if(sql.includes('pg_extension'))return {rows:[{extversion:'2'}]};if(sql.includes('to_regclass'))return {rows:[{readings:null,hourly:null}]};throw new Error('Unexpected query');}async end(){ended=true;}}
  const results=await checkApis({env:{TIGER_DATABASE_URL:'postgresql://user:secret@db.example/test?sslmode=no-verify'},poolFactory:FakePool,fetchImpl:async()=>response(publicData)});
  assert.equal(get(results,'Tiger Data').status,'encrypted-unverified');assert.match(get(results,'Tiger Data').detail,/certificate verification is disabled/);assert.equal(ended,true);
});
