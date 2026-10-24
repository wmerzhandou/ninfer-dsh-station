#!/usr/bin/env python3
"""Codex 模型路由网关（本机 ninfer 臂，Responses 透传）。

  model == "qwen3.8-27b-ninfer" → 本机 ninfer-serve（引擎原生 /v1/responses，
  function_call / reasoning.effort=xhigh / store 全部原生支持）。
  臂内做卫生处理（sanitize_for_farm）：
  剥 reasoning 密文条目 + encrypted_content 部件、agent_message→message、effort high→xhigh。
  其它模型 → deepseek。
"""
import json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

DEEPSEEK = "https://api.deepseek.com"
NINFER = "http://127.0.0.1:8092"          # 满血引擎（ninfer-mode full）；切日常时 ninfer-mode 会改回 8091
NINFER_MODEL = "qwen3.8-27b-ninfer"       # 对外统一 slug（codex/dsh 用这个名字）
NINFER_ENGINE_MODEL = "qwen3.8-27b-full"  # 引擎真实 id（转发时改写）
LOG = "/tmp/codex-gateway.log"

# ---- LAN 鉴权：非本机回环必须带 Bearer；令牌存 ~/.codex/gateway.token ----
TOKEN_PATH = os.path.expanduser("~/.codex/gateway.token")
def _load_token():
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH) as f:
            return f.read().strip()
    tok = os.urandom(24).hex()
    with open(TOKEN_PATH, "w") as f:
        f.write(tok + "\n")
    os.chmod(TOKEN_PATH, 0o600)
    return tok
AUTH_TOKEN = _load_token()

def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

def sanitize_for_farm(body: bytes) -> bytes:
    """剥 OpenAI 私有字段（reasoning 密文 / encrypted_content），agent_message→message。

    ninfer 与上游同样不接受这两类部件，统一做这一卫生处理。
    effort high→xhigh 对两边都合法（ninfer 的 effort 枚举含 xhigh）。"""
    try:
        d = json.loads(body)
    except Exception:
        return body
    inp = d.get("input")
    if isinstance(inp, list):
        cleaned = []
        for item in inp:
            if not isinstance(item, dict):
                cleaned.append(item); continue
            if item.get("type") == "reasoning":
                continue
            content = item.get("content")
            if isinstance(content, list):
                parts = [p for p in content
                         if not (isinstance(p, dict) and p.get("type") == "encrypted_content")]
                if not parts:
                    continue
                item = dict(item); item["content"] = parts
            if item.get("type") == "agent_message":
                item = dict(item); item["type"] = "message"; item["role"] = "user"
            cleaned.append(item)
        d["input"] = cleaned
    inc = d.get("include")
    if isinstance(inc, list):
        inc = [i for i in inc if i != "reasoning.encrypted_content"]
        if inc: d["include"] = inc
        else: d.pop("include", None)
    reasoning = d.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort") == "high":
        d["reasoning"] = dict(reasoning)
        d["reasoning"]["effort"] = "xhigh"
    return json.dumps(d, ensure_ascii=False).encode()

def adapt_tools_for_ninfer(body: bytes) -> bytes:
    """ninfer 只认 function 型工具；Codex 发的是 custom（客户端执行）工具。

    custom -> function 是无损映射（name/description/input_schema 与
    function 的 name/description/parameters 同构），模型仍能正常发起
    function_call，由 Codex 在本地执行。其余未知类型记录后剔除。"""
    try:
        d = json.loads(body)
    except Exception:
        return body
    tools = d.get("tools")
    text = d.get("text")
    if isinstance(text, dict):
        text = {k: v for k, v in text.items() if k != "verbosity"}
        if text:
            d["text"] = text
        else:
            d.pop("text", None)
    if not isinstance(tools, list):
        return json.dumps(d, ensure_ascii=False).encode()
    # 嵌套感知：chat/completions 用 {"type":"function","function":{...}}，
    # responses 用扁平 {"type":"function","name":...}——镜像入站形状，避免引擎 400。
    nested = any(isinstance(t, dict) and isinstance(t.get("function"), dict) for t in tools)
    fixed = []
    GENERIC = {"type": "object",
               "properties": {"input": {"type": "string"}},
               "required": ["input"]}
    for t in tools:
        if not isinstance(t, dict):
            continue
        ty = t.get("type")
        if ty in ("function", "custom", "freeform"):
            if isinstance(t.get("function"), dict):  # 嵌套入站：提取内层
                t, ty = t["function"], t["function"].get("type", "function")
            params = t.get("parameters") or t.get("input_schema")
            fn = {"name": t.get("name"),
                  "description": t.get("description") or "",
                  "parameters": params if isinstance(params, dict) else GENERIC}
            if nested:
                fixed.append({"type": "function", "function": fn})
            else:
                fixed.append(dict(fn, type="function"))
        else:
            log(f"NINFER tools: 剔除不支持的类型 {ty} (name={t.get('name')})")
    d["tools"] = fixed
    return json.dumps(d, ensure_ascii=False).encode()

