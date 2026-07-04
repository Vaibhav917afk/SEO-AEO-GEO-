from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from .config import Settings
from .executor import Executor
from .planner import Planner
from .validator import Validator
from .verifier import Verifier


@dataclass
class QualityCase:
    name: str
    prompt: str
    source_material: str
    expected_actions: set[str]
    forbidden_title_terms: tuple[str, ...] = ("include", "explain", "benefits", "process", "common mistakes")
    min_words: int = 500


CASES = [
    QualityCase(
        "Blog + SEO",
        "Write a blog post about AI search optimization, optimize SEO, and save as draft",
        "Audience: founders and content teams. Include practical steps.",
        {"generate_content", "seo_optimize", "create_post", "verify"},
    ),
    QualityCase(
        "Landing Page",
        "Create a landing page for an AI WordPress automation service with FAQs and schema",
        "Benefits: faster publishing, consistent SEO, safe draft workflow.",
        {"generate_content", "generate_schema", "create_page", "verify"},
    ),
    QualityCase(
        "Rewrite",
        "Rewrite and humanize this article, optimize SEO, and create a draft post",
        "AI tools can help teams create content faster but require editorial review.",
        {"generate_content", "content_transform", "seo_optimize", "create_post"},
    ),
    QualityCase(
        "Taxonomy",
        "Create a blog post about answer engine optimization with category SEO and tags AI, AEO",
        "Explain AEO in clear language.",
        {"generate_content", "create_category", "create_tag", "create_post"},
    ),
    QualityCase(
        "Research URL",
        "Scrape https://example.com, summarize it, write a blog draft, and verify everything",
        "",
        {"scrape_url", "research_topic", "generate_content", "create_post", "verify"},
    ),
    QualityCase(
        "Advanced Blog",
        "Create a detailed draft blog post about AI search optimization for B2B businesses. Include practical steps, common mistakes, FAQ section, SEO optimization, category SEO, and tags AI, AEO, GEO. Do not publish.",
        "",
        {"generate_content", "seo_optimize", "create_category", "create_tag", "create_post", "verify"},
        min_words=900,
    ),
    QualityCase(
        "Professional Landing",
        "Create a professional landing page for an AI SEO automation service. Include hero copy, benefits, process, use cases, FAQ schema, internal link suggestions, SEO optimization, and save as draft.",
        "",
        {"generate_content", "seo_optimize", "generate_schema", "internal_link", "create_page", "verify"},
        min_words=450,
    ),
    QualityCase(
        "Presentation Landing",
        """Create a deep, professional landing page for an AI SEO automation service called "AI SEO Automation" for Business Insights.
Save as draft only. Do not publish.
Include hero section, problem section, solution section, detailed benefits, how it works, use cases, why Business Insights, quality and safety, FAQ schema, internal link suggestions, SEO optimization, category SEO, and tags AI SEO, SEO Automation, WordPress SEO, Content Automation, AEO, GEO.
Focus keyword: AI SEO automation
Meta title: AI SEO Automation Service for Business Websites
Meta description: Create SEO-ready WordPress content faster with AI SEO automation for blogs, landing pages, service pages, metadata, and publishing workflows.
Make the final page at least 1,200 words.""",
        "",
        {"generate_content", "seo_optimize", "generate_schema", "internal_link", "create_category", "create_tag", "create_page", "verify"},
        min_words=1200,
    ),
    QualityCase(
        "Comparison Article",
        "Create a detailed comparison article about SEO vs AEO vs GEO for business websites. Explain differences, use cases, benefits, implementation steps, FAQs, SEO metadata, category SEO, and tags SEO, AEO, GEO. Save as draft.",
        "",
        {"generate_content", "seo_optimize", "create_category", "create_tag", "create_post", "verify"},
        min_words=900,
    ),
    QualityCase(
        "Pricing Page",
        "Create a pricing page for an AI SEO automation platform with three plans: Starter, Growth, and Enterprise. Include feature comparison, best-fit guidance, FAQ schema, SEO metadata, and a clear CTA. Save as draft.",
        "",
        {"generate_content", "generate_schema", "create_page", "verify"},
        min_words=250,
    ),
    QualityCase(
        "Update Existing",
        "Update post 123 with a stronger introduction and SEO refresh about AI content operations.",
        "",
        {"generate_content", "seo_optimize", "update_post", "verify"},
        min_words=500,
    ),
    QualityCase(
        "Delete Existing",
        "Delete post 123 and verify the action.",
        "",
        {"delete_post", "verify"},
        min_words=0,
    ),
    # --- New comprehensive cases ---
    QualityCase(
        "Minimal Prompt",
        "Write about SEO",
        "",
        {"generate_content", "seo_optimize", "create_post", "verify"},
        min_words=500,
    ),
    QualityCase(
        "Case Study Page",
        "Create a case study page about how our AI tool improved content velocity for an e-commerce company. Include challenge, solution, results, and FAQ schema. Save as draft.",
        "",
        {"generate_content", "generate_schema", "create_page", "verify"},
        min_words=250,
    ),
    QualityCase(
        "Service Page",
        "Create a service page for our WordPress content automation service with SEO optimization and internal links. Save as draft.",
        "",
        {"generate_content", "seo_optimize", "internal_link", "create_page", "verify"},
        min_words=250,
    ),
    QualityCase(
        "Top 5 Listicle",
        "Write a blog post about the top 5 SEO mistakes B2B companies make. Optimize SEO, add category Digital Marketing and tags SEO, B2B. Save as draft.",
        "",
        {"generate_content", "seo_optimize", "create_category", "create_tag", "create_post", "verify"},
        min_words=800,
    ),
]


