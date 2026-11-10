"""Small Ark Chat Completions client using only the Python standard library."""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_MODEL = "doubao-seed-2-1-lite-260915"
API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_local_env():
    """Read ignored project-local settings, without overriding the shell."""
    path = PROJECT_ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


class ModelError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ArkClient:
    def __init__(self, model=None, timeout=60):
        load_local_env()
        self.model = model or os.getenv("ARK_MODEL", DEFAULT_MODEL)
        self._key = os.getenv("ARK_API_KEY", "")
        if not self._key or self._key == "replace_with_your_local_key":
            raise ModelError("未配置 ARK_API_KEY，请在项目根目录的本地 .env 设置。")
        self.timeout = timeout
        self.history = []
        self.opener = urllib.request.build_opener(NoRedirect())

    def safe_error(self, text):
        return str(text).replace(self._key, "[REDACTED]")[:600]

    def complete(self, messages, tools=None, purpose="agent", max_tokens=1600, json_output=False):
        payload = {"model": self.model, "messages": messages,
                   "max_completion_tokens": max_tokens, "reasoning_effort": "minimal"}
        if tools:
            payload["tools"] = tools
        if json_output:
            payload["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            API_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"},
            method="POST")
        started = time.perf_counter()
        log = {"purpose": purpose, "elapsed_ms": None, "usage": None,
               "finish_reason": None, "error": None}
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            choice = result["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError("response.message is not an object")
            log["usage"] = result.get("usage")
            log["finish_reason"] = choice.get("finish_reason")
            return {"message": message, "usage": log["usage"],
                    "finish_reason": log["finish_reason"]}
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", errors="replace")
            log["error"] = self.safe_error(f"HTTP {exc.code}: {detail}")
            raise ModelError(log["error"]) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            log["error"] = self.safe_error(f"{type(exc).__name__}: {exc}")
            raise ModelError(log["error"]) from None
        finally:
            log["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
            self.history.append(log)


def pricing_from_env():
    load_local_env()
    try:
        return {"input_per_million": float(os.environ["ARK_INPUT_PRICE_PER_MILLION"]),
                "output_per_million": float(os.environ["ARK_OUTPUT_PRICE_PER_MILLION"]),
                "currency": "CNY"}
    except (KeyError, ValueError):
        return None


def probe():
    client = ArkClient()
    result = client.complete([{"role": "user", "content": "请只回复：连接成功"}],
                             purpose="probe", max_tokens=64)
    return {"model": client.model, "answer": result["message"].get("content"),
            "usage": result["usage"], "elapsed_ms": client.history[-1]["elapsed_ms"]}


if __name__ == "__main__":
    try:
        print(json.dumps(probe(), ensure_ascii=False, indent=2))
    except ModelError as error:
        print(str(error))
        raise SystemExit(1)
