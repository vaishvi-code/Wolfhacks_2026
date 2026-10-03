export const LANGUAGES = Object.freeze({en:'English',es:'Spanish'});
export const MAX_RECORDING_BYTES = 2 * 1024 * 1024;
export const AUDIO_TYPES = Object.freeze({'audio/webm':'webm','audio/ogg':'ogg','audio/mp4':'m4a','audio/wav':'wav','audio/mpeg':'mp3'});
const fail=(message,status=400)=>Object.assign(new Error(message),{status});
const validVoice=id=>typeof id==='string'&&/^[a-zA-Z0-9_-]{1,100}$/.test(id);
let voiceCache;

export function guidanceOptions(body={}) {
  const language=body.language??'en',question=body.question??'';
  if(!Object.hasOwn(LANGUAGES,language))throw fail('Choose English or Spanish.');
  if(typeof question!=='string'||question.length>600)throw fail('Keep your question within 600 characters.');
  return {language,question:question.trim()};
}

export async function listVoices() {
  const key=process.env.ELEVENLABS_API_KEY;
  if(!key)throw fail('Add ELEVENLABS_API_KEY to enable voice selection.',503);
  if(voiceCache?.key===key&&voiceCache.expires>Date.now())return voiceCache.voices;
  // Offer stock voices; avoid exposing private voice metadata or adding library voices.
  const response=await fetch('https://api.elevenlabs.io/v2/voices?voice_type=default&page_size=100',{headers:{'xi-api-key':key},signal:AbortSignal.timeout(15000),redirect:'error'});
  if(!response.ok)throw fail(`Voice list unavailable (HTTP ${response.status}). Check Voices: Read permission.`,502);
  const data=await response.json();
  if(!Array.isArray(data.voices))throw fail('ElevenLabs returned an invalid voice list.',502);
  const voices=data.voices.filter(v=>validVoice(v.voice_id)&&typeof v.name==='string').map(v=>({id:v.voice_id,name:v.name.slice(0,100)}));
  voiceCache={key,voices,expires:Date.now()+5*60000};return voices;
}

export async function resolveVoice(requested) {
  const voice=requested||process.env.ELEVENLABS_VOICE_ID;
  if(!validVoice(voice))throw fail('Choose a voice before listening.');
  if(voice===process.env.ELEVENLABS_VOICE_ID)return voice;
  if(!(await listVoices()).some(v=>v.id===voice))throw fail('Choose a voice from the available list.');
  return voice;
}

export async function transcribeRecording(bytes,type,language='en') {
  guidanceOptions({language});
  const mime=type?.split(';')[0]?.trim();
  if(!Object.hasOwn(AUDIO_TYPES,mime))throw fail('Use a WebM, Ogg, MP4, WAV, or MP3 audio recording.',415);
  if(!bytes?.length||bytes.length>MAX_RECORDING_BYTES)throw fail('Recording must be between 1 byte and 2 MB.',413);
  if(!process.env.ELEVENLABS_API_KEY)throw fail('Add ELEVENLABS_API_KEY to enable transcription.',503);
  const form=new FormData();form.set('model_id','scribe_v2');form.set('language_code',language);
  form.set('tag_audio_events','false');form.set('diarize','false');form.set('timestamps_granularity','none');
  form.set('file',new Blob([bytes],{type:mime}),`question.${AUDIO_TYPES[mime]}`);
  const response=await fetch('https://api.elevenlabs.io/v1/speech-to-text',{method:'POST',headers:{'xi-api-key':process.env.ELEVENLABS_API_KEY},body:form,signal:AbortSignal.timeout(45000),redirect:'error'});
  if(!response.ok)throw fail(`Transcription unavailable (HTTP ${response.status}). Check Speech to Text permission and credits.`,502);
  const data=await response.json();
  if(typeof data.text!=='string'||!data.text.trim())throw fail('No speech was recognized. Try again or type your question.',422);
  return {text:data.text.trim().slice(0,600),truncated:data.text.trim().length>600,provider:'ElevenLabs Scribe',language};
}
