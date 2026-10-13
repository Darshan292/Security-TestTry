"""Body-bound HMAC attestations from trusted source-side sanitizers."""
from __future__ import annotations
import hashlib, hmac, json, re, secrets, sqlite3, threading, time
from pathlib import Path
SOURCE_HEADER="X-PGW-Input-Source"; TIMESTAMP_HEADER="X-PGW-Input-Timestamp"; NONCE_HEADER="X-PGW-Input-Nonce"; SIGNATURE_HEADER="X-PGW-Input-Signature"
MAX_CLOCK_SKEW_SECONDS=300
_ID=re.compile(r"^[a-z0-9][a-z0-9._@-]{0,63}$"); _NONCE=re.compile(r"^[A-Za-z0-9_-]{16,128}$")
def canonical_body(body): return json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def _message(sanitizer,timestamp,nonce,tenant,app_id,purpose,body):
 digest=hashlib.sha256(canonical_body(body)).hexdigest()
 return json.dumps(["pgw-source-attestation-v1",sanitizer,timestamp,nonce,tenant,app_id,purpose,digest],ensure_ascii=False,separators=(",",":")).encode()
def sign_headers(*,sanitizer,body,key,tenant,app_id,purpose,timestamp=None,nonce=None):
 if not _ID.fullmatch(sanitizer) or not isinstance(key,bytes) or len(key)<32: raise ValueError("invalid sanitizer ID or weak HMAC key")
 ts=str(int(time.time()) if timestamp is None else int(timestamp)); n=nonce or secrets.token_urlsafe(24)
 if not _NONCE.fullmatch(n): raise ValueError("invalid sanitizer nonce")
 sig=hmac.new(key,_message(sanitizer,ts,n,tenant,app_id,purpose,body),hashlib.sha256).hexdigest()
 return {SOURCE_HEADER:sanitizer,TIMESTAMP_HEADER:ts,NONCE_HEADER:n,SIGNATURE_HEADER:sig}
def verify_headers(*,headers,body,keys,tenant,app_id,purpose,now=None):
 sid=str(headers.get(SOURCE_HEADER.lower(),"")).strip(); ts=str(headers.get(TIMESTAMP_HEADER.lower(),"")).strip(); nonce=str(headers.get(NONCE_HEADER.lower(),"")).strip(); sig=str(headers.get(SIGNATURE_HEADER.lower(),"")).strip().lower()
 if not _ID.fullmatch(sid) or sid not in keys or not ts.isascii() or not ts.isdigit() or len(ts)>12 or not _NONCE.fullmatch(nonce) or not re.fullmatch(r"[0-9a-f]{64}",sig): return None
 if abs((int(time.time()) if now is None else int(now))-int(ts))>MAX_CLOCK_SKEW_SECONDS: return None
 key=keys[sid]
 if not isinstance(key,bytes) or len(key)<32: return None
 try: expected=hmac.new(key,_message(sid,ts,nonce,tenant,app_id,purpose,body),hashlib.sha256).hexdigest()
 except (TypeError,ValueError,UnicodeEncodeError): return None
 return sid if hmac.compare_digest(expected,sig) else None
class SQLiteReplayGuard:
 """Atomic one-use nonce storage. Multi-host deployments need a shared transactional store."""
 def __init__(self,path):
  self.path=str(path); self.lock=threading.Lock(); Path(self.path).parent.mkdir(parents=True,exist_ok=True)
  with sqlite3.connect(self.path) as db: db.execute("CREATE TABLE IF NOT EXISTS pgw_attestation_nonce (scope_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)")
 def claim(self,*,sanitizer,nonce,tenant,app_id,expires_at,now=None):
  now=int(time.time()) if now is None else int(now); digest=hashlib.sha256(json.dumps([sanitizer,nonce,tenant,app_id],separators=(",",":")).encode()).hexdigest()
  try:
   with self.lock, sqlite3.connect(self.path,timeout=5,isolation_level="IMMEDIATE") as db:
    db.execute("DELETE FROM pgw_attestation_nonce WHERE expires_at < ?",(now,)); db.execute("INSERT INTO pgw_attestation_nonce VALUES (?,?)",(digest,int(expires_at)))
   return True
  except sqlite3.Error: return False
