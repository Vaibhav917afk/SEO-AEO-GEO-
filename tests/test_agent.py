import re
import unittest
from pathlib import Path

from backend.config import Settings
from backend.executor import Executor
from backend.planner import Planner
from backend.services import SEOService, ContentService, ResearchService
from backend.validator import Validator
from backend.verifier import Verifier


def test_settings() -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8787,
        dry_run=True,
        database=Path(":memory:"),
        api_token="",
        auto_publish=False,
        allowed_wordpress_hosts=(),
        max_request_bytes=250000,
        wordpress_base_url="",
        wordpress_username="",
        wordpress_app_password="",
        wordpress_seo_plugin="generic",
        anthropic_api_key="",
        anthropic_model="test",
        anthropic_fallback_models=(),
    )


class PlannerTests(unittest.TestCase):
    """Test the planner's ability to create correct action plans."""

    def test_planner_creates_publishable_page_plan(self):
        planner = Planner(test_settings())
        plan = planner.create_plan("Create a pricing page with three plans and optimize SEO")
        self.assertTrue(plan.actions)
        self.assertIn("generate_content", [action.action for action in plan.actions])
        self.assertIn("create_page", [action.action for action in plan.actions])

    def test_validator_accepts_planner_output(self):
        planner = Planner(test_settings())
        plan = planner.create_plan("Write a blog post about AI search and publish it")
        Validator().validate(plan)

    def test_publish_is_downgraded_without_auto_publish(self):
        settings = test_settings()
        plan = Planner(settings).create_plan("Write a blog post about AI search and publish it")
        create = next(action for action in plan.actions if action.action == "create_post")
        self.assertEqual(create.params["status"], "draft")

    def test_do_not_publish_overrides_auto_publish(self):
        settings = Settings(
            **{**test_settings().__dict__, "auto_publish": True}
        )
        plan = Planner(settings).create_plan("Create a blog post about AI search. Do not publish.")
        create = next(action for action in plan.actions if action.action == "create_post")
        self.assertEqual(create.params["status"], "draft")

    def test_advanced_prompt_titles_do_not_include_instructions(self):
        planner = Planner(test_settings())
        cases = {
            "Create a detailed draft blog post about AI search optimization for B2B businesses. Include practical steps, common mistakes, FAQ section, SEO optimization, category SEO, and tags AI, AEO, GEO. Do not publish.": "AI Search Optimization For B2B Businesses",
            "Create a professional landing page for an AI SEO automation service. Include hero copy, benefits, process, use cases, FAQ schema, internal link suggestions, SEO optimization, and save as draft.": "AI SEO Automation",
            "Create a pricing page for an AI SEO automation platform with three plans: Starter, Growth, and Enterprise. Include feature comparison, best-fit guidance, FAQ schema, SEO metadata, and a clear CTA. Save as draft.": "AI SEO Automation Platform",
        }
        for prompt, expected in cases.items():
            with self.subTest(prompt=prompt):
                plan = planner.create_plan(prompt)
                generated = next(action for action in plan.actions if action.action == "generate_content")
                title = generated.params["title"]
                self.assertIn(expected, title)
                self.assertNotIn("Include", title)
                self.assertNotIn("Benefits", title)
                self.assertNotIn("Common Mistakes", title)

    def test_update_and_delete_actions_plan_directly(self):
        planner = Planner(test_settings())
        update_plan = planner.create_plan("Update post 123 with a stronger introduction and SEO refresh about AI content operations.")
        self.assertIn("update_post", [action.action for action in update_plan.actions])
        delete_plan = planner.create_plan("Delete post 123 and verify the action.")
        self.assertEqual(delete_plan.actions[0].action, "delete_post")

    def test_faq_keyword_triggers_schema_action(self):
        """Schema should be auto-included when FAQ is mentioned."""
        planner = Planner(test_settings())
        plan = planner.create_plan("Write a blog post about SEO tips with FAQ section and schema.")
        actions = [a.action for a in plan.actions]
        self.assertIn("generate_schema", actions)

    def test_list_count_detection_both_orderings(self):
        """Should detect both 'top 10' and '10 best' patterns."""
        from backend.services import _requested_list_count
        self.assertEqual(_requested_list_count("Top 10 AI innovations"), 10)
        self.assertEqual(_requested_list_count("5 best SEO tools"), 5)
        self.assertEqual(_requested_list_count("The leading 7 trends"), 7)

    def test_internal_links_included_when_requested(self):
        planner = Planner(test_settings())
        plan = planner.create_plan("Create a landing page with internal link suggestions and SEO.")
        actions = [a.action for a in plan.actions]
        self.assertIn("internal_link", actions)

    def test_source_material_triggers_research_step(self):
        planner = Planner(test_settings())
        plan = planner.create_plan(
            "Create a blog post from the pasted research and save as draft.",
            "Customer survey says SMEs need invoice automation, GST-ready reports, and WhatsApp reminders.",
        )
        actions = [a.action for a in plan.actions]
        self.assertIn("research_topic", actions)
        self.assertIn("generate_content", actions)


