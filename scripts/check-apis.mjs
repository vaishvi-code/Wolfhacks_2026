import {pathToFileURL} from 'node:url';
import {parseCountyPopulation} from '../lib/census.mjs';
class CheckError extends Error {}

// This checker never generates paid text/audio, submits ingest batches, prints
// credentials, or echoes provider error bodies (which can contain secrets).
export async function checkApis({env=process.env,fetchImpl=fetch,poolFactory}={}){
  const result=(service,status,detail)=>({service,status,detail});
  const has=name=>!!env[name]?.trim()&&!/^(your[_ -]|paste[_ -]|replace[_ -]|<)/i.test(env[name].trim());
  async function json(url,headers={}){
    const response=await fetchImpl(url,{headers,signal:AbortSignal.timeout(18000),redirect:'error'});
    if(!response.ok){const hint=response.status===401||response.status===403?'Check the key and its permissions.':response.status===404?'Check the model or voice ID.':response.status===429?'The provider is rate-limiting this request. Try later.':'The provider rejected this check.';throw new CheckError(`HTTP ${response.status}. ${hint}`);}
    try{return await response.json();}catch{throw new CheckError('The provider returned an unexpected response. Check the key and service settings.');}
  }
  async function guard(service,fn){try{return await fn();}catch(error){return result(service,'failed',error instanceof CheckError?error.message:'Connection check failed. Check connectivity, credentials, TLS, and service availability.');}}
  const jobs=[
    guard('Public data',async()=>{
      const base=env.APP_URL||'http://127.0.0.1:4173';
      const s=await json(`${base.replace(/\/$/,'')}/api/snapshot?region=raleigh&mode=live`);
      if(s.mode!=='live'||!Array.isArray(s.sources))return result('Public data','failed','The app did not return a live snapshot. Start the server and check APP_URL.');
      const expected=['nws-','weather-','usgs-','osm-'],sources=expected.map(prefix=>s.sources.find(source=>source.id.startsWith(prefix)));
      const ok=sources.every(source=>source&&['live','cached'].includes(source.status));
      return result('Public data',ok?'verified':'failed',`${sources.map((source,i)=>`${expected[i].slice(0,-1)}: ${source?.status||'missing'}`).join('; ')}. These sources need no API key. Cached means previously retrieved data within the refresh interval.`);
    }),
    guard('Gemini',async()=>{
      if(!has('GEMINI_API_KEY'))return result('Gemini','needs-key','Add GEMINI_API_KEY from Google AI Studio.');
      const model=env.GEMINI_MODEL||'gemini-3.5-flash-lite';
      if(!/^[a-zA-Z0-9._-]+$/.test(model))return result('Gemini','failed','Use a model ID in GEMINI_MODEL, without a URL or models/ prefix.');
      const data=await json(`https://generativelanguage.googleapis.com/v1beta/models/${model}`,{'x-goog-api-key':env.GEMINI_API_KEY});
      if(!data.supportedGenerationMethods?.includes('generateContent'))return result('Gemini','failed','This model does not advertise generateContent support. Choose a supported text model.');
      return result('Gemini','verified-access','Configured model metadata is accessible. Text generation and generation quota still need an in-app test.');
    }),
    guard('ElevenLabs',async()=>{
      if(!has('ELEVENLABS_API_KEY'))return result('ElevenLabs','needs-key','Add ELEVENLABS_API_KEY. A default ELEVENLABS_VOICE_ID is optional when using the in-app voice selector.');
      const headers={'xi-api-key':env.ELEVENLABS_API_KEY};
      if(!has('ELEVENLABS_VOICE_ID')){
        const data=await json('https://api.elevenlabs.io/v2/voices?voice_type=default&page_size=1',headers);
        if(!Array.isArray(data.voices)||!data.voices.length)return result('ElevenLabs','needs-setting','No stock voices returned. Check voice availability or set ELEVENLABS_VOICE_ID.');
        return result('ElevenLabs','verified-access','Key accepted for voice lookup. Choose a narrator in My guidance. Speech generation and transcription permissions still need in-app tests.');
      }
      if(!/^[a-zA-Z0-9_-]+$/.test(env.ELEVENLABS_VOICE_ID))return result('ElevenLabs','failed','ELEVENLABS_VOICE_ID must be the ID only, not a voice page URL.');
      const voice=await json(`https://api.elevenlabs.io/v1/voices/${env.ELEVENLABS_VOICE_ID}`,headers);
      if(voice.voice_id!==env.ELEVENLABS_VOICE_ID)return result('ElevenLabs','failed','The requested voice was not returned.');
      return result('ElevenLabs','verified-access','Voice metadata is accessible. Text-to-speech permission and remaining quota still need an in-app audio test.');
    }),
    guard('Census',async()=>{
      if(!has('CENSUS_API_KEY'))return result('Census','needs-key','Request and activate a Census API key, then set CENSUS_API_KEY.');
      const query=new URLSearchParams({get:'NAME,B01003_001E,B01003_001M,B01003_001EA',for:'county:183',in:'state:37',key:env.CENSUS_API_KEY});
      const data=await json(`https://api.census.gov/data/2024/acs/acs5?${query}`);
      try{parseCountyPopulation(data);}catch{return result('Census','failed','A valid county population estimate was not returned. Check key activation and ACS data availability.');}
      return result('Census','verified','ACS 2024 population data returned for Wake County. This is county context, not an estimate of people exposed.');
    }),
    guard('Tiger Data',async()=>{
      if(!has('TIGER_DATABASE_URL'))return result('Tiger Data','needs-key','Add the full PostgreSQL URL, including the database password, from your Tiger Cloud service.');
      let url;try{url=new URL(env.TIGER_DATABASE_URL);}catch{return result('Tiger Data','failed','TIGER_DATABASE_URL is not a valid PostgreSQL connection URL.');}
      const sslmode=url.searchParams.get('sslmode');
      if(!['postgres:','postgresql:'].includes(url.protocol)||!['require','no-verify','verify-ca','verify-full'].includes(sslmode)||url.searchParams.get('ssl')==='false')return result('Tiger Data','failed','Use an encrypted PostgreSQL URL: sslmode=require, no-verify, verify-ca, or verify-full.');
      const create=poolFactory||((await import('pg')).Pool),pool=new create({connectionString:env.TIGER_DATABASE_URL,max:1,connectionTimeoutMillis:8000,query_timeout:10000});
      try{
        const extension=await pool.query("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'");
        if(!extension.rows.length)return result('Tiger Data','failed','Connected to PostgreSQL, but TimescaleDB is not enabled in this database.');
        const schema=await pool.query("SELECT to_regclass('public.sensor_readings') AS readings, to_regclass('public.sensor_hourly') AS hourly");
        if(!schema.rows[0]?.readings||!schema.rows[0]?.hourly)return result('Tiger Data',sslmode==='no-verify'?'encrypted-unverified':'verified-access',`Database connection and TimescaleDB verified.${sslmode==='no-verify'?' Server certificate verification is disabled.':''} Restart the app to initialize its schema, then verify sensor delivery.`);
        const readings=await pool.query('SELECT COUNT(*) AS count, MAX(observed_at) AS latest FROM public.sensor_readings');
        const security=sslmode==='no-verify'?' encrypted but server certificate verification is disabled for the free service.':'';
        return result('Tiger Data',sslmode==='no-verify'?'encrypted-unverified':'verified-access',`Database and app schema are accessible; ${readings.rows[0].count} stored observations.${security} Inspect Data & help after a live refresh to confirm current delivery.`);
      }finally{await pool.end();}
    }),
    guard('Databricks bridge',async()=>{
      if(!has('DATABRICKS_INGEST_URL'))return result('Databricks bridge','not-configured','Requires a separately deployed ingestion bridge. A Databricks workspace URL is not sufficient.');
      let url;try{url=new URL(env.DATABRICKS_INGEST_URL);}catch{return result('Databricks bridge','failed','DATABRICKS_INGEST_URL must be your bridge HTTPS endpoint.');}
      if(url.protocol!=='https:')return result('Databricks bridge','failed','Use an HTTPS ingestion bridge URL.');
      if(!has('DATABRICKS_INGEST_TOKEN'))return result('Databricks bridge','needs-setting','Set the token expected by your authenticated bridge.');
      return result('Databricks bridge','unverified','Settings present. This checker does not send sensor records. Verify actual delivery in the app and records in Databricks.');
    })
  ];
  return Promise.all(jobs);
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  console.log('WayAhead API setup check — no text/audio generation or database writes.');
  const checks=await checkApis();
  for(const {service,status,detail} of checks)console.log(`\n${service}: ${status}\n  ${detail}`);
  console.log('\nKeys and database URLs are never printed. Restart the app after changing .env.');
  // Distinguish invalid configuration from credentials still awaiting setup.
  process.exitCode=checks.some(c=>c.status==='failed')?1:checks.some(c=>['needs-key','needs-setting','unverified'].includes(c.status))?2:0;
}
