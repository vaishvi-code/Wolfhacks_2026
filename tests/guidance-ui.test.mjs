import test from 'node:test';
import assert from 'node:assert/strict';
import {initGuidance} from '../public/guidance.js';

function setup(t,getUserMedia) {
  const nodes=new Map();
  class Element extends EventTarget {
    value='';textContent='';hidden=false;disabled=false;src='';
    pause(){} removeAttribute(name){delete this[name];}setAttribute(name,value){this[name]=value;}
  }
  const element=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  class Recorder {
    static isTypeSupported(){return true;}
    state='inactive';constructor(){Recorder.latest=this;}
    start(){this.state='recording';}
    stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['question audio'],{type:'audio/webm'})});this.onstop?.();}
  }
  const globals={document:{getElementById:element},window:new EventTarget(),navigator:{mediaDevices:{getUserMedia}},MediaRecorder:Recorder};
  const restore=[];
  for(const [key,value]of Object.entries(globals)){
    const descriptor=Object.getOwnPropertyDescriptor(globalThis,key);Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
    restore.push(()=>{if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];});
  }
  element('guidance-language').value='en';element('guidance-speed').value='1';
  const requests=[],state={revision:0,epoch:0,config:{integrations:{gemini:{configured:true},elevenlabs:{keyConfigured:true}}}};
  const controller=initGuidance({state,api:async(path,body)=>{requests.push({path,body});return {id:'brief',text:'DEMO response',language:'en'};},context:()=>({mode:'demo',hazard:'flood',region:'raleigh'}),toast:()=>{},download:()=>{}});
  t.after(()=>{try{controller.reset();}finally{restore.forEach(fn=>fn());}});return {element,requests,state};
}

test('closing guidance while microphone permission is pending stops late-granted audio tracks',async t=>{
  let grant,stopped=0;const {element}=setup(t,()=>new Promise(resolve=>grant=resolve));
  const pending=element('record-question').onclick();element('guidance-dialog').dispatchEvent(new Event('close'));
  grant({getTracks:()=>[{stop:()=>stopped++}]});await pending;
  assert.equal(stopped,1);assert.equal(element('recording-preview').hidden,true);assert.equal(element('transcribe-question').disabled,true);
});

test('recording requires separate transcription and question actions, and discarding releases its preview',async t=>{
  let stopped=0,uploads=0;const {element,requests}=setup(t,async()=>({getTracks:()=>[{stop:()=>stopped++}]}));
  t.mock.method(globalThis,'fetch',async(url,options)=>{uploads++;assert.match(url,/api\/transcribe/);assert.ok(options.body instanceof Blob);return new Response(JSON.stringify({text:'What is missing?',provider:'ElevenLabs Scribe'}));});
  await element('record-question').onclick();await element('record-question').onclick();
  assert.ok(stopped>0);assert.equal(uploads,0);assert.equal(requests.length,0);assert.equal(element('recording-preview').hidden,false);
  await element('transcribe-question').onclick();assert.equal(uploads,1);assert.equal(requests.length,0);assert.equal(element('guidance-question').value,'What is missing?');
  element('discard-recording').onclick();assert.equal(element('recording-preview').hidden,true);assert.equal(element('transcribe-question').disabled,true);
});

test('a transcription finishing after discard cannot replace the question text',async t=>{
  let respond;const {element}=setup(t,async()=>({getTracks:()=>[{stop:()=>{}}]}));
  t.mock.method(globalThis,'fetch',()=>new Promise(resolve=>respond=resolve));
  await element('record-question').onclick();await element('record-question').onclick();
  const pending=element('transcribe-question').onclick();element('discard-recording').onclick();
  element('guidance-question').value='Keep my typed question';
  respond(new Response(JSON.stringify({text:'Late transcript',provider:'ElevenLabs Scribe'})));await pending;
  assert.equal(element('guidance-question').value,'Keep my typed question');
});