# ---------------- HTTP ----------------

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _headers(self):
        return {k: v for k, v in self.headers.items()
                if k.lower() not in ("host", "content-length", "connection")}
    def _send(self, status, data, ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def _call(self, base, path, body, headers):
        req = Request(base + path, data=body if body else None, headers=headers)
        resp = urlopen(req, timeout=900)
        return resp.status, resp.read()
    def _open(self, base, path, body, headers):
        """打开上游请求并返回原始响应对象（供 _pump 逐块转发）。"""
        req = Request(base + path, data=body if body else None, headers=headers)
        return urlopen(req, timeout=900)
    def _pump(self, resp):
        """逐块转发上游响应（真流式）+ 首字节前每 15s 发 SSE 心跳注释。
        LAN 客户端有流式空闲超时（dsh 默认 5 分钟），而 prefill 期间上游无任何字节
        （TTFT=全量预填），大上下文下必然超时断连——心跳 ': keepalive' 是 SSE 合法
        注释，所有解析器忽略，只用于保活。"""
        import threading
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.headers.get("Content-Type", "application/octet-stream"))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        stop = threading.Event()
        def _beat():
            while not stop.wait(15):
                try:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                except OSError:
                    stop.set()
                    return
        threading.Thread(target=_beat, daemon=True).start()
        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                stop.set()   # 真字节到达，停心跳
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端中途断开（刷新/超时），安静收尾
        finally:
            stop.set()

    def _route_ninfer(self, path, body):
        """v3 新臂：Responses 透传到本机 ninfer（卫生处理 + 工具类型改写 + 引擎模型名改写）。"""
        body = adapt_tools_for_ninfer(sanitize_for_farm(body))
        try:
            d = json.loads(body)
            if d.get("model") == NINFER_MODEL:
                d["model"] = NINFER_ENGINE_MODEL
                body = json.dumps(d).encode()
        except Exception:
            pass
        try:
            resp = self._open(NINFER, path, body, self._headers())
        except HTTPError as e:
            detail = e.read()[:800]
            self._send(e.code, detail)
            log(f"NINFER HTTP {e.code}: {detail.decode('utf-8','replace')[:300]}")
            return
        except (URLError, OSError) as e:
            self._send(502, json.dumps(
                {"error": {"message": f"ninfer 不可达（请检查 ninfer-3090 容器）: {e}"}}).encode())
            log(f"NINFER down: {e}")
            return
        self._pump(resp)
        log(f"POST {path} model={NINFER_MODEL} -> ninfer stream [{resp.status}] ({len(body)}B)")

    def _authorized(self):
        host = self.client_address[0]
        if host in ("127.0.0.1", "::1", "localhost"):
            return True
        return self.headers.get("Authorization") == f"Bearer {AUTH_TOKEN}"

    def _route(self):
        if not self._authorized():
            self._send(401, json.dumps({"error": {
                "message": "LAN 访问需要 Authorization: Bearer <token>（见 ~/.codex/gateway.token）"}}).encode())
            log(f"401 from {self.client_address[0]} {self.path}")
            return
        path = self.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        if "models" in path and not body:
            merged, seen = [], set()
            for base in (NINFER, DEEPSEEK):
                try:
                    d = json.loads(urlopen(Request(base + "/v1/models", headers=self._headers()), timeout=15).read())
                    for m in d.get("data", []):
                        if m.get("id") not in seen:
                            seen.add(m.get("id")); merged.append(m)
                except Exception as e:
                    log(f"models merge fail {base}: {e}")
            self._send(200, json.dumps({"object": "list", "data": merged}).encode())
            log(f"GET {path} -> merged {len(merged)} models")
            return
        try:
            model = json.loads(body).get("model", "") if body else ""
        except Exception:
            model = ""
        if model in (NINFER_MODEL, NINFER_ENGINE_MODEL):   # ninfer 臂（对外 slug + 引擎实名都收）
            self._route_ninfer(path, body)
        else:
            try:
                resp = self._open(DEEPSEEK, path, body, self._headers())
            except HTTPError as e:
                status, data = e.code, e.read()
                ctype = "text/event-stream" if b"data:" in data[:300] else "application/json"
                self._send(status, data, ctype)
                log(f"POST {path} model={model or '-'} -> deepseek HTTP {status} (buffered err)")
                return
            self._pump(resp)
            log(f"POST {path} model={model or '-'} -> deepseek stream [{resp.status}]")
    do_POST = _route
    def do_GET(self):
        if "models" in self.path:
            self._route()
        else:
            self._send(404, b'{"error":"not found"}')
    def log_message(self, *a): pass

if __name__ == "__main__":
    # backlog 128：默认 5，LAN 多客户端+流式并发下会被冲爆导致 connect error
    class Server(ThreadingHTTPServer):
        request_queue_size = 128
        daemon_threads = True
    Server(("0.0.0.0", 18003), H).serve_forever()
