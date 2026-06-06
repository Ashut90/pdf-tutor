"""
Unified AI client supporting four providers:
  - Ollama (local, OpenAI-incompatible native API)
  - Groq (OpenAI-compatible)
  - OpenRouter (OpenAI-compatible)
  - Google Gemini (its own streaming format)

All methods stream tokens via an on_chunk callback for responsive UI.
"""
import json
import time
import socket
import subprocess
import urllib.request
import urllib.error


def ensure_ollama():
    try:
        socket.create_connection(("localhost", 11434), timeout=1).close()
        return True
    except OSError:
        pass
    try:
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        time.sleep(2)
        return True
    except FileNotFoundError:
        return False


# ── AI client ─────────────────────────────────────────────────────────────────


class AIClient:
    @staticmethod
    def _fit_num_ctx(messages, num_predict):
        """Size the context window to the actual content, not a fixed max.

        Ollama reserves KV-cache memory for the FULL num_ctx regardless of how
        much input there is. On a small GPU (e.g. 6GB), a fixed 16K ctx forces
        the model to spill out of VRAM even for a 3-page topic — slow. Sizing
        ctx to (input + output) keeps small topics in VRAM (fast) while big
        chapters still get the room they need.
        """
        chars = sum(len(m.get("content", "")) for m in messages)
        # Reserve a realistic output budget (~3500 tok), not the full num_predict
        # cap — most teaching answers are 1-3k tokens. A 3-page topic then lands
        # in the fast 8192 bucket (benchmarked at 44s, fully in 6GB VRAM).
        out_reserve = min(num_predict, 3500)
        needed = chars // 4 + out_reserve + 512   # ~4 chars/token + headroom
        for bucket in (4096, 8192, 12288, 16384):
            if needed <= bucket:
                return bucket
        return 16384  # cap — beyond this, cloud providers are the better path

    def chat(self, pid, model, key, messages, on_chunk=None):
        if pid == "ollama":
            ensure_ollama()
            num_predict = 6000
            return self._stream("http://localhost:11434/api/chat",
                {"model": model, "messages": messages, "stream": True,
                 "options": {
                     # Tuned via benchmark against TLPI on qwen2.5-coder:7b (RTX 2060 6GB).
                     # repeat_penalty 1.3 was choking generation (halved output);
                     # temp 0.7 caused drift/made-up content on factual teaching.
                     "num_predict": num_predict,   # allow long, detailed answers
                     # Adaptive: small topics stay in VRAM (fast); big chapters grow.
                     "num_ctx": self._fit_num_ctx(messages, num_predict),
                     "temperature": 0.35,          # factual, low drift for teaching
                     "repeat_penalty": 1.1,        # standard — higher chokes technical terms
                     "top_p": 0.9,
                     "top_k": 40,
                 }},
                {}, on_chunk, ollama=True)
        if pid == "groq":
            # Free-tier: total tokens must stay under ~12K. Output 4000 + input budget = workable.
            return self._stream("https://api.groq.com/openai/v1/chat/completions",
                {"model": model, "messages": messages, "stream": True,
                 "max_tokens": 4000, "temperature": 0.4},
                {"Authorization": f"Bearer {key}", "User-Agent": "curl/8.0", "Accept": "*/*"},
                on_chunk)
        if pid == "openrouter":
            return self._stream("https://openrouter.ai/api/v1/chat/completions",
                {"model": model, "messages": messages, "stream": True,
                 "max_tokens": 4000, "temperature": 0.4},
                {"Authorization": f"Bearer {key}", "HTTP-Referer": "ai-teacher", "X-Title": "AI Teacher",
                 "User-Agent": "curl/8.0", "Accept": "*/*"},
                on_chunk)
        if pid == "gemini":
            return self._gemini_stream(model, key, messages, on_chunk)

    def _gemini_stream(self, model, key, messages, on_chunk=None):
        """Google Gemini uses a different API format than OpenAI-compatible providers."""
        # Convert OpenAI-format messages to Gemini format
        system_text = ""
        gemini_contents = []
        for msg in messages:
            role = msg["role"]
            text = msg["content"] if isinstance(msg["content"], str) else str(msg["content"])
            if role == "system":
                system_text += text + "\n"
            elif role == "user":
                gemini_contents.append({"role": "user", "parts": [{"text": text}]})
            elif role == "assistant":
                gemini_contents.append({"role": "model", "parts": [{"text": text}]})

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={key}"
        payload = {
            "contents": gemini_contents,
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 8000,
            },
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                      headers={"Content-Type": "application/json",
                                               "User-Agent": "curl/8.0"})
        full = []
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                for raw in r:
                    line = raw.decode().strip()
                    if not line.startswith("data:"):
                        continue
                    line = line[5:].strip()
                    if not line or line == "[DONE]":
                        continue
                    try:
                        obj = json.loads(line)
                        candidates = obj.get("candidates", [])
                        if not candidates:
                            continue
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for p in parts:
                            text = p.get("text", "")
                            if text:
                                full.append(text)
                                if on_chunk:
                                    on_chunk(text)
                    except json.JSONDecodeError:
                        continue
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode()
                err_obj = json.loads(body) if body.startswith("{") else {}
                err_msg = err_obj.get("error", {}).get("message", body[:200])
            except Exception:
                err_msg = f"HTTP {e.code}"
            raise RuntimeError(f"Gemini API error: {err_msg}")
        except urllib.error.URLError as e:
            raise ConnectionError(f"Cannot reach Gemini API: {e.reason}")
        return "".join(full)

    def _stream(self, url, payload, extra, on_chunk, ollama=False):
        headers = {"Content-Type": "application/json", **extra}
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
        full = []
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                for raw in r:
                    line = raw.decode().strip()
                    # Detect Ollama error messages embedded in stream
                    if ollama and line.startswith("{") and '"error"' in line:
                        try:
                            err = json.loads(line).get("error", "")
                            if err:
                                raise RuntimeError(f"Ollama error: {err}")
                        except json.JSONDecodeError:
                            pass
                    chunk = self._parse(line, ollama)
                    if chunk:
                        full.append(chunk)
                        if on_chunk:
                            on_chunk(chunk)
        except urllib.error.HTTPError as e:
            # Read the actual error body to show useful message
            try:
                body = e.read().decode()
                # Try to extract Ollama-style error
                if body.startswith("{"):
                    err_obj = json.loads(body)
                    err_msg = err_obj.get("error", body[:200])
                else:
                    err_msg = body[:200]
            except Exception:
                err_msg = f"HTTP {e.code} {e.reason}"
            if ollama:
                # Most common: model not installed
                if "not found" in err_msg.lower() or "model" in err_msg.lower():
                    raise RuntimeError(f"Model not installed. Run: ollama pull {payload.get('model','MODEL')}\n\nFull error: {err_msg}")
                raise RuntimeError(f"Ollama API error: {err_msg}")
            raise RuntimeError(f"HTTP {e.code}: {err_msg}")
        except urllib.error.URLError as e:
            if ollama:
                raise ConnectionError(f"Ollama not reachable at localhost:11434. Start it first: 'ollama serve' (Linux: sudo systemctl start ollama).\n\nDetails: {e.reason}")
            raise
        return "".join(full)

    def _parse(self, line, ollama):
        if not line:
            return ""
        if ollama:
            try:
                return json.loads(line).get("message", {}).get("content", "")
            except Exception:
                return ""
        if not line.startswith("data:"):
            return ""
        line = line[5:].strip()
        if line == "[DONE]":
            return ""
        try:
            return json.loads(line)["choices"][0]["delta"].get("content", "")
        except Exception:
            return ""


# ════════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ════════════════════════════════════════════════════════════════════════════════