class AnthropicClientParsingTests(unittest.TestCase):
    def test_lenient_json_parser_repairs_control_characters(self):
        from backend.anthropic_client import _loads_json_lenient

        raw = '{"title":"Indoor Plants","html":"<p>Line one\nLine two\tTabbed</p>","excerpt":"ok"}'
        parsed = _loads_json_lenient(raw)
        self.assertEqual(parsed["title"], "Indoor Plants")
        self.assertIn("Line two", parsed["html"])


class ExecutionTests(unittest.TestCase):
    """Test end-to-end execution from planner through executor and verifier."""

    def test_executor_dry_run_completes_wordpress_action(self):
        settings = test_settings()
        plan = Planner(settings).create_plan("Write a blog post about AI search and publish it")
        Validator().validate(plan)
        results = Executor(settings).execute(plan)
        verification = Verifier().verify(plan, results)
        self.assertTrue(verification["ok"])
        self.assertTrue(any(result.dry_run for result in results))

    def test_fallback_content_has_unique_sections(self):
        """Each section should have different body text — no boilerplate repetition."""
        settings = test_settings()
        plan = Planner(settings).create_plan(
            "Create a short draft blog post about AI search optimization for business owners. "
            "Optimize SEO, add category SEO and tags AI, AEO. Do not publish."
        )
        results = Executor(settings).execute(plan)
        generated = next(result for result in results if result.action == "generate_content")
        html = generated.data["html"]

        # Word count should be substantial
        self.assertGreater(generated.data["word_count_estimate"], 900)

        # Should have FAQ section
        self.assertIn("<h2>FAQ</h2>", html)

        # Should have proper heading structure
        h2_count = len(re.findall(r"<h2[^>]*>", html, re.I))
        self.assertGreaterEqual(h2_count, 5)

        # No consecutive duplicate paragraphs
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.I | re.S)
        clean_paras = [re.sub(r"\s+", " ", p).strip().lower() for p in paragraphs if len(p.strip()) > 50]
        for i in range(1, len(clean_paras)):
            self.assertNotEqual(clean_paras[i], clean_paras[i - 1],
                                f"Duplicate paragraph found at position {i}")

        # Title should be topic-focused
        self.assertIn("AI Search Optimization", generated.data["title"])

    def test_presentation_landing_prompt_is_deep_and_correctly_titled(self):
        settings = test_settings()
        prompt = """Create a deep, professional landing page for an AI SEO automation service called "AI SEO Automation" for Business Insights.
Save as draft only. Do not publish.
Include hero section, problem section, solution section, detailed benefits, how it works, use cases, why Business Insights, quality and safety, FAQ schema, internal link suggestions, SEO optimization, category SEO, and tags AI SEO, SEO Automation, WordPress SEO, Content Automation, AEO, GEO.
Focus keyword: AI SEO automation
Meta title: AI SEO Automation Service for Business Websites
Meta description: Create SEO-ready WordPress content faster with AI SEO automation for blogs, landing pages, service pages, metadata, and publishing workflows.
Make the final page at least 1,200 words."""
        plan = Planner(settings).create_plan(prompt)
        generated_action = next(action for action in plan.actions if action.action == "generate_content")
        self.assertEqual(generated_action.params["title"], "AI SEO Automation")
        results = Executor(settings).execute(plan)
        generated = next(result for result in results if result.action == "generate_content")
        self.assertGreaterEqual(generated.data["word_count_estimate"], 1200)
        self.assertIn("The problem: SEO content is still too manual", generated.data["html"])
        seo_action = next(action for action in plan.actions if action.action == "seo_optimize")
        self.assertEqual(seo_action.params["focus_keyword"], "AI SEO automation")
        self.assertEqual(seo_action.params["meta_title"], "AI SEO Automation Service for Business Websites")

    def test_top_ten_prompt_keeps_requested_structure(self):
        settings = test_settings()
        prompt = '''Create a detailed, professional draft blog post called "Top 10 AI Innovations Happening in China" for Business Insights.

Goal:
Write a high-quality, research-style article for business leaders, investors, founders, and technology teams who want to understand how China is advancing in artificial intelligence.'''
        plan = Planner(settings).create_plan(prompt)
        actions = [action.action for action in plan.actions]
        self.assertIn("seo_optimize", actions)
        generated_action = next(action for action in plan.actions if action.action == "generate_content")
        self.assertEqual(generated_action.params["title"], "Top 10 AI Innovations Happening in China")
        results = Executor(settings).execute(plan)
        generated = next(result for result in results if result.action == "generate_content")
        html = generated.data["html"]
        self.assertGreaterEqual(generated.data["word_count_estimate"], 1200)
        self.assertIn("<h2>1. Large language models and enterprise AI assistants</h2>", html)
        self.assertIn("<h2>10. Open-source AI models and developer ecosystems</h2>", html)

    def test_minimal_prompt_still_produces_quality_content(self):
        """Even a simple prompt should produce structured, quality content."""
        settings = test_settings()
        plan = Planner(settings).create_plan("Write about SEO")
        results = Executor(settings).execute(plan)
        generated = next(result for result in results if result.action == "generate_content")
        self.assertGreater(generated.data["word_count_estimate"], 500)
        html = generated.data["html"]
        h2_count = len(re.findall(r"<h2[^>]*>", html, re.I))
        self.assertGreaterEqual(h2_count, 3)

    def test_pasted_source_material_is_used_in_fallback(self):
        settings = test_settings()
        prompt = "Create a professional blog post from the pasted source material for small business owners."
        source = (
            "SmartLedger helps Indian SMEs automate invoice creation, GST-ready tax reports, "
            "WhatsApp payment reminders, role-based staff access, and daily sales dashboards."
        )
        plan = Planner(settings).create_plan(prompt, source)
        results = Executor(settings).execute(plan)
        research = next(result for result in results if result.action == "research_topic")
        generated = next(result for result in results if result.action == "generate_content")
        self.assertEqual(research.data["source_mode"], "provided_source")
        self.assertIn("SmartLedger", generated.data["html"])
        self.assertIn("GST-ready", generated.data["html"])


