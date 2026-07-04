import re
from typing import Any

from .models import ActionResult, Plan


class Verifier:
    def verify(self, plan: Plan, results: list[ActionResult]) -> dict[str, Any]:
        failures = [result for result in results if not result.ok]
        completed_actions = {result.action for result in results if result.ok}
        wordpress_action_completed = bool(
            {"create_post", "create_page", "update_post", "update_page", "delete_post", "delete_page"} & completed_actions
        )
        seo_applied = "seo_optimize" in completed_actions
        content_generated = "generate_content" in completed_actions
        destructive_only = bool({"delete_post", "delete_page"} & completed_actions)

        # Content quality checks
        content_quality = self._check_content_quality(results)
        seo_quality = self._check_seo_quality(results)

        ok = (
            not failures
            and wordpress_action_completed
            and (content_generated or destructive_only)
            and content_quality["passed"]
        )

        return {
            "ok": ok,
            "content_generated": content_generated,
            "seo_applied": seo_applied,
            "wordpress_action_completed": wordpress_action_completed,
            "content_quality": content_quality,
            "seo_quality": seo_quality,
            "failed_steps": [{"id": item.id, "action": item.action, "error": item.error} for item in failures],
            "summary": self._summary(plan, ok, failures, content_quality),
        }

    def _check_content_quality(self, results: list[ActionResult]) -> dict[str, Any]:
        """Validate the quality of generated content."""
        generated = next((r for r in results if r.action == "generate_content" and r.ok), None)
        if not generated:
            return {"passed": True, "checks": {}, "issues": []}

        html_content = str(generated.data.get("html", ""))
        word_count = int(generated.data.get("word_count_estimate", 0))
        title = str(generated.data.get("title", ""))
        plain = re.sub(r"<[^>]+>", " ", html_content)

        issues: list[str] = []
        checks: dict[str, Any] = {}

        # Check 1: Minimum word count
        checks["word_count"] = word_count
        if word_count < 200:
            issues.append(f"Content is very thin ({word_count} words). Aim for 500+ words minimum.")

        # Check 2: Heading structure
        h2_count = len(re.findall(r"<h2[^>]*>", html_content, flags=re.I))
        checks["h2_count"] = h2_count
        if h2_count < 2 and word_count > 200:
            issues.append(f"Only {h2_count} H2 headings. Well-structured content needs 3+ sections.")

        # Check 3: No duplicate consecutive paragraphs
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html_content, flags=re.I | re.S)
        clean_paras = [re.sub(r"\s+", " ", p).strip().lower() for p in paragraphs if len(p.strip()) > 50]
        duplicate_count = 0
        for i in range(1, len(clean_paras)):
            if clean_paras[i] == clean_paras[i - 1]:
                duplicate_count += 1
        checks["duplicate_paragraphs"] = duplicate_count
        if duplicate_count > 0:
            issues.append(f"{duplicate_count} consecutive duplicate paragraph(s) detected. Content should be unique throughout.")

        # Check 4: FAQ section presence (for content that should have it)
        has_faq = bool(re.search(r"<h[2-3][^>]*>\s*FAQ", html_content, flags=re.I))
        checks["has_faq"] = has_faq

        # Check 5: Title quality
        checks["title"] = title
        checks["title_length"] = len(title)
        bad_title_words = ["include", "explain", "create", "write", "generate"]
        title_has_instructions = any(word in title.lower() for word in bad_title_words)
        checks["title_clean"] = not title_has_instructions
        if title_has_instructions:
            issues.append(f'Title contains instruction words: "{title}". Titles should be topic-focused.')

        # Check 6: Content not just boilerplate
        unique_sentences = set()
        sentences = re.split(r"(?<=[.!?])\s+", plain)
        for s in sentences:
            clean = re.sub(r"\s+", " ", s).strip().lower()
            if len(clean) > 30:
                unique_sentences.add(clean)
        content_variety = len(unique_sentences) / max(1, len([s for s in sentences if len(s.strip()) > 30]))
        checks["content_variety"] = round(content_variety, 2)
        if content_variety < 0.5 and word_count > 300:
            issues.append("Content variety is low — many sentences are repeated. Each section should have unique content.")

        passed = duplicate_count == 0 and not title_has_instructions
        return {"passed": passed, "checks": checks, "issues": issues}

    def _check_seo_quality(self, results: list[ActionResult]) -> dict[str, Any]:
        """Validate SEO optimization completeness."""
        seo = next((r for r in results if r.action == "seo_optimize" and r.ok), None)
        if not seo:
            return {"applied": False, "checks": {}}

        data = seo.data
        checks = {}
        checks["has_meta_title"] = bool(data.get("meta_title"))
        checks["has_meta_description"] = bool(data.get("meta_description"))
        checks["has_focus_keyword"] = bool(data.get("focus_keyword"))
        checks["has_slug"] = bool(data.get("slug"))
        checks["seo_score"] = data.get("seo_score", 0)
        checks["recommendation_count"] = len(data.get("recommendations", []))

        return {"applied": True, "checks": checks}

    def _summary(self, plan: Plan, ok: bool, failures: list[ActionResult], content_quality: dict[str, Any]) -> str:
        if ok:
            return f"Completed: {plan.summary}"
        if failures:
            return f"Completed with issues. First failure: {failures[0].action} - {failures[0].error}"
        if not content_quality.get("passed"):
            issues = content_quality.get("issues", [])
            issue_text = issues[0] if issues else "Content quality check failed."
            return f"Content quality issue: {issue_text}"
        return "Execution did not complete the required content and WordPress actions."
