import test from 'node:test';
import assert from 'node:assert/strict';
import {guidanceOptions,listVoices,resolveVoice,transcribeRecording,MAX_RECORDING_BYTES} from '../lib/voice.mjs';
import {generateBrief,speech} from '../lib/integrations.mjs';
test('automatic transcription omits a language hint and returns the detected language',async t=>{
  env(t,{ELEVENLABS_API_KEY:'test'});
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    assert.equal(options.body.has('language_code'),false);
    return json({text:'यह चेतावनी क्या है?',language_code:'hin'});
  });
  const result=await transcribeRecording(Buffer.from('audio'),'audio/webm','auto');
  assert.equal(result.language,'hin');assert.match(result.text,/चेतावनी/);
});
test('Gemini automatic replies preserve the detected answer language',async t=>{
  env(t,{GEMINI_API_KEY:'test'});
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    const request=JSON.parse(options.body);assert.equal(request.generationConfig.responseMimeType,'application/json');
    assert.match(request.systemInstruction.parts[0].text,/Detect the language/);
    return json({candidates:[{content:{parts:[{text:JSON.stringify({text:'Información del escenario.',language:'es'})}]}}]});
  });
  const result=await generateBrief(demoSnapshot(),{language:'auto',question:'¿Qué significa esta alerta?'});
  assert.equal(result.ai,true);assert.equal(result.language,'es');assert.equal(result.text,'Información del escenario.');
});
import {demoSnapshot} from '../lib/demo.mjs';
const json=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
function env(t,values){for(const [name,value]of Object.entries(values)){const before=process.env[name];process.env[name]=value;t.after(()=>{if(before===undefined)delete process.env[name];else process.env[name]=before;});}}

test('guidance validates language and question before calling a provider',async()=>{
  assert.deepEqual(guidanceOptions({language:'es',question:'  What is missing?  '}),{language:'es',question:'What is missing?'});
  for(const body of [{language:'__proto__'},{language:'fr'},{question:{}},{question:'a'.repeat(601)}])assert.throws(()=>guidanceOptions(body),{status:400});
});
test('unavailable translation and question answering are explicit in the local fallback',async t=>{
  env(t,{GEMINI_API_KEY:''});const result=await generateBrief(demoSnapshot(),{language:'es',question:'Can I leave?'});
  assert.equal(result.ai,false);assert.equal(result.language,'en');assert.match(result.notice,/English template/);assert.match(result.notice,/does not answer/);
});
test('Gemini questions use selected evidence and do not transmit route origins or arbitrary properties',async t=>{
  env(t,{GEMINI_API_KEY:'test-key'});const snapshot=demoSnapshot();snapshot.start=[-78.1,35.2];snapshot.privateValue='PRIVATE-SENTINEL';
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    const body=JSON.parse(options.body),evidence=JSON.parse(body.contents[0].parts[0].text);
    assert.equal(evidence.question,'What is missing?');assert.ok(!options.body.includes('PRIVATE-SENTINEL'));assert.ok(!options.body.includes('-78.1'));
    assert.match(body.systemInstruction.parts[0].text,/Write in Spanish/);assert.match(body.systemInstruction.parts[0].text,/never give turn-by-turn/);
    return json({candidates:[{content:{parts:[{text:'DEMOSTRACIÓN. Siga las instrucciones locales.'}]}}]});
  });
  const result=await generateBrief(snapshot,{language:'es',question:'What is missing?'});assert.equal(result.language,'es');assert.equal(result.ai,true);
});
test('voice choices contain only allowlisted public fields and arbitrary voice IDs are rejected',async t=>{
  env(t,{ELEVENLABS_API_KEY:'voice-list-test',ELEVENLABS_VOICE_ID:''});let calls=0;
  t.mock.method(globalThis,'fetch',async(url,options)=>{calls++;assert.match(url,/voice_type=default/);assert.equal(options.headers['xi-api-key'],'voice-list-test');return json({voices:[{voice_id:'stock1',name:'Stock voice',samples:[{private:'SECRET'}]},{voice_id:'../../bad',name:'Invalid'}]});});
  assert.deepEqual(await listVoices(),[{id:'stock1',name:'Stock voice'}]);assert.equal(await resolveVoice('stock1'),'stock1');assert.equal(calls,1);
  await assert.rejects(resolveVoice('not-in-list'),{status:400});await assert.rejects(resolveVoice('../voice'),{status:400});
});
test('speech uses the selected stock voice and multilingual text without an unsupported language parameter',async t=>{
  env(t,{ELEVENLABS_API_KEY:'speech-test',ELEVENLABS_VOICE_ID:''});
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    if(url.includes('/v2/voices'))return json({voices:[{voice_id:'spanish1',name:'Stock'}]});
    assert.match(url,/text-to-speech\/spanish1/);const body=JSON.parse(options.body);assert.equal(body.text,'Esta es una demostración.');assert.equal(body.model_id,'eleven_multilingual_v2');assert.equal(body.language_code,undefined);
    return new Response(new Uint8Array([73,68,51]),{headers:{'Content-Type':'audio/mpeg'}});
  });
  assert.equal((await speech('Esta es una demostración.',{voiceId:'spanish1'})).length,3);
});
test('transcription accepts a bounded recording and sends multipart audio only to ElevenLabs',async t=>{
  env(t,{ELEVENLABS_API_KEY:'transcript-test'});
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    assert.equal(url,'https://api.elevenlabs.io/v1/speech-to-text');assert.equal(options.redirect,'error');assert.equal(options.body.get('model_id'),'scribe_v2');assert.equal(options.body.get('language_code'),'es');assert.equal(options.body.get('diarize'),'false');assert.equal(options.body.get('file').type,'audio/webm');
    return json({text:'¿Qué significa esta advertencia?'});
  });
  const result=await transcribeRecording(Buffer.from('test audio'),'audio/webm;codecs=opus','es');assert.equal(result.provider,'ElevenLabs Scribe');assert.equal(result.truncated,false);
});
test('invalid uploads never reach providers and provider error bodies are not exposed',async t=>{
  env(t,{ELEVENLABS_API_KEY:'error-test'});let calls=0;
  t.mock.method(globalThis,'fetch',async()=>{calls++;return new Response('secret provider detail',{status:403});});
  await assert.rejects(transcribeRecording(Buffer.alloc(0),'audio/webm'),{status:413});
  await assert.rejects(transcribeRecording(Buffer.alloc(MAX_RECORDING_BYTES+1),'audio/webm'),{status:413});
  await assert.rejects(transcribeRecording(Buffer.from('x'),'text/html'),{status:415});assert.equal(calls,0);
  await assert.rejects(transcribeRecording(Buffer.from('x'),'audio/webm'),error=>error.status===502&&!error.message.includes('secret provider detail'));
});
