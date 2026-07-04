from .models import Plan


SUPPORTED_ACTIONS = {
    "scrape_url",
    "research_topic",
    "generate_content",
    "seo_optimize",
    "create_post",
    "update_post",
    "create_page",
    "update_page",
    "delete_post",
    "delete_page",
    "upload_media",
    "create_category",
    "create_tag",
    "assign_taxonomy",
    "generate_schema",
    "internal_link",
    "content_transform",
    "verify",
}


class PlanValidationError(ValueError):
    pass


class Validator:
    def validate(self, plan: Plan) -> None:
        if plan.needs_clarification:
            return
        if not plan.actions:
            raise PlanValidationError("Plan contains no actions.")

        seen: set[str] = set()
        for action in plan.actions:
            if not action.id:
                raise PlanValidationError("Every action requires an id.")
            if action.id in seen:
                raise PlanValidationError(f"Duplicate action id: {action.id}")
            seen.add(action.id)
            if action.action not in SUPPORTED_ACTIONS:
                raise PlanValidationError(f"Unsupported action: {action.action}")
            for dependency in action.requires:
                if dependency not in seen:
                    raise PlanValidationError(f"{action.id} depends on unknown or later step {dependency}.")
            if action.action == "scrape_url" and not str(action.params.get("url", "")).startswith(("http://", "https://")):
                raise PlanValidationError("scrape_url requires a valid http(s) URL.")
            if action.action in {"create_post", "create_page"} and not action.params.get("title"):
                raise PlanValidationError(f"{action.action} requires a title.")
            if action.action in {"delete_post", "delete_page", "update_post", "update_page"} and not action.params.get("id"):
                raise PlanValidationError(f"{action.action} requires an id.")
