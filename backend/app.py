import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import load_settings
from .executor import Executor, results_to_dict
from .planner import Planner, plan_to_dict
from .quality import QualityRunner
from .storage import RunStore
from .validator import PlanValidationError, Validator
from .verifier import Verifier


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class AgentApplication:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.planner = Planner(self.settings)
        self.validator = Validator()
        self.executor = Executor(self.settings)
        self.verifier = Verifier()
        self.store = RunStore(self.settings.database)

    def run_prompt(self, prompt: str, source_material: str = "") -> dict[str, Any]:
        plan = self.planner.create_plan(prompt, source_material)
        plan_json = plan_to_dict(plan)
        if plan.needs_clarification:
            result = {
                "ok": False,
                "needs_clarification": True,
                "clarification_question": plan.clarification_question,
                "plan": plan_json,
                "results": [],
                "verification": {"ok": False, "summary": "Clarification required."},
                "final_response": plan.clarification_question,
            }
            result["run_id"] = self.store.create_run(prompt, plan_json, result, False)
            return result

        self.validator.validate(plan)
        results = self.executor.execute(plan)
        verification = self.verifier.verify(plan, results)
        final_response = _final_response(verification, results)
        result = {
            "ok": verification["ok"],
            "needs_clarification": False,
            "plan": plan_json,
            "results": results_to_dict(results),
            "verification": verification,
            "final_response": final_response,
            "output_summary": _output_summary(verification, results),
        }
        result["run_id"] = self.store.create_run(prompt, plan_json, result, verification["ok"])
        return result


APP = AgentApplication()


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        parsed = urlparse(path).path
        if parsed.startswith("/api/"):
            return str(FRONTEND / "index.html")
        if parsed == "/":
            parsed = "/index.html"
        return str(FRONTEND / parsed.lstrip("/"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path).path
        if parsed == "/api/health":
            self._json({
                "ok": True,
                "dry_run": APP.settings.dry_run,
                "wordpress_ready": APP.settings.wordpress_ready,
                "live_ready": APP.settings.live_ready,
                "auto_publish": APP.settings.auto_publish,
                "anthropic_ready": bool(APP.settings.anthropic_api_key),
                "live_research": APP.settings.enable_live_research,
                "web_search_provider": APP.settings.web_search_provider,
            })
            return
        if parsed == "/api/runs":
            self._json({"runs": APP.store.recent_runs()})
            return
        if parsed == "/api/quality":
            self._json(QualityRunner(APP.settings).run())
            return
        return super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/run":
            self._json({"error": "Not found"}, status=404)
            return
        try:
            if not self._authorized():
                self._json({"error": "Unauthorized"}, status=401)
                return
            payload = self._read_json()
            prompt = str(payload.get("prompt", "")).strip()
            source_material = str(payload.get("source_material", "")).strip()
            if not prompt:
                self._json({"error": "Prompt is required."}, status=400)
                return
            self._json(APP.run_prompt(prompt, source_material))
        except PlanValidationError as exc:
            self._json({"error": str(exc)}, status=422)
        except Exception as exc:
            self._json({"error": str(exc)}, status=500)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length > APP.settings.max_request_bytes:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def _authorized(self) -> bool:
        if not APP.settings.api_token:
            return True
        expected = f"Bearer {APP.settings.api_token}"
        return self.headers.get("authorization", "") == expected

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _final_response(verification: dict[str, Any], results: list) -> str:
    created = next((result for result in reversed(results) if result.action in {"create_post", "create_page"} and result.ok), None)
    if created:
        link = created.data.get("link", "")
        mode = "dry-run " if created.dry_run else ""
        return f"{mode}WordPress {created.data.get('kind', 'content')} prepared successfully. {link}".strip()
    return verification.get("summary", "Execution finished.")


def _output_summary(verification: dict[str, Any], results: list) -> dict[str, Any]:
    created = next((result for result in reversed(results) if result.action in {"create_post", "create_page"} and result.ok), None)
    generated = next((result for result in results if result.action == "generate_content" and result.ok), None)
    seo = next((result for result in results if result.action == "seo_optimize" and result.ok), None)
    research = next((result for result in reversed(results) if result.action == "research_topic" and result.ok), None)
    categories = [result.data.get("name") for result in results if result.action == "create_category" and result.ok and result.data.get("name")]
    tags = [result.data.get("name") for result in results if result.action == "create_tag" and result.ok and result.data.get("name")]
    html = generated.data.get("html", "") if generated else ""
    headings = []
    if html:
        import re

        headings = re.findall(r"<h[2-3][^>]*>(.*?)</h[2-3]>", html, flags=re.I)
        headings = [re.sub(r"<[^>]+>", "", heading).strip() for heading in headings][:12]
    return {
        "status": "passed" if verification.get("ok") else "review",
        "deliverable": created.data.get("kind", "content") if created else "content",
        "title": (generated.data.get("title") if generated else created.data.get("title") if created else ""),
        "word_count_estimate": generated.data.get("word_count_estimate") if generated else None,
        "generation_mode": generated.data.get("generation_mode") if generated else "",
        "ai_fallback_reason": generated.data.get("ai_fallback_reason") if generated else "",
        "public_url": created.data.get("link") if created else "",
        "edit_url": created.data.get("edit_link") if created else "",
        "wordpress_status": created.data.get("status") if created else "",
        "dry_run": bool(created.dry_run) if created else False,
        "services_completed": [result.action for result in results if result.ok],
        "content_outline": headings,
        "seo": seo.data if seo else {},
        "research": {
            "mode": research.data.get("source_mode", "") if research else "",
            "summary": research.data.get("summary", "") if research else "",
            "sources": research.data.get("sources", []) if research else [],
        },
        "categories": categories,
        "tags": tags,
        "verification": verification,
    }


def main() -> None:
    server = ThreadingHTTPServer((APP.settings.host, APP.settings.port), Handler)
    print(f"AI WordPress Agent running at http://{APP.settings.host}:{APP.settings.port}")
    print(f"Dry run: {APP.settings.dry_run}")
    server.serve_forever()


if __name__ == "__main__":
    main()
