"""Independent end-to-end regressions for the actual gateway egress/response boundary."""
import json, os, time
from pathlib import Path
import pytest
from starlette.testclient import TestClient
from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product.attestation import sign_headers
from pgw.stream import Restorer
from pgw.types import EntityType
from tests.sealed import ROOT, TEST_SANITIZER_KEYS, production, sha, source

KEY="root-cause-test-key"
TOKEN="ghp_TEST_ONLY_NOT_A_REAL_CREDENTIAL_000000000000"
SUBJ={"requester":{"name":"Ria Sen","employee_id":"KD-TEST-7821"}}
SAP=[
 ("key-value", "SAP HCM PA0008 Basic Pay: ANSAL=1860000 | WAERS=INR", ["1860000"]),
 ("json", '{"PA0008":{"PERNR":"00007821","ANSAL":1860000,"WAERS":"INR"}}', ["1860000"]),
 ("xml", "<PA0008><PERNR>00007821</PERNR><ANSAL>1860000</ANSAL><WAERS>INR</WAERS></PA0008>", ["1860000"]),
 ("csv", "PERNR,ANSAL,WAERS\n00007821,1860000,INR\n", ["1860000"]),
 ("alternate-label", "Annual salary INR 18,60,000; annual compensation 2,050,000 INR", ["18,60,000","2,050,000"]),
]

def make_client(tmp_path, upstream, purposes=("ticket_summary","hr_case_explanation")):
 tasks={p:str(ROOT/"contracts"/f"{p}.yaml") for p in purposes}
 app={"app_id":"pilot","tenant":"acme","key_sha256":sha(KEY),"purposes":list(purposes),"restore_output":True,"models":["mock-1"],"tasks":tasks}
 cfg=production(tmp_path,[app])
 cfg.update(ledger_db_path=str(tmp_path/"ledger.sqlite"),egress_ledger_path=str(tmp_path/"egress.jsonl"),master_key=os.urandom(32).hex(),upstream=upstream,ner_model=None)
 return TestClient(create_app(Settings(**cfg))),upstream

def post(client,purpose,text,stream=False):
 body={"model":"mock-1","stream":stream,"messages":[{"role":"user","content":text}],"pgw_subjects":SUBJ}
 headers={"Authorization":f"Bearer {KEY}","X-PGW-Purpose":purpose,**source(purpose,body,app_id="pilot",tenant="acme")}
 return client.post("/v1/chat/completions",json=body,headers=headers),body

@pytest.mark.parametrize("case,text,canaries",SAP,ids=[x[0] for x in SAP])
def test_compensation_fields_do_not_cross_provider_boundary(tmp_path,case,text,canaries):
 up=MockLLMUpstream("summarise"); client,_=make_client(tmp_path,up,("hr_case_explanation",))
 r,_=post(client,"hr_case_explanation","Synthetic HR case:\n"+text)
 assert r.status_code==200,r.text
 wire=up.received[-1].decode()
 for value in canaries: assert value not in wire,(case,value,wire)

class Echo(MockLLMUpstream):
 def __init__(self,value,tool=False): super().__init__("summarise"); self.value=value; self.tool=tool
 async def complete(self,body):
  self._prepare(body)
  message={"role":"assistant","content":self.value}
  finish="stop"
  if self.tool:
   message={"role":"assistant","content":None,"tool_calls":[{"id":"call_test","type":"function","function":{"name":"search_case","arguments":json.dumps({"query":self.value})}}]}; finish="tool_calls"
  return {"id":"echo","object":"chat.completion","created":0,"model":body.get("model","mock-1"),"choices":[{"index":0,"message":message,"finish_reason":finish}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}
 async def stream(self,body):
  self._prepare(body)
  yield json.dumps({"choices":[{"index":0,"delta":{"content":self.value},"finish_reason":None}]})
  yield json.dumps({"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]})

@pytest.mark.parametrize("stream,tool",[(False,False),(True,False),(False,True)])
def test_withheld_credential_echo_is_blocked_for_content_stream_and_tool_arguments(tmp_path,stream,tool):
 up=Echo(TOKEN,tool); client,_=make_client(tmp_path,up,("ticket_summary",))
 r,_=post(client,"ticket_summary",f"Synthetic incident. Authorization: Bearer {TOKEN}.",stream)
 assert r.status_code==200,r.text
 assert TOKEN not in up.received[-1].decode(),"credential canary reached provider"
 assert TOKEN not in r.text,"provider echoed a known-withheld value to caller"

@pytest.mark.parametrize("value",["1860000","1,860,000","1 860 000","1.860.000"])
def test_known_withheld_numeric_variants_are_gated(value):
 gate=Restorer({},restore=False,mode="buffered",known_withheld=((EntityType.COMPENSATION,"1860000"),))
 out=gate.feed("pay="+value)+gate.finish()
 assert value not in out and "[COMPENSATION]" in out

def test_signed_attestation_replay_is_rejected(tmp_path):
 up=MockLLMUpstream("summarise"); client,_=make_client(tmp_path,up,("ticket_summary",))
 body={"model":"mock-1","messages":[{"role":"user","content":"Synthetic status only"}],"pgw_subjects":SUBJ}
 h={"Authorization":f"Bearer {KEY}","X-PGW-Purpose":"ticket_summary",**source("ticket_summary",body,app_id="pilot",tenant="acme")}
 first=client.post("/v1/chat/completions",json=body,headers=h); second=client.post("/v1/chat/completions",json=body,headers=h)
 assert first.status_code==200,first.text
 assert second.status_code==422,second.text
 assert len(up.received)==1

def test_attestation_context_and_body_are_bound():
 from pgw.product.attestation import verify_headers
 body={"model":"mock-1","messages":[{"role":"user","content":"sanitized synthetic text"}]}
 sid="helpdesk-ticket-export@1"; key=TEST_SANITIZER_KEYS[sid]
 h=sign_headers(sanitizer=sid,body=body,key=key,tenant="acme",app_id="pilot",purpose="ticket_summary",timestamp=int(time.time()),nonce="abcdefghijklmnopQRSTUV")
 assert verify_headers(headers={k.lower():v for k,v in h.items()},body=body,keys=TEST_SANITIZER_KEYS,tenant="acme",app_id="pilot",purpose="ticket_summary") == sid
 assert verify_headers(headers={k.lower():v for k,v in h.items()},body={**body,"extra":"unsafe"},keys=TEST_SANITIZER_KEYS,tenant="acme",app_id="pilot",purpose="ticket_summary") is None
 assert verify_headers(headers={k.lower():v for k,v in h.items()},body=body,keys=TEST_SANITIZER_KEYS,tenant="other",app_id="pilot",purpose="ticket_summary") is None
