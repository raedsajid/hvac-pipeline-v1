from .store import write
"""Gemini REST client. Saved keys retain the previous AES-GCM wire format."""
import base64
import json
import math
import re
import secrets
import urllib.error
import urllib.request
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app
from .store import AppError, one, run, consume_limit, sha

def chat_model(): return current_app.config['GEMINI_CHAT_MODEL']
def embed_model(): return current_app.config['EMBEDDING_MODEL']
def configured(): return bool(one("SELECT key FROM settings WHERE key='gemini_key'") or current_app.config.get('GEMINI_API_KEY'))

def encryption_key():
    secret=current_app.config.get('SETTINGS_SECRET')
    if not secret: raise AppError(503,'Set SETTINGS_SECRET before saving an API key.')
    return bytes.fromhex(sha(secret))

def save_key(key):
    if not isinstance(key,str) or not re.fullmatch(r'AIza[\w-]{20,}',key): raise AppError(400,'Enter a valid Google AI Studio API key.')
    iv=secrets.token_bytes(12); data=AESGCM(encryption_key()).encrypt(iv,key.encode(),None)
    value=base64.b64encode(iv).decode()+'.'+base64.b64encode(data).decode()
    write("INSERT INTO settings VALUES ('gemini_key',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",value)

def api_key():
    row=one("SELECT value FROM settings WHERE key='gemini_key'")
    if not row:
        key=current_app.config.get('GEMINI_API_KEY')
        if key: return key
        raise AppError(503,'The owner needs to connect Gemini in Settings.','key_missing')
    try:
        iv,data=row['value'].split('.')
        return AESGCM(encryption_key()).decrypt(base64.b64decode(iv),base64.b64decode(data),None).decode()
    except AppError: raise
    except Exception as exc: raise AppError(503,'Saved API key could not be decrypted. Check SETTINGS_SECRET.') from exc

def call(path,body,uid):
    key=api_key(); consume_limit('ai:global',10000); consume_limit('ai:user:'+uid,10000)
    req=urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/'+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','x-goog-api-key':key},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=90) as response: return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code==429: raise AppError(429,'Gemini quota reached. Progress is saved.','provider_quota') from exc
        raise AppError(502,'Gemini rejected the request. Check the key and configured model in Settings.','provider_rejected') from exc
    except (urllib.error.URLError,TimeoutError,ValueError) as exc: raise AppError(502,'Gemini is unavailable. Your progress is saved.','provider_unavailable') from exc

def generate(system,text,uid,pdf=None):
    if len(text)>26000: raise AppError(413,'This extraction batch is too large.')
    parts=[{'text':text}]
    if pdf: parts.append({'inlineData':{'mimeType':'application/pdf','data':base64.b64encode(pdf).decode()}})
    result=call('models/'+chat_model()+':generateContent',{'systemInstruction':{'parts':[{'text':system}]},'contents':[{'role':'user','parts':parts}],'generationConfig':{'temperature':0,'responseMimeType':'application/json','maxOutputTokens':65536,'thinkingConfig':{'thinkingBudget':0}}},uid)
    candidates=result.get('candidates') or []; c=candidates[0] if candidates else {}; finish=c.get('finishReason'); output=''.join(p.get('text','') for p in c.get('content',{}).get('parts',[]) if not p.get('thought'))
    if result.get('promptFeedback',{}).get('blockReason') or finish not in ('STOP','MAX_TOKENS'): raise AppError(502,'AI returned an incomplete or blocked response. Retry this batch.','incomplete_output')
    try: return json.loads(output)
    except ValueError as exc: raise AppError(502,'AI returned incomplete JSON. Retry this batch.','truncated_output' if finish=='MAX_TOKENS' else 'invalid_output') from exc

def embed(texts,uid,query=False):
    model=embed_model()
    result=call('models/'+model+':batchEmbedContents',{'requests':[{'model':'models/'+model,'content':{'parts':[{'text':text[:5000]}]},'taskType':'RETRIEVAL_QUERY' if query else 'RETRIEVAL_DOCUMENT','outputDimensionality':768} for text in texts]},uid)
    vectors=[e.get('values') for e in result.get('embeddings',[])]
    if len(vectors)!=len(texts) or any(not isinstance(v,list) or len(v)!=768 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) for v in vectors): raise AppError(502,'AI returned invalid embeddings.')
    return [[x/(math.sqrt(sum(n*n for n in v)) or 1) for x in v] for v in vectors]
