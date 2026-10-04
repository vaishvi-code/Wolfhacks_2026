import http from 'node:http';
import {readFile,stat} from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import {parseByteRange} from './lib/http-range.mjs';
import {resolve,extname,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {Store} from './lib/store.mjs';
import {AreaContext} from './lib/area-context.mjs';
import {DisasterService} from './lib/service.mjs';
import {REGIONS,POLL_MS} from './lib/config.mjs';
import {validCoordinate,pointInGeometry,bboxGeometry} from './lib/geo.mjs';
import {planRoute} from './lib/routing.mjs';
import {planEvacuation,evacuationIncidents,HAZARDS} from './lib/evacuation.mjs';
import {segmentDistanceKm} from './lib/geo.mjs';
import {generateBrief,speech,integrationStatus,StreamSinks} from './lib/integrations.mjs';
import {guidanceOptions,listVoices,transcribeRecording,MAX_RECORDING_BYTES,AUDIO_TYPES} from './lib/voice.mjs';

const root=fileURLToPath(new URL('.',import.meta.url)),publicRoot=resolve(root,'public');
const store=new Store(process.env.DATABASE_PATH||resolve(root,'data/terrawatch.sqlite'));
const sinks=new StreamSinks(store),service=new DisasterService(store,sinks),briefs=new Map(),limits=new Map();
const areaContext=new AreaContext(store);
const MIME={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.json':'application/json','.webmanifest':'application/manifest+json','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon'};
function send(res,status,data){res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));}
function error(message,status=400){return Object.assign(new Error(message),{status});}
function context(url,body={}){
  const region=body.region||url.searchParams.get('region')||'raleigh',mode=body.mode||url.searchParams.get('mode')||'live';
  if(!Object.hasOwn(REGIONS,region)||!['live','demo'].includes(mode))throw error('Unknown region or data mode.');return {region,mode};
}
async function readBody(req){
  if(!req.headers['content-type']?.includes('application/json'))throw error('Use Content-Type: application/json.',415);
  let bytes=0,chunks=[];for await(const chunk of req){bytes+=chunk.length;if(bytes>12000)throw error('Request is too large.',413);chunks.push(chunk);}
  try{const value=JSON.parse(Buffer.concat(chunks).toString());if(!value||typeof value!=='object'||Array.isArray(value))throw Error();return value;}catch{throw error('Invalid JSON body.');}
}
function rateLimit(req,path){
  const key=`${req.socket.remoteAddress}:${path}`,now=Date.now(),limit=path==='/api/reports'?8:['/api/brief','/api/audio','/api/transcribe','/api/voices'].includes(path)?6:30;
  let item=limits.get(key);if(!item||now-item.start>60000){item={start:now,count:0};limits.set(key,item);}if(++item.count>limit)throw error('Too many requests. Please wait a minute.',429);
}
const server=http.createServer(async(req,res)=>{
  res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','strict-origin-when-cross-origin');res.setHeader('X-Frame-Options','DENY');
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'");
  try{
    const url=new URL(req.url,'http://localhost'),path=url.pathname;
    if(req.method==='GET'&&path==='/api/health')return send(res,200,{ok:true,name:'WayAhead',time:new Date().toISOString()});
    if(req.method==='GET'&&path==='/api/config')return send(res,200,{regions:Object.values(REGIONS),pollMs:POLL_MS,integrations:integrationStatus(sinks.tiger,sinks.databricks)});
    if(req.method==='GET'&&path==='/api/area-context'){
      const {region}=context(url);rateLimit(req,path);return send(res,200,await areaContext.load(REGIONS[region]));
    }
    if(req.method==='GET'&&['/api/snapshot','/api/offline'].includes(path)){
      const {region,mode}=context(url);return send(res,200,await service.snapshot(region,mode,path==='/api/offline'));
    }
    if(req.method==='GET'&&path==='/api/stream'){
      const {region}=context(url);service.activeRegions.add(region);
      if(service.listeners.size>=40)throw error('Stream capacity reached.',503);
      res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache','Connection':'keep-alive','X-Accel-Buffering':'no'});res.write('retry: 10000\n\n');
      const emit=(event,data)=>res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
      emit('connected',{region,pollMs:POLL_MS});
      const listener=(id,snapshot)=>{if(id===region)emit('snapshot',snapshot);};service.listeners.add(listener);
      const timer=setInterval(()=>res.write(': heartbeat\n\n'),20000);
      req.on('close',()=>{clearInterval(timer);service.listeners.delete(listener);});return;
    }
    if(req.method==='POST'&&path.startsWith('/api/')){
      // Only this UI can make credential-consuming browser requests.
      if(req.headers.origin){const origin=new URL(req.headers.origin);if(origin.host!==req.headers.host)throw error('Cross-origin requests are not allowed.',403);}
      if(req.headers['sec-fetch-site']==='cross-site')throw error('Cross-site requests are not allowed.',403);
      rateLimit(req,path);
      if(path==='/api/transcribe'){
        const type=req.headers['content-type']?.split(';')[0]?.trim(),{language}=guidanceOptions({language:url.searchParams.get('language')||'auto'});
        if(!Object.hasOwn(AUDIO_TYPES,type))throw error('Use a WebM, Ogg, MP4, WAV, or MP3 recording.',415);
        if(Number(req.headers['content-length'])>MAX_RECORDING_BYTES)throw error('Recording exceeds the 2 MB limit.',413);
        let bytes=0;const chunks=[];for await(const chunk of req){bytes+=chunk.length;if(bytes>MAX_RECORDING_BYTES)throw error('Recording exceeds the 2 MB limit.',413);chunks.push(chunk);}
        return send(res,200,await transcribeRecording(Buffer.concat(chunks),type,language));
      }
      const body=await readBody(req),{region,mode}=context(url,body);
      if(path==='/api/voices')return send(res,200,{voices:await listVoices()});
      if(path==='/api/refresh'){if(mode==='live')await service.refresh(region);else service.demoCache.delete(region);return send(res,200,await service.snapshot(region,mode));}
      if(path==='/api/reports'){
        if(!['flooded_road','blocked_road','fallen_tree','heat_concern'].includes(body.kind))throw error('Choose a valid report type.');
        if(!validCoordinate(body.coordinates)||!pointInGeometry(body.coordinates,bboxGeometry(REGIONS[region].bbox)))throw error('Choose a report location inside the selected map area.');
        if(typeof body.description!=='string'||body.description.trim().length<5||body.description.length>600)throw error('Describe the observation in 5–600 characters.');
        const report={id:randomUUID(),region,mode,kind:body.kind,coordinates:body.coordinates,description:body.description.trim(),status:'unverified',createdAt:new Date().toISOString(),expires:new Date(Date.now()+6*3600000).toISOString()};
        store.addReport(report);sinks.deliverEvents().catch(()=>{});return send(res,201,report);
      }
      if(path==='/api/demo-reset'){
        if(mode!=='demo')throw error('Only simulated demo closures can be reset.');
        const resolved=store.resolveDemoEvents(region);await sinks.conditions(REGIONS[region],'demo');
        return send(res,200,{resolved,snapshot:await service.snapshot(region,'demo')});
      }
      if(path==='/api/demo-closure'){
        if(mode!=='demo')throw error('Simulated closures are only available in demo mode.');
        if(!validCoordinate(body.start)||!pointInGeometry(body.start,bboxGeometry(REGIONS[region].bbox))||!HAZARDS.includes(body.hazard))throw error('Choose a demo route first.');
        const snapshot=await service.snapshot(region,'demo',true);
        const plan=planEvacuation(snapshot,body),route=plan.alternatives[0];
        if(!route)throw error('No current route is available to demonstrate a closure.',422);
        // Pick an interior segment away from both access points. Recalculate the
        // route on the server; a client cannot submit an invented road geometry.
        const points=route.geometry.coordinates;
        const segments=points.slice(1).map((p,i)=>({a:points[i],b:p})).filter(s=>segmentDistanceKm(route.access.start,s.a,s.b)>.15&&segmentDistanceKm(route.access.end,s.a,s.b)>.15);
        if(!segments.length)throw error('Choose a longer demo route to demonstrate a closure.',422);
        const segment=segments[Math.floor(segments.length/2)],coordinates=segment.a.map((n,i)=>(n+segment.b[i])/2);
        const report={id:randomUUID(),region,mode:'demo',kind:'blocked_road',coordinates,description:'Simulated closure added to the previously suggested road route.',status:'unverified',simulation:true,createdAt:new Date().toISOString(),expires:new Date(Date.now()+10*60000).toISOString()};
        store.addReport(report);
        const conditions=await sinks.conditions(REGIONS[region],'demo');
        return send(res,201,{event:report,storage:conditions.source,snapshot:await service.snapshot(region,'demo'),message:'Simulated closure recorded. Recalculate your route.'});
      }
      if(path==='/api/route'){
        const snapshot=await service.snapshot(region,mode,true),destination=snapshot.facilities.find(f=>f.id===body.destinationId);
        if(!destination)throw error('Choose a mapped destination.');if(!validCoordinate(body.start)||!pointInGeometry(body.start,bboxGeometry(REGIONS[region].bbox)))throw error('Choose an origin inside the selected map area.');
        if(mode==='live'&&snapshot.sources.some(s=>/^(nws|osm)-/.test(s.id)&&['unavailable','stale'].includes(s.status)))throw error('Fresh warning and road data are required for live route planning.',409);
        if(snapshot.incidents.some(i=>!i.geometry))throw error('A relevant warning has no mapped boundary. Route planning is paused until its area is available.',409);
        try{return send(res,200,{...planRoute(snapshot.roads,body.start,destination.coordinates,snapshot.incidents,snapshot.reports),destination});}catch(e){throw error(e.message,422);}
      }
      if(path==='/api/evacuate'){
        if(!HAZARDS.includes(body.hazard))throw error('Choose one disaster: flood, hurricane, or heat.');
        if(!validCoordinate(body.start)||!pointInGeometry(body.start,bboxGeometry(REGIONS[region].bbox)))throw error('Set your location inside the selected coverage area.');
        if(body.destinationId!=null&&typeof body.destinationId!=='string')throw error('Invalid destination.');
        const snapshot=await service.snapshot(region,mode,true),{roads,...mapSnapshot}=snapshot;
        return send(res,200,{...planEvacuation(snapshot,body),mapSnapshot});
      }
      if(path==='/api/brief'){
        const options=guidanceOptions(body);
        const snapshot=await service.snapshot(region,mode);
        snapshot.areaContext=areaContext.peek(REGIONS[region]);
        if(body.hazard){if(!HAZARDS.includes(body.hazard))throw error('Choose a valid disaster.');snapshot.incidents=evacuationIncidents(snapshot,body.hazard).filter(i=>i.category===body.hazard);snapshot.selectedHazard=body.hazard;}
        const result=await generateBrief(snapshot,options);const id=randomUUID();
        briefs.set(id,{...result,createdAt:Date.now()});return send(res,200,{...result,id});
      }
      if(path==='/api/audio'){
        const brief=briefs.get(body.briefId);if(!brief||Date.now()-brief.createdAt>3600000)throw error('Generate a fresh situation brief first.');
        if(!process.env.ELEVENLABS_API_KEY)throw error('ElevenLabs is not configured. Add the API key to .env.',503);
        const audio=await speech(brief.text.slice(0,4500),{voiceId:body.voiceId});res.writeHead(200,{'Content-Type':'audio/mpeg','Cache-Control':'no-store'});return res.end(audio);
      }
      throw error('API endpoint not found.',404);
    }
    if(path.startsWith('/api/'))throw error('API endpoint not found.',404);
    if(req.method!=='GET'&&req.method!=='HEAD')throw error('Method not allowed.',405);
    let file;
    if(['/shared/routing.mjs','/shared/geo.mjs','/shared/evacuation.mjs','/shared/route-events.mjs'].includes(path))file=resolve(root,'lib',path.split('/').at(-1));
    else{file=resolve(publicRoot,'.'+decodeURIComponent(path==='/'?'/index.html':path));if(!file.startsWith(publicRoot+sep))throw error('Not found.',404);}
    const info=await stat(file).catch(()=>null);if(!info?.isFile())throw error('Not found.',404);
    if(extname(file)==='.pmtiles'){
      const range=parseByteRange(req.headers.range,info.size);
      if(range===false){res.writeHead(416,{'Content-Range':`bytes */${info.size}`});return res.end();}
      const start=range?.start??0,end=range?.end??info.size-1;
      res.writeHead(range?206:200,{'Content-Type':'application/octet-stream','Accept-Ranges':'bytes','Content-Length':end-start+1,'Cache-Control':'no-cache',...(range?{'Content-Range':`bytes ${start}-${end}/${info.size}`}:{})});
      if(req.method==='HEAD')return res.end();
      const stream=createReadStream(file,{start,end});stream.on('error',()=>res.destroy());res.on('close',()=>stream.destroy());stream.pipe(res);return;
    }
    res.writeHead(200,{'Content-Type':MIME[extname(file)]||'application/octet-stream','Cache-Control':path.startsWith('/vendor/')?'public, max-age=86400':'no-cache'});res.end(req.method==='HEAD'?undefined:await readFile(file));
  }catch(e){if(!res.headersSent)send(res,e.status||502,{error:e.status?e.message:'The request could not be completed. Check the source status and try again.'});else res.end();}
});
server.requestTimeout=95000;server.headersTimeout=15000;
const timer=setInterval(()=>{for(const region of service.activeRegions)service.refresh(region).catch(()=>{});for(const [id,b]of briefs)if(Date.now()-b.createdAt>3600000)briefs.delete(id);for(const [id,l]of limits)if(Date.now()-l.start>120000)limits.delete(id);},POLL_MS);
const port=Number(process.env.PORT)||4173,host=process.env.HOST||'127.0.0.1';
server.listen(port,host,()=>{console.log(`WayAhead running at http://${host}:${port}`);sinks.init().catch(()=>{});if(process.env.DISABLE_POLL!=='1')service.refresh('raleigh').catch(()=>{});});
if(process.env.DISABLE_POLL==='1')clearInterval(timer);
function shutdown(){clearInterval(timer);server.close(()=>{store.close();sinks.pool?.end().finally(()=>process.exit(0));if(!sinks.pool)process.exit(0);});server.closeAllConnections();}
process.on('SIGINT',shutdown);process.on('SIGTERM',shutdown);
