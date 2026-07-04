from .config import Settings
from .models import ActionResult, Plan
from .services import ContentService, ResearchService, SEOService, WordPressClient


class Executor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.wordpress = WordPressClient(settings)
        self.research = ResearchService(settings)
        self.content = ContentService(settings)
        self.seo = SEOService(settings)

    def execute(self, plan: Plan) -> list[ActionResult]:
        results: list[ActionResult] = []
        result_map: dict[str, ActionResult] = {}

        for action in plan.actions:
            blocked = [dep for dep in action.requires if not result_map.get(dep) or not result_map[dep].ok]
            if blocked:
                result = ActionResult(action.id, action.action, False, error=f"Blocked by failed dependencies: {', '.join(blocked)}")
            else:
                try:
                    result = self._execute_action(action.id, action.action, action.params, results)
                except Exception as exc:
                    result = ActionResult(action.id, action.action, False, error=str(exc))
            results.append(result)
            result_map[action.id] = result
            if action.critical and not result.ok:
                break
        return results

    def _execute_action(self, action_id: str, action: str, params: dict, previous: list[ActionResult]) -> ActionResult:
        previous_payloads = [_result_to_dict(result) for result in previous]
        if action == "scrape_url":
            data = self.research.scrape_url(str(params["url"]))
            return ActionResult(action_id, action, True, data=data)
        if action == "research_topic":
            data = self.research.research_topic(str(params.get("topic", "")), str(params.get("source_material", "")), previous_payloads)
            return ActionResult(action_id, action, True, data=data)
        if action == "generate_content":
            data = self.content.generate(
                title=str(params.get("title", "Untitled")),
                prompt=str(params.get("prompt", "")),
                content_type=str(params.get("content_type", "post")),
                source_material=str(params.get("source_material", "")),
                context=previous_payloads,
            )
            return ActionResult(action_id, action, True, data=data)
        if action == "seo_optimize":
            content = _latest_content(previous)
            data = self.seo.optimize(
                str(params.get("title", "Untitled")),
                str(params.get("focus_keyword", "")),
                content,
                str(params.get("meta_title", "")),
                str(params.get("meta_description", "")),
            )
            return ActionResult(action_id, action, True, data=data)
        if action == "content_transform":
            data = self.content.transform(str(params.get("operation", "rewrite")), str(params.get("prompt", "")), _latest_content(previous))
            return ActionResult(action_id, action, True, data=data)
        if action == "generate_schema":
            data = self.seo.schema(str(params.get("schema_type", "Article")), str(params.get("title", "Untitled")), _latest_content(previous))
            return ActionResult(action_id, action, True, data={"schema": data})
        if action == "internal_link":
            data = self.seo.internal_links(_latest_content(previous))
            return ActionResult(action_id, action, True, data=data)
        if action in {"create_post", "create_page"}:
            kind = "post" if action == "create_post" else "page"
            data = self.wordpress.create_content(
                kind=kind,
                title=str(params.get("title", "Untitled")),
                content=_latest_content(previous),
                status=str(params.get("status", "draft")),
                seo=_latest_seo(previous),
                taxonomy=_latest_taxonomy(previous),
            )
            return ActionResult(action_id, action, True, data=data, dry_run=self.settings.dry_run)
        if action in {"update_post", "update_page"}:
            kind = "post" if action == "update_post" else "page"
            payload = dict(params)
            content = _latest_content(previous)
            if content:
                payload["content"] = content
            seo = _latest_seo(previous)
            if seo:
                payload["seo"] = seo
            data = self.wordpress.update_content(kind, str(params.get("id", "")), payload)
            return ActionResult(action_id, action, True, data=data, dry_run=self.settings.dry_run)
        if action in {"delete_post", "delete_page"}:
            kind = "post" if action == "delete_post" else "page"
            data = self.wordpress.delete_content(kind, str(params.get("id", "")))
            return ActionResult(action_id, action, True, data=data, dry_run=self.settings.dry_run)
        if action == "create_category":
            data = self.wordpress.create_term("category", str(params.get("name", "Uncategorized")))
            return ActionResult(action_id, action, True, data=data, dry_run=self.settings.dry_run)
        if action == "create_tag":
            data = self.wordpress.create_term("tag", str(params.get("name", "General")))
            return ActionResult(action_id, action, True, data=data, dry_run=self.settings.dry_run)
        if action == "assign_taxonomy":
            return ActionResult(action_id, action, True, data={"message": "Taxonomy assignment captured for publish payload or follow-up update."}, dry_run=self.settings.dry_run)
        if action == "upload_media":
            return ActionResult(action_id, action, True, data={"message": "Media upload is reserved for configured WordPress binary uploads."}, dry_run=True)
        if action == "verify":
            return ActionResult(action_id, action, True, data={"message": "Execution reached verification step."})
        raise ValueError(f"Unsupported action: {action}")


def _latest_content(results: list[ActionResult]) -> str:
    for result in reversed(results):
        if result.ok and result.data.get("html"):
            return str(result.data["html"])
    return ""


def _latest_seo(results: list[ActionResult]) -> dict:
    for result in reversed(results):
        if result.ok and result.action == "seo_optimize":
            return result.data
    return {}


def _latest_taxonomy(results: list[ActionResult]) -> dict[str, list[int]]:
    taxonomy: dict[str, list] = {"categories": [], "tags": []}
    for result in results:
        if not result.ok:
            continue
        raw_id = result.data.get("id")
        if isinstance(raw_id, int):
            if result.action == "create_category":
                taxonomy["categories"].append(raw_id)
            if result.action == "create_tag":
                taxonomy["tags"].append(raw_id)
        elif isinstance(raw_id, str) and raw_id.startswith("dry-"):
            if result.action == "create_category":
                taxonomy["categories"].append(result.data.get("name", raw_id))
            if result.action == "create_tag":
                taxonomy["tags"].append(result.data.get("name", raw_id))
    return {key: value for key, value in taxonomy.items() if value}


def _result_to_dict(result: ActionResult) -> dict:
    return {
        "id": result.id,
        "action": result.action,
        "ok": result.ok,
        "data": result.data,
        "error": result.error,
        "dry_run": result.dry_run,
    }


def results_to_dict(results: list[ActionResult]) -> list[dict]:
    return [_result_to_dict(result) for result in results]
