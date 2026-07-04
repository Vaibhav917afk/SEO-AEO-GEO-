import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from .config import Settings

# Retryable HTTP status codes (rate limit + server errors)
_RETRYABLE_CODES = {429, 500, 502, 503, 529}
_MAX_RETRIES = 3
_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


class AnthropicClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def ready(self) -> bool:
        return bool(self.settings.anthropic_api_key)

    def complete_json(self, system: str, payload: dict[str, Any] | str, max_tokens: int = 1800) -> dict[str, Any]:
        user_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=True)
        text = self.complete_text(system, user_text, max_tokens=max_tokens)
        extracted = _extract_json(text)
        parsed = _loads_json_lenient(extracted)
        if isinstance(parsed, list):
            return {"items": parsed}
        return parsed

    def complete_text(self, system: str, user_text: str, max_tokens: int = 1800) -> str:
        errors: list[str] = []
        candidates = [self.settings.anthropic_model]
        candidates.extend(model for model in self.settings.anthropic_fallback_models if model not in candidates)
        for model in candidates:
            try:
                return self._complete_text_with_model(model, system, user_text, max_tokens)
            except RuntimeError as exc:
                errors.append(str(exc))
                if "not_found_error" not in str(exc) and "model:" not in str(exc):
                    break
        raise RuntimeError("Anthropic API failed for all configured models: " + " | ".join(errors))

    def _complete_text_with_model(self, model: str, system: str, user_text: str, max_tokens: int) -> str:
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_text}],
        }
        data = json.dumps(body).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "x-api-key": self.settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        }

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=data,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=90) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))
                    text = "".join(part.get("text", "") for part in response_payload.get("content", []))
                    if not text.strip():
                        raise RuntimeError(f"Anthropic returned empty response for model {model}")
                    # Check for truncation
                    stop_reason = response_payload.get("stop_reason", "")
                    if stop_reason == "max_tokens":
                        # Content was truncated — try to salvage if it looks like valid output
                        # but log the truncation for the caller
                        text = text.rstrip()
                        if text and not text.endswith(("}", ">", ".", "!", "?")):
                            # Try to close any open JSON or HTML
                            text = _attempt_close_truncated(text)
                    return text
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")[:1000]
                last_error = RuntimeError(f"Anthropic API failed with HTTP {exc.code}: {error_body}")
                if exc.code in _RETRYABLE_CODES and attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise last_error from exc
            except urllib.error.URLError as exc:
                last_error = RuntimeError(f"Anthropic API network error: {exc}")
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise last_error from exc

        raise last_error or RuntimeError("Anthropic API failed after retries")


def _attempt_close_truncated(text: str) -> str:
    """Try to close truncated JSON or HTML so downstream parsing doesn't break."""
    stripped = text.strip()
    # If it looks like JSON that got cut off, try closing braces
    if stripped.startswith("{"):
        open_braces = stripped.count("{") - stripped.count("}")
        if open_braces > 0:
            stripped += '"' + ("}" * open_braces)
    # If it looks like HTML that got cut off, just return as-is
    return stripped


def _extract_json(text: str) -> str:
    """Extract JSON from model response, handling markdown fences and mixed content."""
    stripped = text.strip()

    # Direct JSON object
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    # Direct JSON array
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped

    # JSON inside markdown code fences: ```json ... ``` or ``` ... ```
    fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```", stripped)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate.startswith(("{", "[")):
            return candidate

    # Find the outermost JSON object
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]

    # Find the outermost JSON array
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start >= 0 and end > start:
        return stripped[start : end + 1]

    raise ValueError("No JSON object found in model response.")


def _loads_json_lenient(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        repaired = _escape_control_chars_inside_strings(value)
        return json.loads(repaired)


def _escape_control_chars_inside_strings(value: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    for char in value:
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\":
            result.append(char)
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            result.append(char)
            continue
        if in_string and ord(char) < 32:
            if char == "\n":
                result.append("\\n")
            elif char == "\r":
                result.append("\\r")
            elif char == "\t":
                result.append("\\t")
            else:
                result.append(f"\\u{ord(char):04x}")
            continue
        result.append(char)
    return "".join(result)
