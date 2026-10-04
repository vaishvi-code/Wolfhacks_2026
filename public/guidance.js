export function initGuidance({state,api,context,toast,download}) {
  const $=id=>document.getElementById(id);
  $('guidance-dialog').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();$('guidance-dialog').close();}});
  let answerVersion=0,generating=false,voicesLoaded=false,loadingVoices=false,audioUrl;
  let recorder,stream,chunks=[],recording,clock,deadline,startedAt,captureVersion=0,requestingMic=false;
  let transcribing=false,transcriptionController,questionVersion=0;
  const supported=()=>!!navigator.mediaDevices?.getUserMedia&&typeof MediaRecorder!=='undefined';
  const status=text=>{$('recording-status').textContent=text;$('recording-status').hidden=!text;};
  const active=()=>recorder?.state==='recording';
  function sync(){
    const busy=generating||active()||requestingMic||transcribing;
    $('generate-brief').disabled=busy;
    $('ask-guidance').disabled=busy||!$('guidance-question').value.trim()||!state.config?.integrations.gemini.configured;
    $('record-question').disabled=!supported()||!state.config?.integrations.elevenlabs.keyConfigured||!state.config?.integrations.gemini.configured||generating||requestingMic||transcribing;
    $('record-question').innerHTML=active()?'<svg class="icon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>':'<svg class="icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/></svg>';
    $('record-question').setAttribute('aria-label',active()?'Stop and send question':'Speak your question');
    $('record-question').setAttribute('title',active()?'Stop and send question':'Speak your question');
    $('record-question').setAttribute('aria-pressed',String(!!active()));
    $('discard-recording').disabled=!recording&&!active()&&!requestingMic&&!transcribing;
    $('discard-recording').hidden=$('discard-recording').disabled;
    $('guidance-language').disabled=active()||requestingMic||transcribing;
    const messages=[];
    if(!state.config)messages.push(state.configError?'Connection to the app server failed. Reopen chat to retry.':'Checking voice and chat availability…');
    else{
      if(!state.config.integrations?.elevenlabs?.keyConfigured)messages.push('Mic unavailable: ElevenLabs is not configured on this server.');
      if(!state.config.integrations?.gemini?.configured)messages.push('Send unavailable: Gemini is not configured on this server.');
    }
    if(!supported())messages.push('This browser cannot record audio here. Open the HTTPS site in Safari or Chrome, or type your question.');
    $('guidance-availability').textContent=messages.join(' ');$('guidance-availability').hidden=!messages.length;
  }
  function stopAudio(){
    $('brief-audio').pause();$('brief-audio').removeAttribute('src');$('brief-audio').hidden=true;
    if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=null;
    window.speechSynthesis?.cancel();
  }
  function clearAnswer(){
    answerVersion++;generating=false;state.brief=null;stopAudio();
    $('guidance-content').textContent='Generate a short explanation of the available information.';
    $('guidance-content').lang='en';$('play-brief').disabled=true;$('download-brief').hidden=true;sync();
  }
  function discard(){
    captureVersion++;requestingMic=false;clearInterval(clock);clearTimeout(deadline);
    if(active())recorder.stop();stream?.getTracks().forEach(track=>track.stop());stream=null;recorder=null;chunks=[];
    transcriptionController?.abort();transcribing=false;recording=null;
    status('');sync();
  }
  function reset(){clearAnswer();discard();$('guidance-question').value='';questionVersion++;sync();}
  async function loadVoices(){
    const configured=state.config?.integrations.elevenlabs;
    sync();
    if(!configured?.keyConfigured){$('voice-status').textContent='Browser voice available. Add an ElevenLabs key for voice selection and recording transcription.';return;}
    if(voicesLoaded||loadingVoices)return;loadingVoices=true;
    $('voice-status').textContent='Loading ElevenLabs voices…';
    try{
      const data=await api('/api/voices',context()),select=$('guidance-voice');select.replaceChildren();
      if(configured.configured)select.add(new Option('Configured default voice',''));
      for(const voice of data.voices)select.add(new Option(voice.name,voice.id));
      select.disabled=!data.voices.length&&!configured.configured;voicesLoaded=true;
      $('voice-status').textContent=data.voices.length?'ElevenLabs stock voices. Availability and usage depend on your account.':'No stock voices returned. Configure a voice ID or check account access.';
    }catch(error){$('voice-status').textContent=error.message;}
    finally{loadingVoices=false;}
  }
  async function generate(question=''){
    clearAnswer();const version=answerVersion,revision=state.revision,epoch=state.epoch;
    generating=true;sync();$('guidance-content').textContent=question?'Answering from the warning information…':'Preparing your guidance…';
    try{
      const brief=await api('/api/brief',{...context(),language:$('guidance-language').value,question});
      if(version!==answerVersion||revision!==state.revision||epoch!==state.epoch)return;
      state.brief=brief;const badge=document.createElement('span'),text=document.createElement('p'),notice=document.createElement('p');
      badge.className='tag';badge.textContent=brief.provider;text.className='brief-text';text.textContent=brief.text;
      notice.className='brief-notice';notice.textContent=brief.notice;$('guidance-content').replaceChildren(badge,text,notice);
      $('guidance-content').lang=brief.language||'en';$('play-brief').disabled=false;$('download-brief').hidden=false;
    }catch(error){if(version===answerVersion){$('guidance-content').textContent='Guidance could not be generated. Try again.';toast(error.message,true);}}
    finally{if(version===answerVersion){generating=false;sync();}}
  }
  $('open-guidance').onclick=()=>{
    if(!$('guidance-dialog').open)$('guidance-dialog').show();$('open-guidance').hidden=true;$('open-guidance').setAttribute('aria-expanded','true');
    if(!state.config){api('/api/config').then(config=>{state.config=config;state.configError=false;refresh();}).catch(()=>{state.configError=true;sync();});}else loadVoices();
    if(!supported())status('Recording is unavailable in this browser. You can type a question.');
  };
  $('generate-brief').onclick=()=>generate();
  $('ask-guidance').onclick=()=>generate($('guidance-question').value.trim());
  $('guidance-question').oninput=()=>{questionVersion++;sync();};
  $('guidance-language').onchange=()=>{clearAnswer();discard();};
  $('guidance-voice').onchange=stopAudio;
  $('guidance-speed').onchange=()=>{$('brief-audio').playbackRate=Number($('guidance-speed').value);};
  $('download-brief').onclick=()=>{if(state.brief)download(`wayahead-${state.hazard}-guidance.txt`,state.brief.text);};
  $('play-brief').onclick=async()=>{
    if(!state.brief)return;const brief=state.brief,version=answerVersion,voiceId=$('guidance-voice').value;
    stopAudio();
    if(!state.config?.integrations.elevenlabs.keyConfigured){
      if(!window.speechSynthesis){toast('Audio is unavailable in this browser.',true);return;}
      const utterance=new SpeechSynthesisUtterance(brief.text);utterance.lang=brief.language&&brief.language!=='auto'?brief.language:navigator.language||'en-US';utterance.rate=Number($('guidance-speed').value);
      window.speechSynthesis.speak(utterance);toast('Playing browser voice. ElevenLabs is not configured.');return;
    }
    if(!voiceId&&!state.config.integrations.elevenlabs.configured){toast('Choose an available ElevenLabs voice first.',true);return;}
    $('play-brief').disabled=true;
    try{
      const response=await fetch('/api/audio',{method:'POST',headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(50000),body:JSON.stringify({...context(),briefId:brief.id,voiceId})});
      if(!response.ok)throw new Error((await response.json()).error);
      const blob=await response.blob();if(version!==answerVersion||voiceId!==$('guidance-voice').value)return;
      audioUrl=URL.createObjectURL(blob);$('brief-audio').src=audioUrl;$('brief-audio').playbackRate=Number($('guidance-speed').value);$('brief-audio').hidden=false;await $('brief-audio').play();
    }catch(error){if(version===answerVersion)toast(error.message,true);}
    finally{$('play-brief').disabled=!state.brief;}
  };
  $('record-question').onclick=async()=>{
    if(active()){recorder.stop();stream?.getTracks().forEach(track=>track.stop());return;}
    discard();const version=captureVersion;requestingMic=true;sync();status('Waiting for microphone permission…');
    try{
      const input=await navigator.mediaDevices.getUserMedia({audio:true});
      if(version!==captureVersion){input.getTracks().forEach(track=>track.stop());return;}
      stream=input;requestingMic=false;
      const mime=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(type=>MediaRecorder.isTypeSupported(type));
      if(!mime)throw new Error('This browser has no supported recording format. Type your question instead.');
      recorder=new MediaRecorder(stream,{mimeType:mime,audioBitsPerSecond:64000});chunks=[];let size=0;
      recorder.ondataavailable=event=>{if(version!==captureVersion)return;size+=event.data.size;if(size>2*1024*1024){discard();status('Recording too large. Try a shorter question.');return;}if(event.data.size)chunks.push(event.data);};
      recorder.onerror=()=>{if(version===captureVersion){discard();status('Microphone recording failed. Try again or type your question.');}};
      recorder.onstop=()=>{
        if(version!==captureVersion)return;clearInterval(clock);clearTimeout(deadline);stream?.getTracks().forEach(track=>track.stop());stream=null;
        recording=new Blob(chunks,{type:mime});chunks=[];recorder=null;
        if(!recording.size){discard();status('No audio captured. Try again.');return;}
        void transcribeAndAsk();
      };
      recorder.start(250);startedAt=Date.now();status('Recording… 0 / 30 seconds.');sync();
      clock=setInterval(()=>status(`Recording… ${Math.floor((Date.now()-startedAt)/1000)} / 30 seconds.`),1000);
      deadline=setTimeout(()=>{if(active())recorder.stop();},30000);
    }catch(error){if(version===captureVersion){discard();status(error.name==='NotAllowedError'?'Microphone access was not granted. You can type your question.':error.message);}}
  };
  $('discard-recording').onclick=discard;
  async function transcribeAndAsk(){
    if(!recording)return;const version=captureVersion,edited=questionVersion;transcribing=true;sync();status('Sending recording to ElevenLabs for transcription…');
    transcriptionController=new AbortController();
    try{
      const response=await fetch(`/api/transcribe?language=${$('guidance-language').value}`,{method:'POST',headers:{'Content-Type':recording.type},body:recording,signal:AbortSignal.any([transcriptionController.signal,AbortSignal.timeout(55000)])});
      const result=await response.json();if(!response.ok)throw new Error(result.error);
      if(version!==captureVersion)return;
      if(edited!==questionVersion){recording=null;status('Your typed question changed while transcribing. It was kept. Choose Send question when ready.');return;}
      $('guidance-question').value=result.text;questionVersion++;
      recording=null;transcribing=false;
      status('');
      await generate(result.text);
    }catch(error){if(version===captureVersion)status(error.name==='AbortError'?'Transcription cancelled.':error.message);}
    finally{if(version===captureVersion){transcribing=false;sync();}}
  }
  $('guidance-dialog').addEventListener('close',()=>{$('open-guidance').hidden=false;$('open-guidance').setAttribute('aria-expanded','false');$('open-guidance').focus();discard();stopAudio();answerVersion++;generating=false;sync();});
  window.addEventListener('pagehide',()=>{discard();stopAudio();});
  function refresh(){sync();if($('guidance-dialog').open&&state.config)void loadVoices();}
  sync();return {reset,refresh};
}
