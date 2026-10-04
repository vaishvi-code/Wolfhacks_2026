import {AreaContext,floodWaterways} from '../lib/area-context.mjs';
import {Store} from '../lib/store.mjs';
import {REGIONS} from '../lib/config.mjs';
const region=REGIONS[process.argv[2]||'raleigh'];
if(!region)throw new Error('Choose raleigh, wilmington or asheville.');
// An optional SQLite path also saves verified results for a local preview.
const store=new Store(process.argv[3]||':memory:');
try{
  const data=await new AreaContext(store).load(region);
  console.log(JSON.stringify({region:region.id,floodWaterways:floodWaterways(data),sources:Object.fromEntries(['flood','tracts','epa','catalog','streams'].map(kind=>[kind,{status:data[kind].source.status,count:data[kind].data?.features?.length??data[kind].data?.length??null,partial:data[kind].data?.partial??false}]))},null,2));
}finally{store.close();}