class QualityRunner:
    def __init__(self, settings: Settings):
        self.settings = _force_safe_settings(settings)
        self.planner = Planner(self.settings)
        self.validator = Validator()
        self.executor = Executor(self.settings)
        self.verifier = Verifier()

    def run(self) -> dict[str, Any]:
        case_reports = [self._run_case(case) for case in CASES]
        score = self._score(case_reports)
        return {
            "score": score,
            "target": 9.5,
            "passed": score >= 9.5,
            "total_cases": len(case_reports),
            "passed_cases": sum(1 for r in case_reports if r["ok"]),
            "failed_cases": sum(1 for r in case_reports if not r["ok"]),
            "cases": case_reports,
            "recommendation": "Ready for controlled live deployment." if score >= 9.5 else "Improve failed cases before live deployment.",
        }

    def _run_case(self, case: QualityCase) -> dict[str, Any]:
        plan = self.planner.create_plan(case.prompt, case.source_material)
        validation_error = ""
        try:
            self.validator.validate(plan)
        except Exception as exc:
            validation_error = str(exc)
        results = [] if validation_error else self.executor.execute(plan)
        verification = {"ok": False, "summary": validation_error} if validation_error else self.verifier.verify(plan, results)
        actions = {action.action for action in plan.actions}
        missing = sorted(case.expected_actions - actions)
        generated = next((result for result in results if result.action == "generate_content" and result.ok), None)
        generated_title = generated.data.get("title", "") if generated else ""
        word_count = int(generated.data.get("word_count_estimate", 0)) if generated else 0
        title_terms_found = [term for term in case.forbidden_title_terms if term in generated_title.lower()]
        content_too_thin = bool(generated and word_count < case.min_words)

        # Check for duplicate paragraphs in content
        has_duplicates = False
        if generated:
            html_content = generated.data.get("html", "")
            paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html_content, flags=re.I | re.S)
            clean_paras = [re.sub(r"\s+", " ", p).strip().lower() for p in paragraphs if len(p.strip()) > 50]
            for i in range(1, len(clean_paras)):
                if clean_paras[i] == clean_paras[i - 1]:
                    has_duplicates = True
                    break

        return {
            "name": case.name,
            "ok": bool(verification.get("ok")) and not missing and not title_terms_found and not content_too_thin and not has_duplicates,
            "actions": sorted(actions),
            "missing_expected_actions": missing,
            "generated_title": generated_title,
            "word_count": word_count,
            "title_terms_found": title_terms_found,
            "content_too_thin": content_too_thin,
            "has_duplicate_paragraphs": has_duplicates,
            "verification": verification,
            "critical_failures": [
                {"action": result.action, "error": result.error}
                for result in results
                if not result.ok
            ],
        }

    def _score(self, reports: list[dict[str, Any]]) -> float:
        total = 10.0
        penalty_per_issue = 10.0 / max(1, len(reports))
        for report in reports:
            case_penalty = 0.0
            if report["missing_expected_actions"]:
                case_penalty += 0.3 * len(report["missing_expected_actions"])
            if report["critical_failures"]:
                case_penalty += 0.5 * len(report["critical_failures"])
            if not report["verification"].get("ok"):
                case_penalty += 0.3
            if report["title_terms_found"]:
                case_penalty += 0.3 * len(report["title_terms_found"])
            if report["content_too_thin"]:
                case_penalty += 0.5
            if report.get("has_duplicate_paragraphs"):
                case_penalty += 0.4
            # Cap per-case penalty
            total -= min(case_penalty, penalty_per_issue)
        safety_bonus = 0.15 if self.settings.dry_run and not self.settings.auto_publish else 0
        return round(max(0.0, min(10.0, total + safety_bonus)), 2)


def _force_safe_settings(settings: Settings) -> Settings:
    return Settings(
        host=settings.host,
        port=settings.port,
        dry_run=True,
        database=Path(":memory:"),
        api_token="",
        auto_publish=False,
        allowed_wordpress_hosts=settings.allowed_wordpress_hosts,
        max_request_bytes=settings.max_request_bytes,
        wordpress_base_url=settings.wordpress_base_url,
        wordpress_username=settings.wordpress_username,
        wordpress_app_password=settings.wordpress_app_password,
        wordpress_seo_plugin=settings.wordpress_seo_plugin,
        anthropic_api_key="",
        anthropic_model=settings.anthropic_model,
        anthropic_fallback_models=settings.anthropic_fallback_models,
    )
