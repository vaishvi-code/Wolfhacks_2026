import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {initGuidance} from '../public/guidance.js';

const html=readFileSync(new URL('../public/index.html',import.meta.url),'utf8');
const renderedIds=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]));

function setup(t,getUserMedia) {
  const nodes=new Map();
  class Element extends EventTarget {
    value='';textContent='';hidden=false;disabled=false;src='';
    pause(){} focus(){} removeAttribute(name){delete this[name];}setAttribute(name,value){this[name]=value;}
    replaceChildren(...children){this.children=children;}
  }
  const element=id=>{if(!renderedIds.has(id))return null;if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  class Recorder {
    static isTypeSupported(){return true;}
    state='inactive';constructor(){Recorder.latest=this;}
    start(){this.state='recording';}
    stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['question audio'],{type:'audio/webm'})});this.onstop?.();}
  }
  const globals={document:{getElementById:element,createElement:()=>new Element()},window:new EventTarget(),navigator:{mediaDevices:{getUserMedia}},MediaRecorder:Recorder};
  const restore=[];
  for(const [key,value]of Object.entries(globals)){
    const descriptor=Object.getOwnPropertyDescriptor(globalThis,key);Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
    restore.push(()=>{if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];});
  }
  element('guidance-language').value='en';element('guidance-speed').value='1';
  const requests=[],state={revision:0,epoch:0,config:{integrations:{gemini:{configured:true},elevenlabs:{keyConfigured:true}}}};
  const controller=initGuidance({state,api:async(path,body)=>{requests.push({path,body});return {id:'brief',text:'DEMO response',language:'en'};},context:()=>({mode:'demo',hazard:'flood',region:'raleigh'}),toast:()=>{},download:()=>{}});
  t.after(()=>{try{controller.reset();}finally{restore.forEach(fn=>fn());}});return {element,requests,state,controller};
}

test('chat controls refresh when server configuration arrives after initial rendering',t=>{
  const {element,state,controller}=setup(t,async()=>({getTracks:()=>[]}));
  const config=state.config;state.config=null;controller.refresh();
  assert.equal(element('record-question').disabled,true);
  element('guidance-question').value='What does this warning mean?';
  state.config=config;controller.refresh();
  assert.equal(element('record-question').disabled,false);assert.equal(element('ask-guidance').disabled,false);
  assert.equal(element('guidance-availability').hidden,true);
  state.config.integrations.elevenlabs.keyConfigured=false;controller.refresh();
  assert.equal(element('record-question').disabled,true);
  assert.match(element('guidance-availability').textContent,/ElevenLabs is not configured/);
});

test('closing guidance while microphone permission is pending stops late-granted audio tracks',async t=>{
  let grant,stopped=0;const {element}=setup(t,()=>new Promise(resolve=>grant=resolve));
  const pending=element('record-question').onclick();element('guidance-dialog').dispatchEvent(new Event('close'));
  grant({getTracks:()=>[{stop:()=>stopped++}]});await pending;
  assert.equal(stopped,1);assert.equal(element('discard-recording').disabled,true);
});

test('automatic mic mode transcribes and asks Gemini on stop',async t=>{
  let uploads=0;const {element,requests}=setup(t,async()=>({getTracks:()=>[{stop(){}}]}));
  element('guidance-language').value='auto';
  t.mock.method(globalThis,'fetch',async(url)=>{
    uploads++;assert.equal(url,'/api/transcribe?language=auto');
    return new Response(JSON.stringify({text:'¿Qué significa esta alerta?',provider:'ElevenLabs Scribe',language:'es'}));
  });
  await element('record-question').onclick();await element('record-question').onclick();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(uploads,1);assert.equal(requests.length,1);
  assert.equal(requests[0].body.question,'¿Qué significa esta alerta?');
  assert.equal(element('guidance-content').children[1].textContent,'DEMO response');
  assert.equal(element('guidance-question').value,'¿Qué significa esta alerta?');
  assert.equal(element('recording-status').hidden,true);
  assert.equal(element('ask-guidance').disabled,false);
});

test('explicit language also sends speech directly to chat and releases the microphone',async t=>{
  let stopped=0,uploads=0;const {element,requests}=setup(t,async()=>({getTracks:()=>[{stop:()=>stopped++}]}));
  t.mock.method(globalThis,'fetch',async(url,options)=>{uploads++;assert.match(url,/api\/transcribe/);assert.ok(options.body instanceof Blob);return new Response(JSON.stringify({text:'What is missing?',provider:'ElevenLabs Scribe'}));});
  await element('record-question').onclick();await element('record-question').onclick();
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(stopped>0);assert.equal(uploads,1);assert.equal(requests.length,1);
  assert.equal(requests[0].body.question,'What is missing?');
  assert.equal(element('guidance-question').value,'What is missing?');
  assert.equal(element('discard-recording').disabled,true);
});

test('a transcription finishing after discard cannot replace the question text',async t=>{
  let respond;const {element,requests}=setup(t,async()=>({getTracks:()=>[{stop:()=>{}}]}));
  t.mock.method(globalThis,'fetch',()=>new Promise(resolve=>respond=resolve));
  await element('record-question').onclick();await element('record-question').onclick();
  element('discard-recording').onclick();
  element('guidance-question').value='Keep my typed question';
  respond(new Response(JSON.stringify({text:'Late transcript',provider:'ElevenLabs Scribe'})));await new Promise(resolve=>setImmediate(resolve));
  assert.equal(element('guidance-question').value,'Keep my typed question');
  assert.equal(requests.length,0);
});
