"""Real Qwen decision adapter and explicitly separate offline process simulator."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class ModelError(RuntimeError):
    pass


def decode_action(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ModelError("model action must be a JSON object")
    text = value.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    try:
        result = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ModelError("model returned invalid JSON action") from exc
    if not isinstance(result, dict):
        raise ModelError("model action must be an object")
    return result


class QwenModel:
    mode = "live"
    model_name = "qwen3.8-max"

    def __init__(self, api_key=None, base_url=None, model=None, timeout=45):
        self._api_key = api_key if api_key is not None else os.environ.get("DASHSCOPE_API_KEY")
        self.base_url = (base_url or os.environ.get("QWEN_BASE_URL") or
                         "https://maas.qianwenaiapi.com/compatible-mode/v1").rstrip("/")
        requested_model = model or os.environ.get("QWEN_MODEL", "qwen3.8-max")
        if requested_model != self.model_name:
            raise ModelError("live mode requires QWEN_MODEL=qwen3.8-max")
        if not self._api_key:
            raise ModelError("DASHSCOPE_API_KEY is required for live mode")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ModelError("QWEN_BASE_URL must be an HTTPS base URL without credentials or query")
        self.timeout = timeout
        self.last_metadata = None

    def next_action(self, messages):
        data = json.dumps({"model": self.model_name, "messages": messages, "temperature": 0.1,
                           "max_tokens": 1200, "stream": False,
                           "enable_thinking": False,
                           "response_format": {"type": "json_object"}}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + "/chat/completions", data=data,
                                         headers={"Authorization": "Bearer " + self._api_key,
                                                  "Content-Type": "application/json"}, method="POST")
        started = time.perf_counter()
        self.last_metadata = {"requested_model": self.model_name}
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ModelError(f"Qwen HTTP request failed with status {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ModelError("Qwen network request failed or timed out") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ModelError("Qwen response was not valid JSON") from None
        self.last_metadata = {"requested_model": self.model_name, "response_model": result.get("model"),
                              "response_id": result.get("id"), "usage": result.get("usage"),
                              "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ModelError("Qwen response did not contain a decision") from None
        return decode_action(content)


class OfflineModel:
    """Deterministic simulator for process tests, never evidence of live model decisions."""
    mode = "offline"
    model_name = "offline-simulator"

    def next_action(self, messages):
        observations = []
        for message in messages:
            if message.get("role") == "user" and message.get("content", "").startswith("TOOL_OBSERVATION\n"):
                observations.append(json.loads(message["content"].split("\n", 1)[1]))
        def action(name, args=None, reason="offline process simulation"):
            return {"action": name, "arguments": args or {}, "reason": reason}
        if not observations:
            return action("read_material")
        last = observations[-1]
        tool, result = last.get("tool"), last.get("result", {})
        if last.get("error"):
            if last.get("retryable") is False:
                return action("finish", reason="tool reported a nonretryable rejection")
            if tool in ("read_material", "check_rules", "query_slots", "save_draft"):
                return action(tool, last.get("arguments", {}), "retry a failed tool in the simulator")
            return action("read_material")
        if tool == "read_material":
            return action("check_rules")
        if tool == "check_rules":
            return action("finish" if result.get("hard_block") or result.get("missing_fields") else "query_slots")
        if tool == "query_slots":
            if not result.get("candidates"):
                return action("finish", reason="no available slot in the permitted search range")
            return action("save_draft", {"suggested_time": result["candidates"][0]["start"]})
        if tool == "save_draft":
            if result.get("status") == "conflict":
                return action("query_slots", reason="recheck availability after transactional conflict")
            if result.get("status") in ("failed", "unknown"):
                if result.get("retryable") is False:
                    return action("finish", reason="save failure is not retryable")
                return action("save_draft", last.get("arguments", {}), "reconcile the same idempotent save")
            return action("finish")
        return action("finish")
