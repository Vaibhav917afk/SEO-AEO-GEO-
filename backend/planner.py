import json
import re
from typing import Any

from .anthropic_client import AnthropicClient
from .config import Settings
from .models import Action, Plan


PLANNER_SYSTEM_PROMPT = """You are the planner for an autonomous WordPress content agent.
Your job is to create an optimized execution plan from a user's prompt.

Return ONLY valid JSON. Do not execute actions.

JSON Schema:
{
  "goal": "concise description of what the user wants",
  "summary": "one sentence describing the execution plan",
  "needs_clarification": false,
  "clarification_question": "",
  "actions": [
    {
      "id": "step_1",
      "action": "action_name",
      "params": {"key": "value"},
      "requires": [],
      "critical": true
    }
  ]
}

AVAILABLE ACTIONS (use only these):
- scrape_url: Fetch and extract text from a URL. Params: {url}. Use when URLs are in the prompt.
- research_topic: Summarize research material. Params: {topic, source_material}. Use when URLs were scraped or research is needed.
- generate_content: Create WordPress HTML content. Params: {intent, content_type, title, prompt, source_material}. ALWAYS required for content creation/update.
  - intent: "generate" | "update" | "transform"
  - content_type: "post" | "page"
- content_transform: Rewrite/summarize/humanize/translate content. Params: {operation, prompt}. Use for rewrite/summarize/humanize/translate/proofread requests.
- seo_optimize: Generate SEO metadata. Params: {title, focus_keyword, meta_title, meta_description}. Include for ANY content that will be published.
- generate_schema: Create structured data. Params: {schema_type, title}. Include when FAQs exist, or schema/structured data is mentioned.
  - schema_type: "FAQPage" | "Article" | "HowTo"
- internal_link: Suggest internal links. Params: {strategy}. Include when internal links are mentioned.
- create_post: Create a WordPress blog post. Params: {title, status, content_from}. Use for blog/article content.
- create_page: Create a WordPress page. Params: {title, status, content_from}. Use for landing/pricing/service/homepage content.
- update_post / update_page: Update existing content. Params: {id, title, status}. Use when a post/page ID is provided with update intent.
- delete_post / delete_page: Delete content. Params: {id}. Use only when explicit deletion is requested.
- create_category: Create a taxonomy category. Params: {name}. Use when categories are specified.
- create_tag: Create a taxonomy tag. Params: {name}. Use when tags are specified.
- assign_taxonomy: Assign categories/tags. Params: {target_from}. Use after creating categories/tags.
- verify: Run verification checks. Params: {}. ALWAYS include as the last step.

ACTION SELECTION RULES:
1. ALWAYS include generate_content for any content creation request.
2. ALWAYS include seo_optimize when content will be published (blog, page, landing page).
3. ALWAYS include verify as the last step.
4. Include generate_schema when the prompt mentions FAQ, schema, structured data, or when the content type naturally includes FAQs.
5. Include internal_link when explicitly requested or when creating pillar/hub content.
6. Include scrape_url + research_topic when URLs are provided in the prompt.
7. For content_type: use "page" for landing pages, pricing pages, service pages, homepages, FAQ pages. Use "post" for blogs, articles, guides, listicles.
8. For transform requests (rewrite/summarize/humanize), FIRST generate_content, THEN content_transform.
9. Set status to "draft" unless the user explicitly says "publish". When in doubt, use "draft".
10. Ask clarification ONLY when execution is truly impossible (e.g., delete without an ID).
"""


