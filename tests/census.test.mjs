import test from 'node:test';
import assert from 'node:assert/strict';
import {parseCountyPopulation} from '../lib/census.mjs';
import {Providers} from '../lib/providers.mjs';
import {checkApis} from '../scripts/check-apis.mjs';
const rows=(estimate='1178653',moe='-555555555',annotation=null)=>[['NAME','B01003_001E','B01003_001M','B01003_001EA'],['Wake County, North Carolina',estimate,moe,annotation]];

test('a Census controlled population estimate stays usable with a nonnumeric margin of error',()=>{
  const result=parseCountyPopulation(rows());assert.equal(result.population,1178653);assert.equal(result.marginOfError,null);assert.equal(result.marginOfErrorStatus,'controlled');assert.match(result.marginOfErrorNote,/not appropriate/);
});
test('published margins remain numeric while known missing-margin codes are labeled',()=>{
  assert.equal(parseCountyPopulation(rows('500','20')).marginOfError,20);
  assert.equal(parseCountyPopulation(rows('0','0')).population,0);
  assert.equal(parseCountyPopulation(rows('500','-222222222')).marginOfErrorStatus,'unavailable');
});
test('missing, suppressed, malformed and annotated population counts cannot become zero or valid counts',()=>{
  for(const value of [null,undefined,'',true,'bad','-999999999','-666666666','2.5']){const data=rows();data[1][1]=value;assert.throws(()=>parseCountyPopulation(data));}
  assert.throws(()=>parseCountyPopulation(rows('500','20','N')));assert.throws(()=>parseCountyPopulation(rows('500','-1')));
});
test('provider and setup checker both accept controlled estimates',async t=>{
  const old=process.env.CENSUS_API_KEY;process.env.CENSUS_API_KEY='test-key';t.after(()=>{if(old===undefined)delete process.env.CENSUS_API_KEY;else process.env.CENSUS_API_KEY=old;});
  const values=new Map(),store={get:key=>values.get(key),set:(key,data)=>values.set(key,{data,updated:new Date().toISOString()})};
  const fakeFetch=async url=>new Response(JSON.stringify(String(url).includes('census.gov')?rows():{mode:'live',sources:['nws-','weather-','usgs-','osm-'].map(id=>({id,status:'cached'}))}),{headers:{'Content-Type':'application/json'}});
  t.mock.method(globalThis,'fetch',fakeFetch);
  const provider=await new Providers(store).census({id:'raleigh',fips:'183'});assert.equal(provider.source.status,'live');assert.equal(provider.data.marginOfErrorStatus,'controlled');
  const checks=await checkApis({env:{CENSUS_API_KEY:'test-key'},fetchImpl:fakeFetch});assert.equal(checks.find(c=>c.service==='Census').status,'verified');
});