class SEOServiceTests(unittest.TestCase):
    """Test the SEO optimization service for dynamic analysis."""

    def test_seo_generates_dynamic_recommendations(self):
        """SEO recommendations should be based on actual content analysis, not hardcoded."""
        seo = SEOService()
        result = seo.optimize(
            title="AI Search Optimization Guide",
            focus_keyword="AI search optimization",
            content="<h2>Introduction</h2><p>Short content about search.</p>",
        )
        # Should have content-specific recommendations
        self.assertTrue(len(result["recommendations"]) > 0)
        # Should include keyword density check
        self.assertIn("seo_checks", result)
        self.assertIn("keyword_density", result["seo_checks"])
        # Should include SEO score
        self.assertIn("seo_score", result)
        self.assertIsInstance(result["seo_score"], float)

    def test_seo_detects_missing_keyword_in_intro(self):
        """Should flag when focus keyword is missing from the opening paragraph."""
        seo = SEOService()
        result = seo.optimize(
            title="Digital Marketing Guide",
            focus_keyword="digital marketing",
            content="<p>This is a guide about online business strategies.</p><h2>Section</h2><p>More content.</p>",
        )
        self.assertFalse(result["seo_checks"]["keyword_in_introduction"])
        keyword_rec = [r for r in result["recommendations"] if "focus keyword" in r.lower() and "opening paragraph" in r.lower()]
        self.assertTrue(len(keyword_rec) > 0, "Should recommend adding keyword to intro")

    def test_seo_detects_keyword_in_intro(self):
        """Should pass when keyword is in the first paragraph."""
        seo = SEOService()
        result = seo.optimize(
            title="Digital Marketing Guide",
            focus_keyword="digital marketing",
            content="<p>This is a comprehensive digital marketing guide for teams.</p><h2>Section</h2><p>More content here.</p>",
        )
        self.assertTrue(result["seo_checks"]["keyword_in_introduction"])

    def test_seo_meta_description_generated_from_content(self):
        """Meta description should be derived from content, not just truncated."""
        seo = SEOService()
        content = "<p>AI search optimization is the practice of making content discoverable by AI-powered search engines and answer systems. This comprehensive guide covers strategies, tools, and best practices for businesses.</p>"
        result = seo.optimize(
            title="AI Search Optimization",
            focus_keyword="AI search optimization",
            content=content,
        )
        meta_desc = result["meta_description"]
        self.assertGreater(len(meta_desc), 80)
        self.assertLessEqual(len(meta_desc), 170)

    def test_seo_score_calculation(self):
        """SEO score should reflect actual content quality."""
        seo = SEOService()
        # Good content
        good_content = (
            "<p>AI search optimization is essential for modern businesses. Companies that invest in AI search optimization see better results.</p>"
            "<h2>Why AI search optimization matters</h2><p>AI search optimization helps businesses rank higher in search engines by aligning content with AI-powered algorithms and user intent.</p>" * 10
            + "<h2>FAQ</h2><h3>What is AI search optimization?</h3><p>It is the practice of optimizing content for AI-powered search.</p>"
        )
        good_result = seo.optimize("AI Search Optimization Guide", "AI search optimization", good_content)

        # Thin content
        thin_content = "<p>Short content.</p>"
        thin_result = seo.optimize("Guide", "optimization", thin_content)

        self.assertGreater(good_result["seo_score"], thin_result["seo_score"])