class Planner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.anthropic = AnthropicClient(settings)

    def create_plan(self, prompt: str, source_material: str = "") -> Plan:
        if self.anthropic.ready:
            try:
                return self._anthropic_plan(prompt, source_material)
            except Exception:
                return self._heuristic_plan(prompt, source_material)
        return self._heuristic_plan(prompt, source_material)

    def _anthropic_plan(self, prompt: str, source_material: str) -> Plan:
        parsed = self.anthropic.complete_json(
            PLANNER_SYSTEM_PROMPT,
            {"prompt": prompt, "source_material": source_material},
        )
        return _normalize_plan(plan_from_dict(parsed), self.settings, prompt)

    def _heuristic_plan(self, prompt: str, source_material: str) -> Plan:
        lowered = prompt.lower()
        actions: list[Action] = []
        step = 1

        direct_action_plan = _direct_mutation_plan(prompt, self.settings)
        if direct_action_plan:
            return direct_action_plan

        urls = re.findall(r"https?://[^\s)>\"]+", prompt + "\n" + source_material)
        for url in urls:
            actions.append(
                Action(
                    id=f"step_{step}",
                    action="scrape_url",
                    params={"url": url.rstrip(".,")},
                    critical=False,
                )
            )
            step += 1

        if urls or source_material.strip() or _needs_research(lowered):
            actions.append(
                Action(
                    id=f"step_{step}",
                    action="research_topic",
                    params={"topic": _compact(prompt), "source_material": source_material},
                )
            )
            step += 1

        content_type = "post"
        create_action = "create_post"
        if any(word in lowered for word in ["page", "landing", "pricing", "homepage", "faq page"]):
            content_type = "page"
            create_action = "create_page"

        transform_requested = any(word in lowered for word in ["rewrite", "summarize", "translate", "humanize", "proofread"])
        if transform_requested:
            intent = "transform"
        elif any(word in lowered for word in ["create", "write", "generate", "publish", "blog", "article", "page"]):
            intent = "generate"
        else:
            intent = "manage"

        title = _title_from_prompt(prompt, content_type)
        actions.append(
            Action(
                id=f"step_{step}",
                action="generate_content",
                params={
                    "intent": intent,
                    "content_type": content_type,
                    "title": title,
                    "prompt": prompt,
                    "source_material": source_material,
                },
                critical=True,
            )
        )
        content_step = f"step_{step}"
        step += 1

        if transform_requested:
            actions.append(
                Action(
                    id=f"step_{step}",
                    action="content_transform",
                    params={"operation": _transform_operation(lowered), "prompt": prompt},
                    requires=[content_step],
                )
            )
            content_step = f"step_{step}"
            step += 1

        if intent == "generate" or "seo" in lowered or "optimi" in lowered or "publish" in lowered:
            actions.append(
                Action(
                    id=f"step_{step}",
                    action="seo_optimize",
                    params={
                        "title": title,
                        "focus_keyword": _requested_value(prompt, "focus keyword") or _focus_keyword(prompt),
                        "meta_title": _requested_value(prompt, "meta title"),
                        "meta_description": _requested_value(prompt, "meta description"),
                    },
                    requires=[content_step],
                )
            )
            step += 1

        # Auto-include schema for FAQ content or when explicitly requested
        if "schema" in lowered or "faq" in lowered or "structured data" in lowered:
            schema_type = "FAQPage" if "faq" in lowered else "Article"
            if "how to" in lowered or "how-to" in lowered or "howto" in lowered:
                schema_type = "HowTo"
            actions.append(
                Action(
                    id=f"step_{step}",
                    action="generate_schema",
                    params={"schema_type": schema_type, "title": title},
                    requires=[content_step],
                )
            )
            step += 1

        if "internal link" in lowered or "internal links" in lowered:
            actions.append(
                Action(
                    id=f"step_{step}",
                    action="internal_link",
                    params={"strategy": "suggest_relevant_links"},
                    requires=[content_step],
                )
            )
            step += 1

        tax_requires: list[str] = []
        for category in _extract_named_values(prompt, ["category", "categories"]) or _extract_after_keyword(prompt, "category"):
            actions.append(Action(id=f"step_{step}", action="create_category", params={"name": category}))
            tax_requires.append(f"step_{step}")
            step += 1
        for tag in _extract_named_values(prompt, ["tag", "tags"]) or _extract_after_keyword(prompt, "tags") or _extract_after_keyword(prompt, "tag"):
            actions.append(Action(id=f"step_{step}", action="create_tag", params={"name": tag}))
            tax_requires.append(f"step_{step}")
            step += 1

        should_publish = _should_publish(lowered, self.settings)
        status = "publish" if should_publish else "draft"
        actions.append(
            Action(
                id=f"step_{step}",
                action=create_action,
                params={
                    "title": title,
                    "status": status,
                    "content_from": content_step,
                },
                requires=[content_step],
                critical=True,
            )
        )
        publish_step = f"step_{step}"
        step += 1

        if tax_requires:
            actions.append(
                Action(
                    id=f"step_{step}",
                    action="assign_taxonomy",
                    params={"target_from": publish_step},
                    requires=[publish_step, *tax_requires],
                )
            )
            step += 1

        actions.append(Action(id=f"step_{step}", action="verify", params={}))

        return Plan(
            goal=_compact(prompt),
            summary=f"Plan {len(actions)} steps to produce and {'publish' if should_publish else 'draft'} a WordPress {content_type}.",
            actions=actions,
        )


