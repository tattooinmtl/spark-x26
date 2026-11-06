#!/usr/bin/env python3
"""Streaming, append-only history, thinking budget and Esc cancel, against a fake llama-server.

Needs no model: python harness\\stream_test.py
"""
import json, sys, threading, time, types, os, tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
H = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, H)
import spark_agent as a, tui

SCRIPT = []      # list of (delay_before, [pieces], stop_type)
PROMPTS = []
class Fake(BaseHTTPRequestHandler):
    def log_message(self,*x): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        PROMPTS.append(body)
        delay, pieces, stop_type = SCRIPT.pop(0)
        self.send_response(200); self.send_header("Content-Type","text/event-stream"); self.end_headers()
        try:
            time.sleep(delay)
            n = 0
            for p in pieces:
                if n >= body["n_predict"]: stop_type = "limit"; break
                self.wfile.write(f"data: {json.dumps({'content':p,'stop':False})}\n\n".encode()); self.wfile.flush(); n += 1
                time.sleep(0.002)
            fin = {"content":"","stop":True,"stop_type":stop_type,"tokens_predicted":n,"tokens_evaluated":100,
                   "timings":{"prompt_n":10,"cache_n":90,"predicted_per_second":7.0}}
            self.wfile.write(f"data: {json.dumps(fin)}\n\n".encode()); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError): pass
srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake); threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]

ws = Path(tempfile.mkdtemp()); (ws/"hello.txt").write_text("hi")
cfg = types.SimpleNamespace(host="127.0.0.1", port=port, timeout=30, temp=1.0, top_p=0.95, top_k=0, repeat=1.0,
    max_tokens=8192, think="high", think_budget=0, max_steps=6, transcript_dir=str(ws/"logs"), tmp_dir=ws/"tmp",
    model="m.gguf", ctx=32768)
eng = a.ServerEngine(cfg); eng.running = True
tmpl = a.SparkTemplate(dict(a.FALLBACK_TOKENS))
agent = a.SparkAgent(cfg, eng, a.Workspace(ws), [], a.build_tools([]), tmpl)
ok = True
def check(label, cond):
    global ok; print(("PASS " if cond else "FAIL ") + label); ok = ok and cond

# 1: think + tool call, then answer
SCRIPT += [(0.3, list("let me look")+["</think>","\n\nChecking.","<tool_","call>list_dir<arg_key>path</arg_key><arg_value>.</arg_value></tool_call>"], "eos"),
           (0.0, list("done")+["</think>","Found ","hello.txt."], "eos")]
ans = agent.turn("what files?")
check("answer returned", ans == "Found hello.txt.")
check("final answer kept in history", agent.messages[-1]["role"] == "assistant" and agent.messages[-1]["raw"].endswith("hello.txt."))
p1, p2 = PROMPTS[0]["prompt"], PROMPTS[1]["prompt"]
raw1 = agent.messages[2]["raw"]
check("append-only: prompt 2 starts with prompt 1 + raw output", p2.startswith(p1 + raw1))
check("stream + id_slot sent", PROMPTS[0]["stream"] is True and PROMPTS[0]["id_slot"] == 0)
check("cache % reported", round(agent.stats.get("cache_pct", 0)) == 90)
# next user turn is append-only too
SCRIPT += [(0.0, ["ok</think>","Sure."], "eos")]
agent.turn("thanks")
check("append-only across user turns", PROMPTS[2]["prompt"].startswith(p2 + agent.messages[-3]["raw"]))
check("last reasoning kept", agent.last_reasoning == "ok")

# 2: thinking budget
cfg.think_budget = 20
SCRIPT += [(0.0, ["x"]*500, "eos"), (0.0, ["Answer."], "eos")]
n0 = len(PROMPTS)
ans = agent.turn("think hard")
check("budget stops thinking and continues", ans == "Answer." and len(PROMPTS) == n0 + 2)
check("continuation prompt = prompt + raw + closing", PROMPTS[n0+1]["prompt"].endswith("x"*20 + "\n</think>\n\n") or PROMPTS[n0+1]["prompt"].count("x") >= 20)
check("history raw holds the forced close", "</think>" in agent.messages[-1]["raw"])
cfg.think_budget = 0

# 3: output limit inside thinking -> never shown as answer
cfg.max_tokens = 30
SCRIPT += [(0.0, ["y"]*100, "eos")]
ans = agent.turn("loop")
check("limit inside think gives empty answer, not the reasoning", ans == "" and agent.messages[-1]["raw"].endswith("</think>"))
cfg.max_tokens = 8192

# 4: Esc cancels during prompt processing
SCRIPT += [(3.0, ["never"], "eos")]
t0 = time.time(); calls = {"n": 0}
def fake_esc():
    calls["n"] += 1; return time.time() - t0 > 0.5
orig = tui.esc_pressed; tui.esc_pressed = fake_esc
try:
    agent.turn("slow"); check("cancel raised", False)
except a.GenerationCancelled:
    check("Esc cancels within ~1s", time.time() - t0 < 1.5)
tui.esc_pressed = orig

# 5: template renders legacy messages (no raw) unchanged
legacy = [{"role":"system","content":"s"},{"role":"user","content":"u"},
          {"role":"assistant","content":"c","reasoning_content":"r","tool_calls":[{"name":"t","arguments":{"a":1}}]}]
check("legacy history still renders", "<think>r</think>c<tool_call>t<arg_key>a</arg_key><arg_value>1</arg_value></tool_call>" in tmpl.render(legacy))
print("ALL PASS" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
