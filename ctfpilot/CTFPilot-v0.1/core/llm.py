"""LLM backend adapter with graceful degradation.

Priority: local Ollama -> intranet OpenAI-compatible gateway -> mock planner.
No third-party deps: uses urllib only. All calls carry short timeouts so a
dead backend never stalls a solve.
"""
import json
import urllib.request
import urllib.error


class LLMResult:
    def __init__(self, text, backend, model, ok=True, error=""):
        self.text = text
        self.backend = backend
        self.model = model
        self.ok = ok
        self.error = error


class LLMRouter:
    def __init__(self, cfg, log=print):
        self.backends = cfg.get("llm", {}).get("backends", [])
        self.mock_fallback = cfg.get("llm", {}).get("mock_fallback", True)
        self.log = log
        self._probe_cache = {}

    def _probe(self, be):
        name = be.get("name")
        if name in self._probe_cache:
            return self._probe_cache[name]
        base = be.get("base_url", "")
        ok = False
        try:
            if be.get("type") == "ollama":
                req = urllib.request.Request(base + "/api/tags", method="GET")
                with urllib.request.urlopen(req, timeout=3) as r:
                    ok = r.status == 200
            else:
                req = urllib.request.Request(base + "/models", method="GET")
                with urllib.request.urlopen(req, timeout=3) as r:
                    ok = r.status == 200
        except Exception:
            ok = False
        self._probe_cache[name] = ok
        self.log("[llm] probe %s -> %s" % (name, "up" if ok else "down"))
        return ok

    def available_backend(self):
        for be in self.backends:
            if self._probe(be):
                return be
        return None

    def chat(self, system, user, max_tokens=2048, temperature=0.2):
        be = self.available_backend()
        if be is None:
            if self.mock_fallback:
                return LLMResult("", "mock", "mock", ok=False,
                                 error="no backend reachable")
            return LLMResult("", "none", "none", ok=False, error="no backend")
        try:
            if be.get("type") == "ollama":
                return self._chat_ollama(be, system, user, max_tokens, temperature)
            return self._chat_openai(be, system, user, max_tokens, temperature)
        except Exception as e:
            return LLMResult("", be.get("name"), "", ok=False, error=str(e))

    def _chat_ollama(self, be, system, user, max_tokens, temperature):
        model = be.get("models", [None])[0]
        payload = {
            "model": model,
            "prompt": user,
            "system": system,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        req = urllib.request.Request(
            be["base_url"] + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=be.get("timeout_s", 120)) as r:
            data = json.loads(r.read().decode("utf-8"))
        return LLMResult(data.get("response", ""), be["name"], model)

    def _chat_openai(self, be, system, user, max_tokens, temperature):
        model = be.get("models", [None])[0]
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        req = urllib.request.Request(
            be["base_url"] + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + be.get("api_key", "EMPTY")},
            method="POST")
        with urllib.request.urlopen(req, timeout=be.get("timeout_s", 120)) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        return LLMResult(text, be["name"], model)