def plan_from_dict(payload: dict[str, Any]) -> Plan:
    return Plan(
        goal=str(payload.get("goal", "")),
        summary=str(payload.get("summary", "")),
        needs_clarification=bool(payload.get("needs_clarification", False)),
        clarification_question=str(payload.get("clarification_question", "")),
        actions=[
            Action(
                id=str(item.get("id", f"step_{index + 1}")),
                action=str(item.get("action", "")),
                params=dict(item.get("params", {})),
                requires=list(item.get("requires", [])),
                critical=bool(item.get("critical", False)),
            )
            for index, item in enumerate(payload.get("actions", []))
        ],
    )


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    return {
        "goal": plan.goal,
        "summary": plan.summary,
        "needs_clarification": plan.needs_clarification,
        "clarification_question": plan.clarification_question,
        "actions": [
            {
                "id": action.id,
                "action": action.action,
                "params": action.params,
                "requires": action.requires,
                "critical": action.critical,
            }
            for action in plan.actions
        ],
    }


def _normalize_plan(plan: Plan, settings: Settings, prompt: str) -> Plan:
    no_publish = _explicit_no_publish(prompt.lower())
    normalized: list[Action] = []
    seen: set[str] = set()
    for index, action in enumerate(plan.actions, start=1):
        action.id = action.id or f"step_{index}"
        if action.id in seen:
            action.id = f"step_{index}"
        seen.add(action.id)
        if action.action in {"create_post", "create_page"}:
            requested = str(action.params.get("status", "draft")).lower()
            action.params["status"] = "publish" if requested == "publish" and settings.auto_publish and not no_publish else "draft"
        normalized.append(action)
    plan.actions = normalized
    return plan


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:220]


def _needs_research(lowered: str) -> bool:
    research_terms = [
        "research",
        "scrape",
        "competitor",
        "compare",
        "latest",
        "current",
        "recent",
        "today",
        "now",
        "happening",
        "trends",
        "innovations",
        "news",
        "market",
    ]
    return any(term in lowered for term in research_terms)


def _title_from_prompt(prompt: str, content_type: str) -> str:
    lowered = prompt.lower()
    if any(word in lowered for word in ["rewrite", "humanize", "proofread"]) and re.search(r"\bthis\b", lowered):
        return "Humanized Article Draft"
    if "scrape" in lowered and "blog" in lowered:
        return "Research-Backed Blog Draft"
    explicit = _explicit_title(prompt)
    if explicit:
        return explicit
    cleaned = re.sub(r"https?://\S+", "", prompt)
    cleaned = re.sub(r"\b(do not publish|save as draft|short draft|draft)\b", "", cleaned, flags=re.I)
    cleaned = re.split(
        r"\b(?:include|including|explain|position|add category|category|tags?|optimize seo|internal links?|schema|save|do not|cta|faq)\b",
        cleaned,
        maxsplit=1,
        flags=re.I,
    )[0]
    topic_match = re.search(r"\b(?:about|for)\s+(.+)", cleaned, flags=re.I)
    if topic_match:
        cleaned = topic_match.group(1)
    cleaned = re.split(r"\b(?:with|that|who|where|using|to)\b", cleaned, maxsplit=1, flags=re.I)[0]
    cleaned = re.sub(
        r"\b(create|write|generate|publish|post|blog|article|page|landing|homepage|hero|section|supporting|content|professional|detailed|draft|high-converting|service|case|study|pricing|pillar|optimize|and|then|also|please|a|an|the)\b",
        "",
        cleaned,
        flags=re.I,
    )
    words = [word.strip(" .,:;!?") for word in cleaned.split() if word.strip(" .,:;!?")]
    if not words:
        return f"New WordPress {content_type.title()}"
    title = _fix_acronyms(" ".join(words[:11]).title())
    return title[:90]