class SchemaTests(unittest.TestCase):
    """Test schema generation extracts real data from content."""

    def test_faq_schema_extracts_real_questions(self):
        """FAQPage schema should extract actual Q&A from content, not hardcode them."""
        seo = SEOService()
        content = (
            "<h2>Introduction</h2><p>Guide content here.</p>"
            "<h2>FAQ</h2>"
            "<h3>What is SEO?</h3><p>SEO stands for Search Engine Optimization. It is the practice of improving website visibility in search results.</p>"
            "<h3>How long does SEO take?</h3><p>SEO typically takes 3-6 months to show significant results, depending on competition and content quality.</p>"
            "<h3>Is SEO worth it?</h3><p>Yes, SEO provides long-term organic traffic that compounds over time, making it one of the best marketing investments.</p>"
        )
        schema = seo.schema("FAQPage", "SEO Guide", content)
        self.assertEqual(schema["@type"], "FAQPage")
        entities = schema["mainEntity"]
        self.assertGreaterEqual(len(entities), 3, "Should extract all 3 FAQ questions")
        self.assertEqual(entities[0]["name"], "What is SEO?")
        self.assertIn("Search Engine Optimization", entities[0]["acceptedAnswer"]["text"])

    def test_article_schema_includes_word_count(self):
        """Article schema should include word count from content."""
        seo = SEOService()
        content = "<h2>Heading</h2><p>Some content about SEO best practices for businesses. " * 50 + "</p>"
        schema = seo.schema("Article", "SEO Best Practices", content)
        self.assertEqual(schema["@type"], "Article")
        self.assertIn("wordCount", schema)
        self.assertGreater(schema["wordCount"], 0)