def _explicit_title(prompt: str) -> str:
    patterns = [
        r'\bcalled\s+[\"\'""'']?([^\"\'""''.;\n]+)[\"\'""'']?',
        r'\bnamed\s+[\"\'""'']?([^\"\'""''.;\n]+)[\"\'""'']?',
        r"\bpage\s+title\s*:\s*([^\n.]+)",
        r"(?m)^\s*title\s*:\s*([^\n.]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.I)
        if match:
            value = match.group(1).strip(" \"'""'':-")
            if value:
                return _fix_acronyms(value[:90])
    return ""


def _requested_value(prompt: str, label: str) -> str:
    match = re.search(rf"\b{re.escape(label)}\s*:\s*([^\n]+)", prompt, flags=re.I)
    if not match:
        return ""
    value = match.group(1).strip()
    return value[:300]


def _direct_mutation_plan(prompt: str, settings: Settings) -> Plan | None:
    lowered = prompt.lower()
    match = re.search(r"\b(?:post|page|id)\s*#?\s*(\d+)\b", lowered)
    content_id = match.group(1) if match else ""
    is_page = "page" in lowered
    if any(word in lowered for word in ["delete", "remove", "trash"]):
        if not content_id:
            return Plan(
                goal=_compact(prompt),
                summary="Clarification required before deleting WordPress content.",
                actions=[],
                needs_clarification=True,
                clarification_question="Which WordPress post or page ID should be deleted?",
            )
        return Plan(
            goal=_compact(prompt),
            summary="Delete the requested WordPress content.",
            actions=[
                Action(id="step_1", action="delete_page" if is_page else "delete_post", params={"id": content_id}, critical=True),
                Action(id="step_2", action="verify", params={}),
            ],
        )
    if any(word in lowered for word in ["update", "modify", "edit", "refresh"]):
        if not content_id:
            return None
        title = _title_from_prompt(prompt, "page" if is_page else "post")
        return Plan(
            goal=_compact(prompt),
            summary="Generate revised content and update the requested WordPress item.",
            actions=[
                Action(
                    id="step_1",
                    action="generate_content",
                    params={"intent": "update", "content_type": "page" if is_page else "post", "title": title, "prompt": prompt, "source_material": ""},
                    critical=True,
                ),
                Action(id="step_2", action="seo_optimize", params={"title": title, "focus_keyword": _focus_keyword(prompt)}, requires=["step_1"]),
                Action(
                    id="step_3",
                    action="update_page" if is_page else "update_post",
                    params={"id": content_id, "title": title, "status": "draft"},
                    requires=["step_1"],
                    critical=True,
                ),
                Action(id="step_4", action="verify", params={}),
            ],
        )
    return None


def _focus_keyword(prompt: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", prompt.lower())
    stop = {
        "create",
        "write",
        "generate",
        "publish",
        "about",
        "with",
        "from",
        "this",
        "that",
        "wordpress",
        "short",
        "draft",
        "blog",
        "post",
        "page",
        "article",
        "category",
        "tags",
        "optimize",
        "rewrite",
        "humanize",
        "scrape",
        "summarize",
        "verify",
        "everything",
        "include",
        "detailed",
        "professional",
        "section",
        "save",
        "only",
        "deep",
    }
    picked = [word for word in words if word not in stop]
    return " ".join(picked[:3]) or "wordpress content"


def _transform_operation(lowered: str) -> str:
    for operation in ["rewrite", "summarize", "translate", "humanize", "proofread"]:
        if operation in lowered:
            return operation
    return "rewrite"


def _should_publish(lowered: str, settings: Settings) -> bool:
    return settings.auto_publish and "publish" in lowered and not _explicit_no_publish(lowered)


def _explicit_no_publish(lowered: str) -> bool:
    patterns = [
        r"\bdo\s+not\s+publish\b",
        r"\bdon't\s+publish\b",
        r"\bnot\s+publish\b",
        r"\bsave\s+as\s+draft\b",
        r"\bdraft\s+only\b",
        r"\bkeep\s+as\s+draft\b",
    ]
    return any(re.search(pattern, lowered, flags=re.I) for pattern in patterns)


def _extract_named_values(prompt: str, labels: list[str]) -> list[str]:
    values: list[str] = []
    label_pattern = "|".join(re.escape(label) for label in labels)
    for match in re.finditer(rf"\b(?:{label_pattern})\b\s*(?:as|to|:)?\s*([A-Za-z0-9 ,&-]{{2,80}})", prompt, flags=re.I):
        raw = re.split(r"\b(?:and|with|then|publish|draft|optimize)\b", match.group(1), maxsplit=1, flags=re.I)[0]
        for part in re.split(r"[,/]", raw):
            cleaned = part.strip(" .;:")
            if cleaned and len(cleaned) <= 40:
                values.append(_fix_acronyms(cleaned.title()))
    return values[:8]


def _extract_after_keyword(prompt: str, keyword: str) -> list[str]:
    pattern = rf"\b{re.escape(keyword)}\b\s+([A-Za-z0-9][A-Za-z0-9 &,-]{{1,80}})"
    match = re.search(pattern, prompt, flags=re.I)
    if not match:
        return []
    raw = re.split(r"\b(?:and|with|then|publish|draft|optimize|tags?|categor(?:y|ies))\b", match.group(1), maxsplit=1, flags=re.I)[0]
    return [_fix_acronyms(part.strip(" .;:").title()) for part in re.split(r"[,/]", raw) if part.strip(" .;:")][:4]


def _fix_acronyms(value: str) -> str:
    replacements = {
        "Ai": "AI",
        "Aeo": "AEO",
        "Seo": "SEO",
        "Geo": "GEO",
        "Api": "API",
        "Faq": "FAQ",
        "Saas": "SaaS",
        "Cms": "CMS",
        "Url": "URL",
        "Wordpress": "WordPress",
    }
    for wrong, right in replacements.items():
        value = re.sub(rf"\b{wrong}\b", right, value)
    return value