class InternalLinkTests(unittest.TestCase):
    """Test internal link suggestions are content-aware."""

    def test_internal_links_are_contextual(self):
        """Suggestions should be based on content themes, not hardcoded."""
        seo = SEOService()
        seo_content = "<p>This article covers SEO and content marketing strategies for WordPress websites.</p>"
        result = seo.internal_links(seo_content)
        suggestions = result["suggestions"]
        self.assertGreater(len(suggestions), 0)
        # Should include SEO-related and WordPress-related suggestions
        anchors = [s["anchor"].lower() for s in suggestions]
        has_seo = any("seo" in a for a in anchors)
        has_wp = any("wordpress" in a for a in anchors)
        self.assertTrue(has_seo or has_wp, f"Suggestions should be contextual. Got: {anchors}")

    def test_internal_links_include_content_themes(self):
        """Should extract and report content themes."""
        seo = SEOService()
        content = "<p>AI-powered SEO automation for WordPress content marketing.</p>"
        result = seo.internal_links(content)
        self.assertIn("content_themes", result)
        self.assertGreater(len(result["content_themes"]), 0)


class TransformTests(unittest.TestCase):
    """Test content transformation operations."""

    def test_summarize_produces_structured_output(self):
        """Summarize should create a structured summary, not just return input."""
        content_service = ContentService(test_settings())
        original = "<p>AI tools are transforming how businesses create content. They help teams produce drafts faster. They ensure consistency across pages. Teams should always review AI output for accuracy. The best approach combines AI speed with human judgment. This creates better results for search visibility and user experience.</p>"
        result = content_service.transform("summarize", "Summarize this content", original)
        self.assertIn("<h2>Summary</h2>", result["html"])
        self.assertGreater(result["word_count_estimate"], 10)

    def test_humanize_modifies_content(self):
        """Humanize should actually change the content, not return it unchanged."""
        content_service = ContentService(test_settings())
        original = "<p>The system is utilized to facilitate optimal content creation in order to enhance business outcomes.</p>"
        result = content_service.transform("humanize", "Humanize this", original)
        # Should replace corporate-speak
        self.assertNotIn("is utilized", result["html"])
        self.assertNotIn("facilitate", result["html"])
        self.assertNotIn("in order to", result["html"])


class VerifierTests(unittest.TestCase):
    """Test the enhanced verifier."""

    def test_verifier_detects_duplicate_paragraphs(self):
        """Verifier should catch duplicate consecutive paragraphs."""
        from backend.models import ActionResult, Plan, Action
        plan = Plan(goal="test", summary="test", actions=[
            Action(id="step_1", action="generate_content", params={}, critical=True),
            Action(id="step_2", action="create_post", params={"title": "Test", "status": "draft"}, critical=True),
        ])
        dupe_html = "<p>This is a substantial test paragraph that definitely contains more than fifty characters of content for detection.</p>" * 3
        results = [
            ActionResult("step_1", "generate_content", True, data={"html": dupe_html, "word_count_estimate": 50, "title": "Test"}),
            ActionResult("step_2", "create_post", True, data={"kind": "post", "link": "http://test.com/test/"}),
        ]
        verification = Verifier().verify(plan, results)
        content_quality = verification.get("content_quality", {})
        self.assertGreater(content_quality.get("checks", {}).get("duplicate_paragraphs", 0), 0)

    def test_verifier_checks_seo_quality(self):
        """Verifier should report on SEO completeness."""
        from backend.models import ActionResult, Plan, Action
        plan = Plan(goal="test", summary="test", actions=[
            Action(id="step_1", action="generate_content", params={}, critical=True),
            Action(id="step_2", action="seo_optimize", params={}, requires=["step_1"]),
            Action(id="step_3", action="create_post", params={"title": "Test", "status": "draft"}, critical=True),
        ])
        results = [
            ActionResult("step_1", "generate_content", True, data={"html": "<h2>Test</h2><p>Content</p>", "word_count_estimate": 500, "title": "Test Title"}),
            ActionResult("step_2", "seo_optimize", True, data={"meta_title": "Test Title", "meta_description": "Test desc", "focus_keyword": "test", "slug": "test-title", "seo_score": 7.5, "recommendations": ["Add more content"]}),
            ActionResult("step_3", "create_post", True, data={"kind": "post", "link": "http://test.com/test/"}),
        ]
        verification = Verifier().verify(plan, results)
        seo_quality = verification.get("seo_quality", {})
        self.assertTrue(seo_quality.get("applied"))
        self.assertTrue(seo_quality["checks"]["has_meta_title"])
        self.assertTrue(seo_quality["checks"]["has_focus_keyword"])


if __name__ == "__main__":
    unittest.main()
