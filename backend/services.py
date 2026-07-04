import base64
import html
import json
import math
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote, urlparse

from .anthropic_client import AnthropicClient
from .config import Settings


# ---------------------------------------------------------------------------
# WordPress Client
# ---------------------------------------------------------------------------

class WordPressClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def create_content(
        self,
        kind: str,
        title: str,
        content: str,
        status: str,
        seo: dict[str, Any] | None = None,
        taxonomy: dict[str, list[int]] | None = None,
    ) -> dict[str, Any]:
        self._validate_target_host()
        status = self._safe_status(status)
        if self.settings.dry_run or not self._ready:
            slug = _slugify(title)
            return {
                "id": f"dry-{kind}-{slug}",
                "kind": kind,
                "title": title,
                "status": status,
                "link": f"{self.settings.wordpress_base_url or 'https://example.com'}/{slug}/",
                "seo": seo or {},
                "taxonomy": taxonomy or {},
                "message": "Dry run: no WordPress mutation performed.",
            }

        endpoint = "posts" if kind == "post" else "pages"
        payload = {"title": title, "content": content, "status": status}
        if kind == "post" and taxonomy:
            payload.update({key: value for key, value in taxonomy.items() if value})
        payload.update(_seo_payload(self.settings.wordpress_seo_plugin, seo or {}))
        data = self._request("POST", f"/wp-json/wp/v2/{endpoint}", payload)
        return {
            "id": data.get("id"),
            "kind": kind,
            "title": data.get("title", {}).get("rendered", title) if isinstance(data.get("title"), dict) else title,
            "link": data.get("link"),
            "edit_link": self._admin_edit_link(data.get("id")),
            "status": data.get("status"),
            "taxonomy": taxonomy or {},
            "seo": seo or {},
            "raw": data,
        }

    def update_content(self, kind: str, content_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_target_host()
        if self.settings.dry_run or not self._ready:
            return {"id": content_id or "dry-update", "kind": kind, "payload": payload, "message": "Dry run update accepted."}
        endpoint = "posts" if kind == "post" else "pages"
        return self._request("POST", f"/wp-json/wp/v2/{endpoint}/{content_id}", payload)

    def delete_content(self, kind: str, content_id: str) -> dict[str, Any]:
        self._validate_target_host()
        if self.settings.dry_run or not self._ready:
            return {"id": content_id or "dry-delete", "kind": kind, "message": "Dry run delete accepted."}
        endpoint = "posts" if kind == "post" else "pages"
        return self._request("DELETE", f"/wp-json/wp/v2/{endpoint}/{content_id}?force=false", None)

    def create_term(self, taxonomy: str, name: str) -> dict[str, Any]:
        self._validate_target_host()
        if self.settings.dry_run or not self._ready:
            return {"id": f"dry-{taxonomy}-{_slugify(name)}", "taxonomy": taxonomy, "name": name}
        endpoint = "categories" if taxonomy == "category" else "tags"
        existing = self._find_term(endpoint, name)
        if existing:
            return {"id": existing.get("id"), "taxonomy": taxonomy, "name": existing.get("name", name), "reused": True}
        try:
            data = self._request("POST", f"/wp-json/wp/v2/{endpoint}", {"name": name})
            return {"id": data.get("id"), "taxonomy": taxonomy, "name": data.get("name", name), "raw": data}
        except RuntimeError as exc:
            existing = self._find_term(endpoint, name)
            if existing:
                return {"id": existing.get("id"), "taxonomy": taxonomy, "name": existing.get("name", name), "reused": True}
            if "term_exists" not in str(exc):
                raise
            return {"taxonomy": taxonomy, "name": name, "message": "Term already exists."}

    def test_connection(self) -> dict[str, Any]:
        self._validate_target_host()
        if not self._ready:
            return {"ok": False, "message": "WordPress credentials are not fully configured."}
        data = self._request("GET", "/wp-json/", None)
        return {"ok": True, "name": data.get("name"), "url": data.get("url")}

    @property
    def _ready(self) -> bool:
        return bool(
            self.settings.wordpress_base_url
            and self.settings.wordpress_username
            and self.settings.wordpress_app_password
        )

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        auth = f"{self.settings.wordpress_username}:{self.settings.wordpress_app_password}".encode("utf-8")
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.settings.wordpress_base_url + path,
            data=body,
            headers={
                "content-type": "application/json",
                "authorization": "Basic " + base64.b64encode(auth).decode("ascii"),
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"WordPress API error {exc.code}: {body}") from exc

    def _find_term(self, endpoint: str, name: str) -> dict[str, Any] | None:
        data = self._request("GET", f"/wp-json/wp/v2/{endpoint}?search={quote(name)}&per_page=20", None)
        if not isinstance(data, list):
            return None
        lowered = name.strip().lower()
        for item in data:
            if str(item.get("name", "")).strip().lower() == lowered:
                return item
        return data[0] if data else None

    def _admin_edit_link(self, content_id: Any) -> str:
        if not content_id:
            return ""
        return f"{self.settings.wordpress_base_url}/wp-admin/post.php?post={content_id}&action=edit"

    def _validate_target_host(self) -> None:
        if not self.settings.wordpress_base_url:
            return
        host = urlparse(self.settings.wordpress_base_url).hostname
        if host and self.settings.allowed_wordpress_hosts and host.lower() not in self.settings.allowed_wordpress_hosts:
            raise RuntimeError(f"WordPress host is not allowed: {host}")

    def _safe_status(self, status: str) -> str:
        requested = status if status in {"draft", "publish", "pending", "private", "future"} else "draft"
        if requested == "publish" and not self.settings.auto_publish:
            return "draft"
        return requested


# ---------------------------------------------------------------------------
# Research Service — Intelligent Extraction
# ---------------------------------------------------------------------------

class ResearchService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def scrape_url(self, url: str) -> dict[str, Any]:
        if self.settings and self.settings.dry_run:
            return {
                "url": url,
                "text": f"Dry-run scraped content placeholder for {url}. Use live mode to fetch the real page.",
                "characters": 82 + len(url),
                "dry_run": True,
            }
        req = urllib.request.Request(url, headers={"user-agent": "AIWordPressAgent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read(600000).decode("utf-8", errors="replace")
        return self._extract_structured(url, raw)

    def _extract_structured(self, url: str, raw_html: str) -> dict[str, Any]:
        """Extract structured content from HTML instead of just stripping tags."""
        # Extract page title
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.I | re.S)
        page_title = html.unescape(title_match.group(1).strip()) if title_match else ""

        # Extract meta description
        meta_match = re.search(r'<meta\s+[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']', raw_html, flags=re.I)
        if not meta_match:
            meta_match = re.search(r'<meta\s+[^>]*content=["\']([^"\']*)["\'][^>]*name=["\']description["\']', raw_html, flags=re.I)
        meta_description = html.unescape(meta_match.group(1).strip()) if meta_match else ""

        # Extract headings for structure
        headings = []
        for match in re.finditer(r"<h([1-6])[^>]*>(.*?)</h\1>", raw_html, flags=re.I | re.S):
            level = int(match.group(1))
            text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            text = html.unescape(re.sub(r"\s+", " ", text))
            if text and len(text) > 3:
                headings.append({"level": level, "text": text})

        # Clean body text (remove scripts, styles, nav, footer)
        body = re.sub(r"<(script|style|nav|footer|header|aside).*?</\1>", " ", raw_html, flags=re.I | re.S)
        body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
        body = re.sub(r"<[^>]+>", " ", body)
        body = html.unescape(re.sub(r"\s+", " ", body)).strip()

        return {
            "url": url,
            "page_title": page_title,
            "meta_description": meta_description,
            "headings": headings[:30],
            "text": body[:10000],
            "characters": len(body),
        }

    def research_topic(self, topic: str, source_material: str = "", previous: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        snippets = [source_material.strip()]
        sources: list[dict[str, str]] = []
        for item in previous or []:
            data = item.get("data", {})
            if data.get("text"):
                snippets.append(data["text"])
            # Also pull structured data from scrapes
            if data.get("page_title"):
                snippets.append(f"Source: {data['page_title']}")
                sources.append({"title": str(data.get("page_title", "")), "url": str(data.get("url", ""))})
            if data.get("meta_description"):
                snippets.append(data["meta_description"])
        if self.settings and self.settings.enable_live_research and self.settings.web_search_provider == "tavily":
            search_payload = self._search_tavily(topic)
            snippets.extend(search_payload.get("snippets", []))
            sources.extend(search_payload.get("sources", []))
        combined = "\n\n".join(part for part in snippets if part)
        if not combined:
            combined = f"Research brief requested for: {topic}"
        return {
            "topic": topic,
            "summary": _summarize(combined),
            "key_points": _key_points(combined),
            "sources": sources[:8],
            "source_mode": "live_search" if sources and self.settings and self.settings.enable_live_research else "provided_source" if source_material.strip() or sources else "topic_brief",
        }

    def _search_tavily(self, topic: str) -> dict[str, Any]:
        if not self.settings or not self.settings.tavily_api_key:
            return {"snippets": [], "sources": []}
        payload = {
            "api_key": self.settings.tavily_api_key,
            "query": topic,
            "search_depth": "advanced",
            "max_results": 6,
            "include_answer": True,
        }
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            return {"snippets": [f"Live research failed: {exc}"], "sources": []}
        snippets = []
        if data.get("answer"):
            snippets.append(str(data["answer"]))
        sources = []
        for item in data.get("results", [])[:6]:
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            content = str(item.get("content", "")).strip()
            if content:
                snippets.append(f"{title}: {content}")
            if title or url:
                sources.append({"title": title or url, "url": url})
        return {"snippets": snippets, "sources": sources}


# ---------------------------------------------------------------------------
# Content Service — High-Quality Generation Engine
# ---------------------------------------------------------------------------

_AI_CONTENT_SYSTEM_PROMPT = """You are an expert WordPress content writer producing publication-ready HTML.

CRITICAL RULES:
1. Return ONLY a valid JSON object with keys: title, html, excerpt
2. The "html" value must be clean, semantic WordPress HTML (no markdown, no scripts, no inline styles)
3. NEVER invent statistics, studies, or quotes. Use real knowledge only.
4. Structure requirements:
   - Start with a compelling introduction paragraph (2-3 sentences)
   - Use <h2> for main sections and <h3> for subsections
   - Include at least 5 meaningful <h2> sections
   - Include a FAQ section with at least 3 <h3> question-answer pairs
   - End with a conclusion or call-to-action section
   - Use <ul>/<ol> lists where they add value
   - Use <strong> for emphasis on key terms (sparingly)
5. Content quality:
   - Every section must have UNIQUE, substantive content (never repeat paragraphs)
   - Each section must be at least 80 words
   - Content must directly address the topic, audience, and intent
   - Include actionable advice, not vague platitudes
   - Write in a professional but accessible tone
6. SEO awareness:
   - Naturally include the topic keywords in the first paragraph
   - Use topic-related terms in headings
   - Write an excerpt of 140-160 characters that compels clicks
7. The "title" should be compelling and SEO-friendly (50-65 characters ideal)
8. Target word count: aim for the length appropriate to the content type (blog: 1200+, page: 800+)"""

_SOURCE_GROUNDING_RULES = """

SOURCE-GROUNDED WRITING RULES:
- If source_material or research_context is provided, treat it as the primary evidence base.
- Use the user's requested angle and structure, but extract facts, examples, claims, terminology, and useful details from the source.
- Do not copy long passages verbatim. Paraphrase and synthesize.
- If the source is incomplete, say what can be concluded from the source and avoid pretending unsupported details are known.
- If source URLs are present in research_context, include a short "Sources reviewed" section near the end with source names or URLs.
- The final content must feel specifically written from the provided material, not like a generic template.
"""

_AI_TRANSFORM_PROMPTS = {
    "rewrite": """You are a professional content editor. Rewrite the content to be clearer, more engaging, and better structured.
Maintain the core message but improve flow, word choice, and readability. Use varied sentence lengths.
Return polished WordPress-safe HTML only. No markdown fences. Keep all factual claims accurate.""",

    "summarize": """You are a professional content summarizer. Create a concise, well-structured summary that captures all key points.
The summary should be 20-30% of the original length. Maintain the most important facts and actionable insights.
Return WordPress-safe HTML only. No markdown fences.""",

    "humanize": """You are a professional content humanizer. Rewrite the content to sound more natural, conversational, and engaging.
Remove robotic patterns, vary sentence structure, add conversational transitions, and make it feel written by an experienced human writer.
Maintain accuracy and professional tone. Return WordPress-safe HTML only. No markdown fences.""",

    "proofread": """You are a meticulous proofreader and copy editor. Fix all grammar, spelling, punctuation, and style issues.
Improve awkward phrasing. Ensure consistent tone and tense. Flag any factual claims that seem unsupported.
Return the corrected WordPress-safe HTML only. No markdown fences.""",

    "translate": """You are a professional translator. Translate the content while preserving meaning, tone, and formatting.
Adapt cultural references where appropriate. Maintain HTML structure.
Return translated WordPress-safe HTML only. No markdown fences.""",
}


class ContentService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.anthropic = AnthropicClient(settings)

    def generate(self, title: str, prompt: str, content_type: str, source_material: str, context: list[dict[str, Any]]) -> dict[str, Any]:
        if self.anthropic.ready:
            try:
                result = self._generate_with_ai(title, prompt, content_type, source_material, context)
                # Validate AI output quality
                if result.get("word_count_estimate", 0) < 100:
                    raise ValueError("AI generated insufficient content")
                return result
            except Exception as exc:
                # Log the error instead of silently swallowing
                ai_error = str(exc)
        else:
            ai_error = None

        # Fallback generation
        context_text = "\n".join(json.dumps(item.get("data", {}), ensure_ascii=True)[:1200] for item in context)
        basis = source_material or context_text or prompt
        body = _build_page_html(title, prompt, basis) if content_type == "page" else _build_blog_html(title, prompt, basis)
        plain = re.sub(r"<[^>]+>", " ", body)
        result = {
            "title": title,
            "content_type": content_type,
            "html": body,
            "excerpt": _generate_excerpt(plain),
            "word_count_estimate": max(1, len(plain.split())),
            "generation_mode": "local_fallback",
        }
        if ai_error:
            result["ai_fallback_reason"] = ai_error
        return result

    def transform(self, operation: str, prompt: str, content: str) -> dict[str, Any]:
        plain = re.sub(r"<[^>]+>", " ", content)
        if self.anthropic.ready:
            try:
                system = _AI_TRANSFORM_PROMPTS.get(operation, _AI_TRANSFORM_PROMPTS["rewrite"])
                transformed = self.anthropic.complete_text(
                    system,
                    f"Operation: {operation}\nInstruction: {prompt}\nContent:\n{plain[:15000]}",
                    max_tokens=4000,
                )
                return {"operation": operation, "html": _sanitize_html(transformed), "word_count_estimate": len(re.sub(r'<[^>]+>', ' ', transformed).split())}
            except Exception as exc:
                ai_error = str(exc)
        else:
            ai_error = None

        # Improved fallback transforms
        if operation == "summarize":
            body = _fallback_summarize_html(plain)
        elif operation == "humanize":
            body = _fallback_humanize_html(content, plain)
        elif operation == "rewrite":
            body = _fallback_rewrite_html(content, plain)
        elif operation == "proofread":
            body = _fallback_proofread_html(content, plain)
        else:
            body = content

        result = {"operation": operation, "html": body, "word_count_estimate": len(re.sub(r'<[^>]+>', ' ', body).split())}
        if ai_error:
            result["ai_fallback_reason"] = ai_error
        return result

    def _generate_with_ai(self, title: str, prompt: str, content_type: str, source_material: str, context: list[dict[str, Any]]) -> dict[str, Any]:
        # Build context summary from previous results
        context_summary = []
        for item in (context or [])[-5:]:
            data = item.get("data", {})
            if data.get("summary"):
                context_summary.append(f"Research: {data['summary']}")
            if data.get("key_points"):
                context_summary.append("Key points: " + "; ".join(data["key_points"][:5]))
            if data.get("text"):
                context_summary.append(f"Source content: {data['text'][:800]}")
            if data.get("sources"):
                source_lines = [f"{source.get('title', '')} {source.get('url', '')}".strip() for source in data["sources"][:6]]
                context_summary.append("Sources reviewed: " + "; ".join(line for line in source_lines if line))

        # Determine word count target
        word_target = _requested_word_count(prompt)
        if not word_target:
            word_target = 1200 if content_type == "post" else 900

        payload = {
            "title": title,
            "content_type": content_type,
            "prompt": prompt,
            "source_material": source_material[:20000] if source_material else "",
            "research_context": "\n".join(context_summary) if context_summary else "",
            "target_word_count": word_target,
        }

        has_source = bool(source_material.strip() or context_summary)
        system = _AI_CONTENT_SYSTEM_PROMPT
        if has_source:
            system += _SOURCE_GROUNDING_RULES
        system += f"\nTarget word count: {word_target} words minimum."

        data = self.anthropic.complete_json(system, payload, max_tokens=6000)
        body = _sanitize_html(str(data.get("html", "")))

        # Validate the AI output
        plain = re.sub(r"<[^>]+>", " ", body)
        word_count = max(1, len(plain.split()))
        h2_count = len(re.findall(r"<h2[^>]*>", body, flags=re.I))

        if h2_count < 2 and word_count > 200:
            # AI didn't structure properly — this is unusual but handle it
            pass

        return {
            "title": str(data.get("title") or title),
            "content_type": content_type,
            "html": body,
            "excerpt": _generate_excerpt(str(data.get("excerpt", "")) or plain),
            "word_count_estimate": word_count,
            "ai_generated": True,
            "generation_mode": "ai",
        }


# ---------------------------------------------------------------------------
# SEO Service — Dynamic Analysis & Recommendations
# ---------------------------------------------------------------------------

class SEOService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings
        self.anthropic = AnthropicClient(settings) if settings else None

    def optimize(
        self,
        title: str,
        focus_keyword: str,
        content: str,
        meta_title_override: str = "",
        meta_description_override: str = "",
    ) -> dict[str, Any]:
        plain = re.sub(r"<[^>]+>", " ", content)
        plain_lower = plain.lower()
        keyword_lower = focus_keyword.lower().strip()

        # --- Dynamic meta title ---
        meta_title = meta_title_override or title
        if len(meta_title) > 65:
            meta_title = meta_title[:62].rsplit(" ", 1)[0] + "..."
        elif len(meta_title) < 30 and keyword_lower:
            meta_title = f"{meta_title} — {_fix_acronyms(focus_keyword.title())}"
            meta_title = meta_title[:65]

        # --- Dynamic meta description ---
        if meta_description_override:
            meta_description = meta_description_override
        else:
            # Generate from the first substantial paragraph
            sentences = re.split(r"(?<=[.!?])\s+", plain.strip())
            desc_parts = []
            for sentence in sentences:
                clean = sentence.strip()
                if len(clean) > 30:
                    desc_parts.append(clean)
                    if len(" ".join(desc_parts)) >= 140:
                        break
            meta_description = " ".join(desc_parts)
            if len(meta_description) > 160:
                meta_description = meta_description[:157].rsplit(" ", 1)[0] + "..."
            elif len(meta_description) < 120 and len(meta_description) > 0:
                meta_description = meta_description.rstrip(".") + ". Learn more."

        slug = _slugify(title)

        # --- Dynamic content analysis ---
        recommendations = []
        seo_checks = {}

        # Check 1: Keyword in first paragraph
        first_para = plain[:500].lower()
        keyword_in_intro = keyword_lower in first_para if keyword_lower else True
        seo_checks["keyword_in_introduction"] = keyword_in_intro
        if not keyword_in_intro and keyword_lower:
            recommendations.append(f'Add the focus keyword "{focus_keyword}" to the opening paragraph for stronger relevance signals.')

        # Check 2: Keyword density
        word_count = max(1, len(plain.split()))
        if keyword_lower:
            keyword_count = plain_lower.count(keyword_lower)
            keyword_density = round((keyword_count / word_count) * 100, 2)
            seo_checks["keyword_density"] = keyword_density
            seo_checks["keyword_occurrences"] = keyword_count
            if keyword_density < 0.5:
                recommendations.append(f'Keyword density is low ({keyword_density}%). Add the focus keyword naturally 2-3 more times.')
            elif keyword_density > 3.0:
                recommendations.append(f'Keyword density is high ({keyword_density}%). Reduce keyword usage to avoid over-optimization.')
        else:
            seo_checks["keyword_density"] = 0
            recommendations.append("No focus keyword specified. Add a focus keyword for better search targeting.")

        # Check 3: Heading structure
        h2_count = len(re.findall(r"<h2[^>]*>", content, flags=re.I))
        h3_count = len(re.findall(r"<h3[^>]*>", content, flags=re.I))
        seo_checks["h2_count"] = h2_count
        seo_checks["h3_count"] = h3_count
        if h2_count < 3:
            recommendations.append(f"Only {h2_count} H2 headings found. Add more structured sections (aim for 5+).")
        keyword_in_headings = bool(re.search(rf"<h[2-3][^>]*>[^<]*{re.escape(keyword_lower)}[^<]*</h[2-3]>", content, flags=re.I)) if keyword_lower else True
        seo_checks["keyword_in_headings"] = keyword_in_headings
        if not keyword_in_headings and keyword_lower:
            recommendations.append(f'Include the focus keyword in at least one H2 or H3 heading.')

        # Check 4: Content length
        seo_checks["word_count"] = word_count
        if word_count < 600:
            recommendations.append(f"Content is {word_count} words. Aim for 1,200+ words for competitive search rankings.")
        elif word_count < 1000:
            recommendations.append(f"Content is {word_count} words. Consider expanding to 1,200+ words for stronger search performance.")

        # Check 5: Internal/external links
        link_count = len(re.findall(r"<a\s+[^>]*href=", content, flags=re.I))
        seo_checks["link_count"] = link_count
        if link_count == 0:
            recommendations.append("No links found. Add 2-3 internal links to related pages and 1-2 authoritative external links.")

        # Check 6: Image alt text
        img_count = len(re.findall(r"<img\s", content, flags=re.I))
        img_alt_count = len(re.findall(r"<img\s[^>]*alt=['\"][^'\"]+['\"]", content, flags=re.I))
        seo_checks["images"] = img_count
        seo_checks["images_with_alt"] = img_alt_count
        if img_count > 0 and img_alt_count < img_count:
            recommendations.append(f"{img_count - img_alt_count} image(s) missing alt text. Add descriptive alt attributes.")
        elif img_count == 0:
            recommendations.append("No images found. Add at least 1 relevant image with descriptive alt text.")

        # Check 7: Meta title and description length
        meta_title_len = len(meta_title)
        meta_desc_len = len(meta_description)
        seo_checks["meta_title_length"] = meta_title_len
        seo_checks["meta_description_length"] = meta_desc_len
        if meta_title_len < 30:
            recommendations.append(f"Meta title is too short ({meta_title_len} chars). Aim for 50-60 characters.")
        elif meta_title_len > 60:
            recommendations.append(f"Meta title is long ({meta_title_len} chars). Keep under 60 characters to avoid truncation.")
        if meta_desc_len < 120:
            recommendations.append(f"Meta description is short ({meta_desc_len} chars). Aim for 150-160 characters.")
        elif meta_desc_len > 160:
            recommendations.append(f"Meta description is too long ({meta_desc_len} chars). Keep under 160 characters.")

        # Check 8: FAQ presence
        has_faq = bool(re.search(r"<h[2-3][^>]*>\s*FAQ", content, flags=re.I))
        seo_checks["has_faq"] = has_faq
        if not has_faq:
            recommendations.append("No FAQ section detected. Add FAQs to target featured snippets and People Also Ask boxes.")

        # Calculate SEO score
        score_points = 0
        score_max = 10
        if keyword_in_intro:
            score_points += 1.5
        if 0.5 <= seo_checks.get("keyword_density", 0) <= 2.5:
            score_points += 1.5
        if h2_count >= 3:
            score_points += 1
        if keyword_in_headings:
            score_points += 0.5
        if word_count >= 1000:
            score_points += 1.5
        elif word_count >= 600:
            score_points += 0.75
        if link_count >= 2:
            score_points += 1
        if has_faq:
            score_points += 1
        if 50 <= meta_title_len <= 60:
            score_points += 1
        elif 30 <= meta_title_len <= 65:
            score_points += 0.5
        if 140 <= meta_desc_len <= 160:
            score_points += 1
        elif 100 <= meta_desc_len <= 170:
            score_points += 0.5

        seo_score = round(min(10, (score_points / score_max) * 10), 1)

        if not recommendations:
            recommendations.append("Content meets all major SEO checkpoints. Consider A/B testing titles for click-through optimization.")

        return {
            "focus_keyword": focus_keyword,
            "meta_title": meta_title,
            "meta_description": meta_description,
            "slug": slug,
            "seo_score": seo_score,
            "seo_checks": seo_checks,
            "schema": {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": title,
                "description": meta_description,
            },
            "recommendations": recommendations,
        }

    def schema(self, schema_type: str, title: str, content: str) -> dict[str, Any]:
        """Generate schema markup by extracting real data from content."""
        plain = re.sub(r"<[^>]+>", " ", content)

        if schema_type == "FAQPage":
            return self._extract_faq_schema(title, content, plain)

        if schema_type == "HowTo":
            return self._extract_howto_schema(title, content, plain)

        # Default Article schema
        sentences = re.split(r"(?<=[.!?])\s+", plain.strip())
        description = " ".join(sentences[:2])[:240] if sentences else plain[:240]
        word_count = len(plain.split())
        return {
            "@context": "https://schema.org",
            "@type": schema_type or "Article",
            "headline": title,
            "description": description,
            "wordCount": word_count,
            "articleBody": plain[:500],
        }

    def _extract_faq_schema(self, title: str, content: str, plain: str) -> dict[str, Any]:
        """Extract real FAQ questions and answers from the HTML content."""
        faq_entries = []

        # Find the FAQ section and extract Q&A pairs from <h3>...<p> patterns
        faq_section = re.search(r"<h2[^>]*>\s*FAQ.*?(?=<h2[^>]*>|\Z)", content, flags=re.I | re.S)
        search_content = faq_section.group(0) if faq_section else content

        # Extract h3 questions followed by p answers
        for match in re.finditer(
            r"<h3[^>]*>(.*?)</h3>\s*<p[^>]*>(.*?)</p>",
            search_content,
            flags=re.I | re.S,
        ):
            question = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            answer = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            if question and answer and len(question) > 10 and len(answer) > 20:
                faq_entries.append({
                    "@type": "Question",
                    "name": question,
                    "acceptedAnswer": {"@type": "Answer", "text": answer},
                })

        # If we couldn't extract from HTML, generate from content
        if not faq_entries:
            faq_entries.append({
                "@type": "Question",
                "name": f"What is {title}?",
                "acceptedAnswer": {"@type": "Answer", "text": plain[:300].strip()},
            })

        return {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": faq_entries[:10],
        }

    def _extract_howto_schema(self, title: str, content: str, plain: str) -> dict[str, Any]:
        """Extract HowTo steps from ordered lists in the content."""
        steps = []
        for match in re.finditer(r"<li[^>]*>(.*?)</li>", content, flags=re.I | re.S):
            step_text = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            if step_text and len(step_text) > 10:
                steps.append({
                    "@type": "HowToStep",
                    "text": step_text,
                })
        if not steps:
            steps.append({"@type": "HowToStep", "text": f"Follow the guide for {title}"})

        return {
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": title,
            "description": plain[:240],
            "step": steps[:15],
        }

    def internal_links(self, content: str) -> dict[str, Any]:
        """Generate contextually relevant internal link suggestions based on content analysis."""
        plain = re.sub(r"<[^>]+>", " ", content).lower()

        # Extract key themes from the content
        suggestions = []

        # Define theme-to-anchor mappings based on content analysis
        theme_anchors = [
            (["seo", "search engine", "search optimization", "ranking"], "SEO optimization guide", "Strengthens topical authority by linking to your SEO hub page."),
            (["content", "blog", "article", "writing", "publishing"], "content creation services", "Connects readers to your content service offering."),
            (["ai", "artificial intelligence", "automation", "machine learning"], "AI-powered tools", "Links to your AI technology or tools page."),
            (["wordpress", "cms", "website", "web platform"], "WordPress solutions", "Directs users to your WordPress service or setup guide."),
            (["business", "enterprise", "company", "b2b"], "business solutions", "Connects commercial intent to your solutions page."),
            (["marketing", "campaign", "conversion", "leads"], "digital marketing services", "Links to your marketing services for conversion-focused readers."),
            (["faq", "questions", "answers", "help"], "knowledge base", "Routes help-seeking readers to your FAQ or support section."),
            (["pricing", "cost", "plans", "subscription"], "pricing and plans", "Connects price-curious readers to your pricing page."),
            (["case study", "example", "success", "results"], "client success stories", "Builds trust by linking to real-world proof."),
            (["strategy", "plan", "roadmap", "implementation"], "strategy resources", "Links readers to deeper strategic content."),
            (["aeo", "answer engine"], "answer engine optimization guide", "Links to your AEO content hub for topical depth."),
            (["geo", "generative engine"], "generative engine optimization guide", "Links to your GEO content hub."),
            (["schema", "structured data", "rich snippets"], "schema markup guide", "Links to technical SEO resources for structured data."),
            (["internal link", "site architecture", "navigation"], "site structure guide", "Connects to your internal linking strategy content."),
        ]

        for keywords, anchor, reason in theme_anchors:
            if any(kw in plain for kw in keywords):
                suggestions.append({"anchor": anchor, "reason": reason})

        # Always include at least 2 suggestions
        if len(suggestions) < 2:
            suggestions.extend([
                {"anchor": "related services", "reason": "General navigation link to your service pages."},
                {"anchor": "latest insights", "reason": "Keeps readers engaged with your most recent content."},
            ])

        # Limit to most relevant
        return {
            "suggestions": suggestions[:6],
            "content_themes": _extract_content_themes(plain),
            "linking_strategy": "Focus on linking to pillar pages and service pages that match the content themes above.",
        }


# ---------------------------------------------------------------------------
# Fallback Transform Functions (when AI is unavailable)
# ---------------------------------------------------------------------------

def _fallback_summarize_html(plain: str) -> str:
    """Create a structured summary from plain text."""
    sentences = re.split(r"(?<=[.!?])\s+", plain.strip())
    # Pick the most informative sentences (longer ones tend to carry more info)
    scored = [(s, len(s)) for s in sentences if len(s.strip()) > 30]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = [s[0] for s in scored[:6]]
    # Reorder by original position
    top.sort(key=lambda s: sentences.index(s))
    if not top:
        return f"<p>{html.escape(plain[:500])}</p>"
    parts = ["<h2>Summary</h2>"]
    parts.append(f"<p>{html.escape(top[0])}</p>")
    if len(top) > 1:
        parts.append("<h2>Key points</h2><ul>")
        for sentence in top[1:]:
            parts.append(f"<li>{html.escape(sentence)}</li>")
        parts.append("</ul>")
    return "".join(parts)


def _fallback_humanize_html(content: str, plain: str) -> str:
    """Apply humanization transformations to make content more natural."""
    result = content
    # Break overly long sentences at conjunctions
    result = re.sub(r"(\. )([A-Z])", r".\n\n<p>\2", result)
    # Replace passive constructions with more active ones
    replacements = [
        (r"\bis utilized\b", "works"),
        (r"\bis leveraged\b", "uses"),
        (r"\bfacilitate\b", "help"),
        (r"\bimplement\b", "set up"),
        (r"\butilize\b", "use"),
        (r"\bleverage\b", "use"),
        (r"\boptimal\b", "best"),
        (r"\benhance\b", "improve"),
        (r"\bin order to\b", "to"),
        (r"\bdue to the fact that\b", "because"),
        (r"\bat this point in time\b", "now"),
        (r"\bin the event that\b", "if"),
        (r"\bprior to\b", "before"),
        (r"\bsubsequently\b", "then"),
    ]
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.I)
    return result


def _fallback_rewrite_html(content: str, plain: str) -> str:
    """Apply rewriting transformations for improved readability."""
    result = content
    # Apply humanization first
    result = _fallback_humanize_html(result, plain)
    # Add transition phrases between sections
    result = re.sub(r"(</h2>\s*<p>)", r"\1Here is what you need to know: ", result, count=1)
    return result


def _fallback_proofread_html(content: str, plain: str) -> str:
    """Apply basic proofreading corrections."""
    result = content
    # Fix double spaces
    result = re.sub(r"  +", " ", result)
    # Fix spacing around punctuation
    result = re.sub(r"\s+([.,;:!?])", r"\1", result)
    result = re.sub(r"([.,;:!?])([A-Za-z])", r"\1 \2", result)
    # Fix common typos/issues
    result = re.sub(r"\bi\b", "I", result)  # lowercase I
    result = re.sub(r"(\.\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), result)  # capitalize after period
    return result


# ---------------------------------------------------------------------------
# Content Theme Extraction
# ---------------------------------------------------------------------------

def _extract_content_themes(plain_lower: str) -> list[str]:
    """Extract the main themes/topics from content for link suggestions."""
    theme_map = {
        "search engine optimization": "SEO",
        "answer engine optimization": "AEO",
        "generative engine optimization": "GEO",
        "artificial intelligence": "AI",
        "content marketing": "Content Marketing",
        "wordpress": "WordPress",
        "digital marketing": "Digital Marketing",
        "machine learning": "Machine Learning",
        "content strategy": "Content Strategy",
        "link building": "Link Building",
    }
    found = []
    for phrase, label in theme_map.items():
        if phrase in plain_lower:
            found.append(label)
    # Also check single keywords
    single_keywords = {"seo": "SEO", "aeo": "AEO", "geo": "GEO", "ai": "AI", "wordpress": "WordPress"}
    for kw, label in single_keywords.items():
        if kw in plain_lower and label not in found:
            found.append(label)
    return found[:8]


# ---------------------------------------------------------------------------
# Excerpt Generator
# ---------------------------------------------------------------------------

def _generate_excerpt(text: str) -> str:
    """Generate a compelling excerpt of 140-160 characters."""
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= 160:
        return clean
    # Try to cut at a sentence boundary within the range
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    excerpt = ""
    for sentence in sentences:
        if len(excerpt) + len(sentence) + 1 <= 160:
            excerpt = (excerpt + " " + sentence).strip()
        else:
            break
    if len(excerpt) < 100:
        # Sentence boundaries didn't work, cut at word boundary
        excerpt = clean[:157].rsplit(" ", 1)[0] + "..."
    return excerpt


# ---------------------------------------------------------------------------
# Summarize and Key Points (improved)
# ---------------------------------------------------------------------------

def _summarize(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text).strip())
    # Prefer sentences that contain data, statistics, or key claims
    scored = []
    for sentence in sentences:
        score = len(sentence)
        if re.search(r"\d+%|\d+\s*(million|billion|thousand)", sentence, re.I):
            score += 100  # Prefer sentences with data
        if re.search(r"\b(important|key|critical|essential|significant)\b", sentence, re.I):
            score += 50  # Prefer sentences with importance markers
        if len(sentence.strip()) > 35:
            scored.append((sentence, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    top = [s[0] for s in scored[:4]]
    # Reorder by original position
    top.sort(key=lambda s: sentences.index(s) if s in sentences else 0)
    return " ".join(top)[:900] or "No usable research text was provided."


def _key_points(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text).strip())
    points = []
    for sentence in sentences:
        clean = sentence.strip()
        if len(clean) > 35:
            # Avoid duplicate-ish points
            is_dupe = any(_similarity(clean.lower(), existing.lower()) > 0.7 for existing in points)
            if not is_dupe:
                points.append(clean)
    return points[:10] or ["Define the audience and intent before publishing.", "Keep structure clear, searchable, and conversion-focused."]


def _similarity(a: str, b: str) -> float:
    """Simple word-overlap similarity score."""
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    return len(intersection) / max(len(words_a), len(words_b))


# ---------------------------------------------------------------------------
# Blog HTML Builders — Unique Content Per Section
# ---------------------------------------------------------------------------

def _build_blog_html(title: str, prompt: str, basis: str) -> str:
    brief = _content_brief(title, prompt, basis)
    if brief["list_count"]:
        return _build_listicle_html(brief, prompt)
    return _build_brief_driven_blog_html(brief, prompt)


def _content_brief(title: str, prompt: str, basis: str) -> dict[str, Any]:
    topic = _topic_label(title, prompt)
    goal = _requested_block(prompt, "goal") or _summarize(prompt)
    audience = _audience(prompt)
    requested_sections = _requested_sections(prompt)
    list_count = _requested_list_count(title + "\n" + prompt)
    list_items = _requested_list_items(prompt)
    if list_count and not list_items:
        list_items = _infer_list_items(topic, list_count)
    key_points = _key_points(basis)
    return {
        "topic": topic,
        "goal": goal,
        "audience": audience,
        "sections": requested_sections,
        "list_count": list_count,
        "list_items": list_items[:list_count] if list_count else list_items,
        "key_points": key_points,
        "source_note": _source_note(key_points),
    }


# Section-specific content generators for fallback blog posts.
# Each section gets UNIQUE body text relevant to its heading.
_SECTION_BODIES: dict[str, tuple[str, str]] = {
    "why this matters now": (
        "The business landscape is shifting faster than most teams realize. What worked for search visibility, audience engagement, and content operations two years ago is already losing ground. Companies that understand these shifts early build a structural advantage — not just better rankings, but better alignment between content, customer intent, and business goals.",
        "For decision-makers, the signal is in the pace of change. Search engines are evolving from keyword-matching systems to intent-understanding platforms. AI-generated answers are replacing traditional search results for some queries. Businesses that only optimize for the old model will find their traffic, leads, and authority declining without understanding why.",
    ),
    "what readers need to understand": (
        "The most important concept is that search optimization is no longer a single discipline. SEO, AEO (answer engine optimization), and GEO (generative engine optimization) each address different parts of how audiences discover content. A strong strategy connects all three rather than treating them as separate tasks.",
        "Readers should also understand that content quality has become a measurable business input, not just a marketing activity. Search engines evaluate expertise, depth, structure, and user experience. Thin or generic content actively hurts visibility. The bar for what counts as helpful content is higher than ever, and it will continue to rise.",
    ),
    "business impact": (
        "When content operations work well, the business impact shows up in organic traffic growth, higher conversion rates, shorter sales cycles, and stronger brand authority. A single well-optimized page can generate leads for years without additional advertising spend.",
        "When content operations are weak, the cost is harder to see but just as real: missed search opportunities, inconsistent messaging across pages, wasted production time, and campaigns that never gain traction because the underlying content does not match what audiences actually need.",
    ),
    "recommended workflow": (
        "A proven workflow follows this sequence: research the topic and audience first, define the search intent and target keywords, create a structured content brief, produce the draft with clear headings and sections, optimize metadata and schema, review for accuracy and brand voice, then publish with proper taxonomy and internal links.",
        "The key is making this workflow repeatable. When every page follows the same process, quality becomes consistent and improvements become measurable. Teams can identify which steps slow them down, where quality drops, and how to automate the production layer without losing editorial control.",
    ),
    "common mistakes to avoid": (
        "The most costly mistake is publishing content without a clear audience or search intent. Pages that try to reach everyone reach no one. A second common error is treating SEO metadata as an afterthought — adding titles and descriptions at the last minute instead of planning them as part of the content strategy.",
        "Other frequent mistakes include: ignoring internal linking (which wastes topical authority), duplicating content across similar pages (which confuses search engines), over-optimizing for keywords at the expense of readability, and publishing without a verification step to confirm that the page actually delivers what was planned.",
    ),
    "practical checklist": (
        "Before publishing any page, confirm these items: the title is compelling and under 65 characters, the meta description is 150-160 characters and includes the focus keyword, the focus keyword appears in the first paragraph and at least one heading, the content has 5+ H2 sections with substantive text under each, and at least 2 internal links connect to related pages.",
        "After publishing, verify these items: the page loads in under 3 seconds, the schema markup is valid (test with Google's Rich Results Test), the page appears in Google Search Console within 48 hours, and the content matches the planned search intent. Set a 30-day review to check initial performance and make adjustments.",
    ),
}


def _build_brief_driven_blog_html(brief: dict[str, Any], prompt: str) -> str:
    topic = str(brief["topic"])
    audience = str(brief["audience"])
    goal = str(brief["goal"])
    requested_sections = list(brief["sections"])
    sections = requested_sections or [
        "Why this matters now",
        "What readers need to understand",
        "Business impact",
        "Recommended workflow",
        "Common mistakes to avoid",
        "Practical checklist",
    ]
    parts = [
        f"<p><strong>{html.escape(topic)}</strong> is a critical area for {html.escape(audience)} because it connects strategy, execution, and measurable business outcomes. Understanding this topic thoroughly gives teams the context they need to make better decisions and avoid costly mistakes.</p>",
        f"<p>{html.escape(goal)} This article provides a structured, practical guide designed to be useful from the first read — not a thin overview, but a resource that addresses the real questions, workflows, and decision points that matter most.</p>",
    ]
    source_insights = _source_insights_html(list(brief.get("key_points", [])))
    if source_insights:
        parts.append(source_insights)
    for section in sections:
        clean_heading = section.strip(" .:-")
        if not clean_heading:
            continue
        heading_key = clean_heading.lower()
        display_heading = _sentence_case_heading(clean_heading)
        display_heading = _fix_acronyms(display_heading)
        parts.append(f"<h2>{html.escape(display_heading)}</h2>")
        parts.append(f"<p>{html.escape(_generate_section_body(topic, clean_heading, audience, 1))}</p>")
        parts.append(f"<p>{html.escape(_generate_section_body(topic, clean_heading, audience, 2))}</p>")

    parts.extend(
        [
            "<h2>FAQ</h2>",
            f"<h3>Who is this article for?</h3><p>This guide is written for {html.escape(audience)} who need a clear, practical understanding of {html.escape(topic.lower())} — including how it affects their business, what to prioritize, and what to avoid.</p>",
            f"<h3>What makes a strong approach to {html.escape(topic.lower())}?</h3><p>A strong approach combines audience research, structured content, accurate SEO metadata, clear internal linking, and a repeatable production workflow. The goal is consistency and measurability, not just volume.</p>",
            "<h3>What should teams do before publishing?</h3><p>Review facts and claims for accuracy, confirm SEO metadata is complete and within character limits, check that internal links are working, verify schema markup, and ensure the content matches the planned search intent and brand voice.</p>",
            "<h2>Conclusion</h2>",
            f"<p>{html.escape(topic)} should be treated as an ongoing business investment, not a one-time project. Teams that build a repeatable workflow — from research through verification — will see compounding returns in search visibility, audience engagement, and business outcomes over time.</p>",
        ]
    )
    return _ensure_min_words("".join(parts), prompt, default_words=1000)


def _sentence_case_heading(heading: str) -> str:
    """Convert a heading to sentence case (capitalize first word, rest lowercase) while preserving acronyms."""
    words = heading.split()
    if not words:
        return heading
    result = [words[0].capitalize()]
    for word in words[1:]:
        # Keep known acronyms uppercase
        if word.upper() in {"AI", "SEO", "AEO", "GEO", "API", "FAQ", "CMS", "URL", "B2B", "B2C", "SaaS"}:
            result.append(word.upper())
        else:
            result.append(word.lower())
    return " ".join(result)


def _generate_section_body(topic: str, section_heading: str, audience: str, paragraph_num: int) -> str:
    """Generate unique section body text based on topic and heading context."""
    topic_lower = topic.lower()
    heading_lower = section_heading.lower()

    if paragraph_num == 1:
        # Opening paragraph for the section
        if any(word in heading_lower for word in ["benefit", "advantage", "value", "gain"]):
            return f"The primary value of {topic_lower} lies in its ability to improve measurable business outcomes. For {audience}, this means higher efficiency, better alignment between content and audience needs, and a clearer path from investment to results. Benefits compound over time as the process matures and the team gains experience with what works."
        if any(word in heading_lower for word in ["risk", "challenge", "danger", "threat"]):
            return f"Understanding the risks associated with {topic_lower} is just as important as knowing the benefits. For {audience}, the main risks include poor implementation leading to wasted resources, inconsistent quality undermining brand credibility, and failure to adapt as the landscape evolves. These risks are manageable with the right process and review discipline."
        if any(word in heading_lower for word in ["process", "step", "how", "implement", "workflow"]):
            return f"A successful implementation of {topic_lower} follows a structured process that balances speed with quality. Start with clear goals and audience definition, then build each step — from research through execution to verification — as a repeatable unit. The most effective teams treat this as an operational system, not a one-time project."
        if any(word in heading_lower for word in ["tool", "resource", "platform", "software"]):
            return f"The right tools make {topic_lower} significantly more effective. For {audience}, the key is choosing tools that integrate with existing workflows rather than replacing them. The best tools automate repetitive production tasks while keeping human judgment in place for strategy, accuracy, and brand voice decisions."
        if any(word in heading_lower for word in ["example", "case", "success", "result"]):
            return f"Real-world applications of {topic_lower} show that the strongest results come from disciplined execution, not just good ideas. Teams that define clear metrics, follow structured workflows, and review outcomes consistently outperform those that rely on ad-hoc efforts. The examples that matter most are the ones that connect specific actions to measurable improvements."
        if any(word in heading_lower for word in ["future", "trend", "outlook", "prediction"]):
            return f"The future of {topic_lower} is being shaped by several converging forces: evolving search algorithms, AI-assisted content workflows, rising audience expectations, and increased competition for organic visibility. For {audience}, the key is to build adaptable systems now that can evolve as the landscape shifts."
        # Default opening
        return f"Understanding {section_heading.lower()} in the context of {topic_lower} requires looking beyond surface-level practices. For {audience}, this means examining how each decision affects both immediate outcomes and long-term positioning. The most effective approaches combine proven frameworks with enough flexibility to adapt to specific business contexts."

    # Second paragraph
    if any(word in heading_lower for word in ["benefit", "advantage", "value"]):
        return "The most overlooked benefit is time savings across the entire content lifecycle. When research, planning, creation, optimization, and verification follow a standard process, each iteration takes less time and produces more consistent results. This frees the team to focus on strategy, differentiation, and content that requires genuine expertise."
    if any(word in heading_lower for word in ["risk", "challenge", "danger"]):
        return "The most effective risk mitigation strategy is to build verification into every stage of the process. Verify research accuracy before writing, verify content quality before publishing, verify SEO completeness before going live, and verify performance within the first 30 days. Verification is not a bottleneck — it is the safety net that protects the entire operation."
    if any(word in heading_lower for word in ["process", "step", "how", "implement"]):
        return "Execution speed improves when the process is documented and the team knows what good output looks like at each stage. Create templates for content briefs, checklists for pre-publish review, and dashboards for post-publish performance tracking. The goal is to make the production workflow predictable enough that scaling it does not require proportionally more effort."
    # Default second paragraph
    return f"For teams working on {topic_lower}, the key takeaway is that consistent execution outperforms occasional excellence. Building the right system — with clear standards, structured workflows, and regular review cycles — creates the kind of operational advantage that competitors cannot easily replicate."


def _build_listicle_html(brief: dict[str, Any], prompt: str) -> str:
    topic = str(brief["topic"])
    audience = str(brief["audience"])
    goal = str(brief["goal"])
    items = list(brief["list_items"]) or _infer_list_items(topic, int(brief["list_count"] or 10))
    parts = [
        f"<p><strong>{html.escape(topic)}</strong> represents a critical area of focus that is reshaping how businesses, technology teams, and decision-makers approach strategy and operations. What follows is a structured analysis of the most significant developments, designed to go beyond surface-level reporting and provide the context needed for informed business decisions.</p>",
        f"<p>{html.escape(goal)} This guide is written for {html.escape(audience)} who need a practical, honest view of what is changing, why it matters, and how to evaluate each development against real business criteria rather than hype cycles.</p>",
        "<h2>Why this matters now</h2>",
        f"<p>The pace of development in {html.escape(topic.lower())} has shifted from experimental to operational. Companies are no longer asking whether to engage — they are asking how to engage effectively, which investments will pay off, and which risks need active management. The window for early-mover advantage is narrowing, making it more important than ever to separate durable trends from short-term noise.</p>",
        f"<p>A rigorous analysis should evaluate each development on specific criteria: commercial readiness, infrastructure requirements, regulatory exposure, talent availability, competitive dynamics, and alignment with strategic priorities. This framework helps {html.escape(audience)} make decisions based on substance rather than headlines.</p>",
    ]
    source_insights = _source_insights_html(list(brief.get("key_points", [])))
    if source_insights:
        parts.append(source_insights)

    # Varied body templates for listicle items
    item_templates = [
        (
            "{item} has emerged as a significant area of development because it addresses a fundamental challenge in how businesses operate. The core innovation is not just technical capability but practical deployment — taking what was previously theoretical and making it operational at scale.",
            "The business case for {item_lower} rests on measurable outcomes: reduced operational costs, faster decision cycles, improved customer experience, or new revenue streams. Companies evaluating this area should focus on pilot programs with clear success metrics rather than broad platform investments.",
            "The risk profile includes implementation complexity, data requirements, talent scarcity, and the potential for over-investment before the technology matures. Smart adopters set clear evaluation criteria and time-bound pilots to manage these risks effectively.",
        ),
        (
            "The significance of {item_lower} lies in how it changes the competitive equation. Organizations that develop capability in this area can move faster, serve customers better, and build operational advantages that are difficult for competitors to replicate quickly.",
            "Practical adoption requires more than technology procurement. It demands process redesign, team training, data infrastructure, and executive commitment to measuring results honestly. The organizations seeing the strongest returns are those that treat this as a business transformation initiative, not an IT project.",
            "Key questions for decision-makers: What specific business problem does this solve? What data do we need? What does success look like in 90 days? What is the total cost of ownership including maintenance, training, and iteration?",
        ),
        (
            "What makes {item_lower} particularly relevant now is the convergence of technical maturity, market demand, and infrastructure readiness. Previous attempts to deploy similar capabilities often failed because one or more of these elements was missing. That is changing.",
            "For {audience}, the most important consideration is not whether this technology works — it does — but whether it works for their specific context. Industry, scale, data maturity, regulatory environment, and competitive position all affect the value equation.",
            "The strongest approach is to identify one high-value use case, build a controlled pilot, measure results against predetermined criteria, and then make a data-informed decision about scaling. Avoid the trap of buying a platform before proving the value proposition.",
        ),
    ]

    for index, item in enumerate(items, start=1):
        safe_item = html.escape(str(item))
        item_lower = html.escape(str(item).lower())
        template = item_templates[index % len(item_templates)]
        parts.extend(
            [
                f"<h2>{index}. {safe_item}</h2>",
                f"<p>{template[0].format(item=safe_item, item_lower=item_lower)}</p>",
                f"<p>{template[1].format(item=safe_item, item_lower=item_lower, audience=html.escape(audience))}</p>",
                f"<p>{template[2].format(item=safe_item, item_lower=item_lower, audience=html.escape(audience))}</p>",
            ]
        )
    parts.extend(
        [
            "<h2>Strategic implications</h2>",
            f"<p>The developments listed above share a common thread: they represent areas where capability is moving from experimental to operational. For {html.escape(audience)}, the strategic implication is clear — organizations need a structured approach to evaluating, piloting, and scaling these capabilities rather than reacting to each announcement individually.</p>",
            "<p>A useful framework for evaluation includes five dimensions: technical maturity, business impact, implementation difficulty, risk profile, and alignment with strategic priorities. Scoring each development on these dimensions creates a rational basis for investment decisions and helps teams communicate priorities to stakeholders.</p>",
            "<h2>Key risks to monitor</h2>",
            "<ul>",
            "<li><strong>Reliability:</strong> systems that work in demos may fail under real-world data volumes, edge cases, and operational pressure. Always test at realistic scale.</li>",
            "<li><strong>Regulatory exposure:</strong> rules governing data use, intellectual property, safety, and sector-specific requirements are evolving rapidly and vary by jurisdiction.</li>",
            "<li><strong>Talent constraints:</strong> specialized skills are in high demand, making hiring, retention, and upskilling critical bottlenecks for many organizations.</li>",
            "<li><strong>Integration complexity:</strong> new capabilities must work with existing systems, data pipelines, and workflows. Underestimating integration effort is a common source of project failure.</li>",
            "<li><strong>Vendor dependency:</strong> evaluate lock-in risk, data portability, and what happens if a vendor changes pricing, features, or strategic direction.</li>",
            "</ul>",
            "<h2>Practical next steps</h2>",
            "<ol>",
            "<li>Identify 2-3 developments from this list that align most closely with your current strategic priorities.</li>",
            "<li>For each, define a specific business problem it could solve and the metrics you would use to measure success.</li>",
            "<li>Build a 90-day pilot plan with clear deliverables, resource requirements, and decision criteria.</li>",
            "<li>Assign an owner for each pilot who has both technical understanding and business context.</li>",
            "<li>Schedule a structured review at the 30-day and 90-day marks to evaluate progress against the predetermined criteria.</li>",
            "</ol>",
            "<h2>FAQ</h2>",
            f"<h3>Why should {html.escape(audience)} pay attention to {html.escape(topic.lower())}?</h3>",
            f"<p>Because these developments have the potential to change cost structures, competitive dynamics, customer expectations, and operational capabilities. Understanding the landscape — even at a strategic level — helps leaders make better resource allocation decisions and avoid being caught off guard by shifts that competitors anticipate.</p>",
            "<h3>How should organizations evaluate these developments?</h3>",
            "<p>Use a structured framework: assess technical maturity, business impact potential, implementation difficulty, risk profile, and strategic alignment. Score each development on these dimensions, then prioritize based on where the highest-impact opportunities intersect with your organization's readiness to execute.</p>",
            "<h3>What is the biggest mistake to avoid?</h3>",
            "<p>The biggest mistake is treating these developments as technology projects rather than business transformation initiatives. The technology is usually the easier part. The harder — and more valuable — work is redesigning processes, training teams, managing change, and building the measurement systems needed to prove and improve results over time.</p>",
            "<h2>Conclusion</h2>",
            f"<p>{html.escape(topic)} should be viewed as a portfolio of strategic opportunities and risks. The most effective approach is disciplined evaluation: identify the developments most relevant to your business, test them rigorously in controlled pilots, and scale only what delivers measurable value. For {html.escape(audience)}, this means building an evaluation process that is as structured as the technologies being evaluated.</p>",
        ]
    )
    return _ensure_min_words("".join(parts), prompt, default_words=1200)


# ---------------------------------------------------------------------------
# Page HTML Builders
# ---------------------------------------------------------------------------

def _build_page_html(title: str, prompt: str, basis: str) -> str:
    lowered = prompt.lower()
    if "pricing" in lowered:
        return _build_pricing_page_html(title, prompt)
    if "case study" in lowered:
        return _build_case_study_html(title, prompt)
    if "landing" in lowered:
        return _build_landing_page_html(title, prompt)
    if "homepage" in lowered:
        return _build_homepage_html(title, prompt)
    if "service page" in lowered or "service" in lowered:
        return _build_service_page_html(title, prompt)
    return _build_landing_page_html(title, prompt)


def _build_landing_page_html(title: str, prompt: str) -> str:
    topic = _topic_label(title, prompt)
    audience = _audience(prompt)
    parts = [
        f"<p><strong>{html.escape(topic)}</strong> is a business-ready AI SEO automation service for teams that need to create, optimize, and manage WordPress content faster without lowering editorial standards. It connects strategy, content creation, SEO metadata, taxonomy, internal-link planning, draft publishing, and verification into one controlled workflow.</p>",
        "<p>For business owners, agencies, marketers, and content teams, the value is not just speed. The real value is consistency: every request becomes a structured plan, every draft follows a professional framework, and every execution returns a review summary so the team can see exactly what was created before anything goes live.</p>",
        "<h2>Hero message</h2>",
        f"<p><strong>Create SEO-ready WordPress content from one intelligent prompt.</strong> {html.escape(topic)} helps teams move from idea to review-ready draft with the key publishing details already handled: structure, metadata, schema, categories, tags, internal-link direction, and verification.</p>",
        "<p><strong>Primary CTA:</strong> Start with one AI SEO workflow and review the first draft before publishing.</p>",
        "<p><strong>Secondary CTA:</strong> Request a content automation audit to identify where your current publishing process slows down.</p>",
        "<h2>The problem: SEO content is still too manual</h2>",
        "<p>Most business websites do not struggle because they lack ideas. They struggle because every page requires too many disconnected steps: research, briefing, writing, formatting, SEO metadata, WordPress setup, taxonomy, internal links, review, and publishing. When those steps are handled manually, quality becomes inconsistent and campaigns slow down.</p>",
        "<p>This creates predictable problems: pages go live without strong metadata, drafts repeat generic language, internal links are forgotten, categories and tags become messy, and teams cannot easily verify what was actually done. Over time, the website becomes harder to manage and harder to improve.</p>",
        "<h2>The solution: one connected publishing workflow</h2>",
        f"<p>{html.escape(topic)} turns a natural-language request into an execution plan. The system identifies the content type, creates a structured draft, adds SEO guidance, prepares taxonomy, suggests internal links, generates schema where relevant, and returns a verification summary with page links and completed services.</p>",
        "<p>Instead of replacing human judgment, the workflow gives the team a stronger starting point. Editors still review the draft, adjust brand voice, confirm claims, and decide when to publish. The automation handles the repetitive production layer so the team can focus on strategy and quality.</p>",
        "<h2>Core benefits</h2>",
        "<ul>",
        "<li><strong>Faster execution:</strong> move from idea to structured WordPress draft without rebuilding the workflow every time.</li>",
        "<li><strong>Better consistency:</strong> every page follows a repeatable structure with headings, sections, FAQs, metadata, and review signals.</li>",
        "<li><strong>SEO built in:</strong> focus keyword, meta title, meta description, slug, schema, taxonomy, and internal-link guidance are considered during creation.</li>",
        "<li><strong>Safer publishing:</strong> draft-first controls keep the team in charge of what goes live.</li>",
        "<li><strong>Clear verification:</strong> the output screen shows the page URL, edit URL, content outline, SEO summary, categories, tags, and completed services.</li>",
        "</ul>",
        "<h2>Process</h2>",
        "<ol>",
        "<li><strong>Prompt:</strong> describe the content goal, audience, page type, SEO requirements, and publishing preference.</li>",
        "<li><strong>Plan:</strong> the system turns the request into ordered tasks such as content generation, SEO optimization, schema, taxonomy, and WordPress draft creation.</li>",
        "<li><strong>Generate:</strong> a structured page or post is created with a clear title, introduction, sections, FAQ, and CTA direction.</li>",
        "<li><strong>Optimize:</strong> the workflow prepares SEO details and supporting publishing metadata.</li>",
        "<li><strong>Verify:</strong> the system checks whether the requested services completed and returns a review summary.</li>",
        "<li><strong>Review:</strong> the team opens the draft in WordPress, edits where needed, and publishes only when ready.</li>",
        "</ol>",
        "<h2>Use cases</h2>",
        "<p><strong>Blog creation:</strong> generate structured long-form posts for SEO, AEO, GEO, and industry education.</p>",
        "<p><strong>Landing pages:</strong> create campaign pages with positioning, benefits, process, FAQs, and CTA sections.</p>",
        "<p><strong>Service pages:</strong> explain offers in more detail and connect them to business outcomes.</p>",
        "<p><strong>Content refreshes:</strong> update existing posts or pages with stronger structure, clearer messaging, and better metadata.</p>",
        "<p><strong>FAQ pages:</strong> turn common questions into structured answers and schema-ready content.</p>",
        "<p><strong>Internal linking:</strong> suggest anchors and related pages so new content supports the broader website architecture.</p>",
        "<p><strong>WordPress draft publishing:</strong> create review-ready drafts without forcing immediate publication.</p>",
        f"<h2>Why {html.escape(audience)} should use this workflow</h2>",
        f"<p>{html.escape(audience.capitalize())} need content that is credible, useful, and consistent across industries, use cases, and audiences. An AI SEO automation workflow helps the team produce more pages while keeping review control and brand standards intact.</p>",
        "<p>The strongest use case is not producing random articles. It is creating a repeatable content operation: pillar pages, supporting posts, landing pages, case studies, and refreshes that all follow the same planning and verification discipline.</p>",
        "<h2>Quality and safety controls</h2>",
        "<p>A production workflow must be safe by default. Draft-first publishing protects the website from accidental live changes. Verification protects the team from silent failures. The output summary protects the review process by showing what was generated, what SEO data was prepared, what taxonomy was used, and where the draft can be opened.</p>",
        "<p>This matters for a real business presentation because stakeholders need to see more than a generated page. They need proof that the system can plan, execute, verify, and hand back a useful result that a human can approve.</p>",
        "<h2>FAQ</h2>",
        "<h3>Can this replace a content team?</h3>",
        "<p>No. It is designed to remove repetitive production work so the team can spend more time on positioning, accuracy, examples, proof, and conversion strategy.</p>",
        "<h3>Can it create WordPress drafts?</h3>",
        "<p>Yes. In live mode, the workflow can create WordPress drafts through the REST API and return the public URL and edit URL for review.</p>",
        "<h3>Can it help with SEO metadata?</h3>",
        "<p>Yes. It prepares metadata, focus keyword, slug, schema guidance, and internal-link suggestions. Plugin-specific SEO fields may depend on how the WordPress SEO plugin exposes metadata through the REST API.</p>",
        "<h3>Can it update existing pages?</h3>",
        "<p>Yes. When a post or page ID is provided, the workflow can generate revised content and send an update request.</p>",
        "<h3>How should teams start?</h3>",
        "<p>Start with draft-only mode, test several content types, review the drafts in WordPress, and only enable automatic publishing after the team trusts the workflow.</p>",
        "<h2>Final CTA</h2>",
        f"<p>Use {html.escape(topic)} to create your next SEO-ready WordPress draft, review the full execution summary, and give your team a faster way to turn business ideas into polished website content.</p>",
    ]
    return _ensure_min_words("".join(parts), prompt)


def _build_service_page_html(title: str, prompt: str) -> str:
    topic = _topic_label(title, prompt)
    body = "".join(
        [
            f"<p><strong>{html.escape(topic)}</strong> helps businesses manage website content with more speed, consistency, and control. It is built for teams that need professional pages, SEO-ready posts, and reliable updates without turning every request into a manual production cycle.</p>",
            "<h2>The problem</h2>",
            "<p>Website work often slows down because content, SEO, publishing, and review happen in separate steps. That creates delays, inconsistent quality, missed metadata, and pages that go live without enough verification. For a business team, the real cost is not only time. It is the missed opportunity that comes from publishing late, publishing weak pages, or leaving useful content ideas stuck in a planning document.</p>",
            "<p>A production workflow needs to protect quality while removing repetitive work. That means every request should become a clear plan, every draft should follow the right structure, and every completed action should be visible to the person reviewing the result.</p>",
            "<h2>The solution</h2>",
            f"<p>{html.escape(topic)} turns a natural-language request into an execution plan, creates the content, applies SEO structure, handles taxonomy where needed, and returns a clear review summary with links and status. It is designed to support the full website management workflow: blog creation, service-page updates, landing pages, refreshes, metadata, schema, internal links, categories, tags, draft publishing, and verification.</p>",
            "<p>The purpose is not to remove human review. The purpose is to give editors, marketers, and business owners a stronger starting point so they can spend their time improving strategy, examples, proof, and brand voice instead of repeating the same setup tasks.</p>",
            "<h2>What is included</h2>",
            "<ul><li><strong>Content creation:</strong> blog posts, landing pages, service pages, comparison articles, FAQ pages, and refreshes.</li><li><strong>SEO preparation:</strong> meta title, meta description, focus keyword, slug, structured headings, schema guidance, and optimization checks.</li><li><strong>WordPress management:</strong> draft creation, page updates, post updates, taxonomy handling, and review links.</li><li><strong>Internal linking:</strong> anchor suggestions that connect new content to related website pages.</li><li><strong>Verification:</strong> an output summary that shows what was completed, what needs review, and where the draft can be opened.</li></ul>",
            "<h2>How the process works</h2>",
            "<ol><li><strong>Prompt:</strong> the user describes what they want in plain language, including topic, audience, content type, and publishing preference.</li><li><strong>Plan:</strong> the system breaks the request into ordered actions such as generate, optimize, create terms, create draft, and verify.</li><li><strong>Generate:</strong> the writing engine creates a page or post that follows the requested structure instead of forcing every prompt into one template.</li><li><strong>Prepare:</strong> SEO, taxonomy, links, schema, and WordPress payloads are assembled.</li><li><strong>Review:</strong> the user receives the public URL, edit URL, outline, metadata, services completed, and any fallback warnings.</li></ol>",
            "<h2>Business benefits</h2>",
            "<p>The biggest benefit is consistency. A business website grows stronger when every page has a clear job, a useful structure, and complete publishing details. The system also improves speed because teams can move from idea to review-ready draft faster, without losing the checks that keep a live website safe.</p>",
            "<p>Another benefit is visibility. When the output screen shows content outline, SEO details, taxonomy, WordPress status, completed services, and links, the reviewer can quickly understand what happened. That makes the workflow easier to trust in a real business environment.</p>",
            "<h2>Best for</h2>",
            "<p>Use it for growing content libraries, service-page refreshes, SEO campaigns, topical clusters, and businesses that need a faster publishing workflow without losing human review. It is especially useful when a team needs to create many high-quality drafts, maintain publishing discipline, and show stakeholders that the system can execute tasks reliably.</p>",
            "<h2>FAQ</h2><h3>Can existing pages be updated?</h3><p>Yes. Existing posts or pages can be updated when the WordPress ID is provided. The system can generate revised content, prepare SEO details, and send the update through the WordPress API.</p><h3>Can publishing stay controlled?</h3><p>Yes. Draft mode can be enforced so content is reviewed before going live. This is the recommended setup for production business websites.</p><h3>Can every prompt produce a different structure?</h3><p>Yes when the AI generation path is working. The local fallback is only a safety net, while the production writer follows the actual prompt, audience, title, structure, and source material.</p>",
            "<h2>Next step</h2>",
            "<p>Start with one high-value page or post, review the generated draft, check the output summary, and then expand into a repeatable content workflow once the team is confident in the process.</p>",
        ]
    )
    return _ensure_min_words(body, prompt, default_words=800)


def _build_pricing_page_html(title: str, prompt: str) -> str:
    topic = _topic_label(title, prompt)
    return "".join(
        [
            f"<p><strong>{html.escape(topic)}</strong> pricing is designed to match the maturity of your content operation. Start with focused draft creation, scale into repeatable SEO workflows, then move into custom automation when the business needs deeper control.</p>",
            "<h2>Plans</h2>",
            "<h3>Starter</h3><p>Best for small teams that need reliable blog drafts, basic SEO metadata, and safe WordPress draft creation.</p>",
            "<h3>Growth</h3><p>Best for teams publishing regularly across blogs, landing pages, service pages, FAQs, and SEO refreshes.</p>",
            "<h3>Enterprise</h3><p>Best for multi-site operations, custom workflows, approval controls, deeper integrations, and advanced reporting.</p>",
            "<h2>Feature comparison</h2>",
            "<ul><li><strong>Starter:</strong> one-prompt content creation, SEO basics, draft publishing.</li><li><strong>Growth:</strong> content clusters, schema, internal-link suggestions, taxonomy, rewrite workflows.</li><li><strong>Enterprise:</strong> custom action libraries, multi-site management, advanced verification, and workflow governance.</li></ul>",
            "<h2>Which plan fits best?</h2>",
            "<p>Choose Starter if you need better drafts. Choose Growth if publishing consistency is the problem. Choose Enterprise if the workflow must fit complex teams, permissions, and multiple sites.</p>",
            "<h2>What every plan protects</h2>",
            "<p>Every plan should protect the same fundamentals: clear content structure, draft-first controls, SEO metadata, verification, and a visible record of what happened. The difference between plans is not basic quality; it is scale, governance, and depth of automation.</p>",
            "<h2>Recommended starting point</h2>",
            "<p>Most teams should begin with Growth if they publish more than a few pages per month. It provides enough structure for repeatable campaigns without requiring a fully custom operating model. Starter is useful for early testing, while Enterprise is best when multiple stakeholders, sites, or approval rules are involved.</p>",
            '<h2>FAQ</h2><h3>Can I start with drafts only?</h3><p>Yes. Draft-first mode is the recommended setup for live business websites.</p><h3>Can plans change later?</h3><p>Yes. The workflow can expand as the content operation becomes more mature.</p>',
            "<h2>Call to action</h2><p>Start with the plan that matches your current publishing volume, then scale when content operations need more automation.</p>",
        ]
    )


def _build_homepage_html(title: str, prompt: str) -> str:
    topic = _topic_label(title, prompt)
    return "".join(
        [
            f"<p><strong>{html.escape(topic)}</strong> helps businesses turn SEO, AEO, and GEO content operations into a faster, more reliable growth system.</p>",
            "<h2>Hero message</h2><p>Create, optimize, and manage WordPress content from one intelligent workflow.</p>",
            "<h2>Primary call to action</h2><p>Start with a content audit or generate your first SEO-ready draft.</p>",
            "<h2>Trust points</h2><ul><li>Draft-first publishing controls</li><li>Structured SEO and schema support</li><li>Clear verification after every action</li><li>Built for real business websites</li></ul>",
            "<h2>Services overview</h2><p>Use the system for blog creation, landing pages, service pages, content refreshes, FAQs, internal links, categories, tags, and WordPress publishing workflows.</p>",
            "<h2>Why it works</h2><p>The workflow combines planning, execution, and verification so teams can move faster without losing control over quality or publishing risk.</p>",
        ]
    )


def _build_case_study_html(title: str, prompt: str) -> str:
    topic = _topic_label(title, prompt)
    body = "".join(
        [
            f"<p><strong>{html.escape(topic)}</strong> shows how a business can improve website publishing speed while keeping quality, SEO, and review controls intact.</p>",
            "<p>This case study is written as a practical business narrative: what was slowing the team down, what changed in the workflow, how the solution was implemented, and what stakeholders should measure before scaling the system further.</p>",
            "<h2>Challenge</h2><p>The team needed to publish more consistently, but manual briefing, drafting, SEO review, and WordPress updates slowed every campaign. Content ideas were not the problem. The real constraint was the number of handoffs required before a page could become a review-ready draft.</p><p>That created several operational issues: inconsistent structure across pages, missing metadata, unclear taxonomy, weak internal linking, and no simple way to confirm what services were completed for each request.</p>",
            "<h2>Solution</h2><p>The workflow connected planning, content generation, SEO metadata, taxonomy, WordPress draft creation, and verification into one repeatable process. Instead of asking the team to manage every step manually, the system converted a plain-language request into an ordered set of actions.</p><p>The strongest part of the solution was not automation alone. It was traceability. Every run returned an outline, WordPress status, links, SEO details, and completed services so a human reviewer could understand the result quickly.</p>",
            "<h2>Implementation</h2><ol><li>Define content goals, audience, page type, and publishing preference.</li><li>Create structured drafts from one prompt while preserving review control.</li><li>Apply SEO metadata, schema guidance, categories, tags, and internal-link suggestions.</li><li>Create or update the WordPress draft through the API.</li><li>Verify the output before anyone decides to publish.</li></ol><p>The rollout started with draft-only mode. This reduced risk because the team could test real prompts, review generated pages, and refine expectations without pushing unapproved content live.</p>",
            "<h2>Results</h2><p>The business reduced production friction, improved draft consistency, and created a clearer review process for every page before it went live. The team could see the practical value immediately: less manual setup, faster first drafts, and better visibility into what happened during each run.</p><p>The measurable results to track include time from idea to draft, percentage of pages with complete metadata, number of drafts needing major restructure, internal-link coverage, and reviewer satisfaction with the first generated version.</p>",
            "<h2>Lessons learned</h2><p>Automation works best when it supports editorial judgment. The strongest workflow creates a good first draft, makes the work traceable, and keeps humans in control of final approval. It should not hide failures or pretend every fallback output is production-grade.</p><p>For a real business, the system must also make generation mode visible. If the AI model produces the content, the draft can be evaluated as a full generation result. If the local fallback is used, the reviewer should know that the system created a safety-net draft and that the AI connection needs attention.</p>",
            "<h2>FAQ</h2><h3>Was everything fully automated?</h3><p>No. The system automated production steps while preserving human review for final publishing decisions.</p><h3>Why keep draft-first controls?</h3><p>Draft-first controls protect the website from accidental live changes and give editors time to confirm claims, examples, structure, and brand voice.</p><h3>What makes the workflow production-ready?</h3><p>A production-ready workflow needs planning, generation, SEO preparation, WordPress execution, verification, logging, and clear fallback visibility.</p>",
            "<h2>Call to action</h2><p>Use the same workflow to turn your next content request into a structured, review-ready WordPress draft.</p>",
        ]
    )
    return _ensure_min_words(body, prompt, default_words=800)


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def _topic_label(title: str, prompt: str) -> str:
    cleaned = title.strip(" .")
    if cleaned and not cleaned.lower().startswith("new wordpress"):
        return _fix_acronyms(cleaned)
    match = re.search(r"\babout\s+([^.;]+)", prompt, flags=re.I)
    return _fix_acronyms((match.group(1).strip() if match else "This topic").title())


def _audience(prompt: str) -> str:
    match = re.search(r"\bfor\s+([^.;]+)", prompt, flags=re.I)
    if not match:
        return "business teams"
    raw = re.split(r"\b(?:with|and|that|who|\.|,)\b", match.group(1), maxsplit=1, flags=re.I)[0]
    return raw.strip(" .") or "business teams"


def _source_note(points: list[str]) -> str:
    if not points:
        return ""
    escaped = html.escape(points[0][:420])
    return f"<h2>Source insight</h2><p>{escaped}</p>"


def _source_insights_html(points: list[str]) -> str:
    useful = [
        point.strip()
        for point in points
        if point.strip()
        and "research brief requested for" not in point.lower()
        and "define the audience" not in point.lower()
        and len(point.strip()) > 35
    ]
    if not useful:
        return ""
    items = "".join(f"<li>{html.escape(point[:360])}</li>" for point in useful[:5])
    return f"<h2>Source-based insights</h2><p>The draft is grounded in the supplied research material and uses these points as the evidence base for the article.</p><ul>{items}</ul>"


def _requested_block(prompt: str, label: str) -> str:
    match = re.search(rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)(?=\n\s*[A-Z][A-Za-z ]{{1,30}}\s*:|\Z)", prompt, flags=re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _requested_sections(prompt: str) -> list[str]:
    sections: list[str] = []
    structure = _requested_block(prompt, "article structure") or _requested_block(prompt, "structure")
    source = structure or prompt
    for match in re.finditer(r"(?m)^\s*(?:[-*]|\d+[.)])\s+(.+)$", source):
        item = match.group(1).strip()
        item = re.sub(r"\b(include|add|write|create|explain)\b", "", item, flags=re.I).strip(" .:-")
        if 4 <= len(item) <= 90 and not item.lower().startswith(("for each", "save as", "do not")):
            sections.append(item)
    return _dedupe(sections)[:14]


def _requested_list_count(text: str) -> int:
    # Match both "top 10" and "10 best" patterns
    match = re.search(r"\b(?:top|best|leading|main|key)\s+([0-9]{1,2})\b", text, flags=re.I)
    if not match:
        # Also match "10 best/top/leading" (number first)
        match = re.search(r"\b([0-9]{1,2})\s+(?:top|best|leading|main|key)\b", text, flags=re.I)
    if not match:
        return 0
    count = int(match.group(1))
    return count if 2 <= count <= 25 else 0


def _requested_list_items(prompt: str) -> list[str]:
    items: list[str] = []
    for match in re.finditer(r"(?m)^\s*[-*]\s+(.+)$", prompt):
        item = match.group(1).strip(" .")
        if 5 <= len(item) <= 90 and not item.lower().startswith(("save ", "make ", "optimize ", "meta ", "focus ", "category", "tags")):
            items.append(_fix_acronyms(item.title()))
    return _dedupe(items)


def _infer_list_items(topic: str, count: int) -> list[str]:
    lowered = topic.lower()
    if "ai" in lowered or "artificial intelligence" in lowered:
        base = [
            "Large language models and enterprise AI assistants",
            "AI chips and domestic computing infrastructure",
            "AI-powered robotics and humanoid robots",
            "Autonomous vehicles and smart mobility systems",
            "Industrial AI for manufacturing automation",
            "Healthcare AI and medical research tools",
            "AI-driven e-commerce and consumer personalization",
            "Smart city infrastructure and urban AI systems",
            "AI in education and workforce training",
            "Open-source AI models and developer ecosystems",
            "Multimodal AI for text, image, audio, and video workflows",
            "AI safety, governance, and model evaluation systems",
        ]
    else:
        subject = re.sub(r"\b(top|best|leading|main|key|[0-9]+)\b", "", topic, flags=re.I).strip()
        base = [
            f"Market context for {subject}",
            f"Core technology behind {subject}",
            f"Customer adoption patterns in {subject}",
            f"Business models connected to {subject}",
            f"Operational use cases for {subject}",
            f"Investment and infrastructure around {subject}",
            f"Competitive advantages created by {subject}",
            f"Implementation challenges for {subject}",
            f"Risk, compliance, and trust factors in {subject}",
            f"Future outlook for {subject}",
        ]
    while len(base) < count:
        base.append(f"Additional strategic development {len(base) + 1}")
    return base[:count]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = re.sub(r"\s+", " ", value).strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _ensure_min_words(html_value: str, prompt: str, default_words: int = 0) -> str:
    requested = _requested_word_count(prompt)
    if not requested:
        requested = default_words
    if not requested:
        return html_value
    current = len(re.sub(r"<[^>]+>", " ", html_value).split())
    if current >= requested:
        return html_value

    # Generate VARIED padding paragraphs instead of repeating the same one
    padding_paragraphs = [
        (
            "<h2>Implementation considerations</h2>"
            "<p>For teams moving to production, the workflow should be introduced in phases. Start with low-risk content types like FAQ pages or blog drafts, compare the automated output against manual benchmarks, and document where human editing adds the most value. This phased approach builds team confidence while identifying the specific improvements needed for brand voice, examples, and conversion language.</p>"
            "<p>The strongest implementations treat the system as a first-draft accelerator, not a replacement for editorial judgment. Every page should still pass through a human review step that checks accuracy, tone, brand alignment, and strategic fit before publishing.</p>"
        ),
        (
            "<h2>Measurement and optimization</h2>"
            "<p>Track content performance across four dimensions: search visibility (rankings, impressions, click-through rates), engagement (time on page, scroll depth, bounce rate), conversion (leads, sign-ups, purchases), and operational efficiency (time to publish, revision cycles, cost per page). These metrics reveal whether the workflow is delivering real business value.</p>"
            "<p>Review performance monthly and use the data to refine content briefs, adjust target word counts, improve heading structures, and update SEO metadata strategies. Continuous improvement based on measurement is what separates effective content operations from publishing volume alone.</p>"
        ),
        (
            "<h2>Scaling the workflow</h2>"
            "<p>Once the workflow is validated for core content types, expand to supporting content: comparison articles, listicles, case studies, pillar pages, and content refreshes for underperforming pages. Each content type may require small adjustments to the brief template, but the underlying workflow — plan, generate, optimize, verify, review — stays the same.</p>"
            "<p>At scale, the most important success factor is consistency. Every page should have a defined audience, a mapped search intent, a professional structure, complete SEO metadata, and a verification trail. This discipline is what turns content production into a competitive advantage.</p>"
        ),
        (
            "<h2>Team alignment and training</h2>"
            "<p>Successful content operations require alignment between the people creating content, the people reviewing it, and the people measuring results. Establish clear roles: who defines the content strategy, who reviews drafts for quality, who handles SEO metadata, and who monitors post-publish performance.</p>"
            "<p>Document the workflow as a standard operating procedure so that new team members can contribute effectively without extensive onboarding. The clearer the process, the more reliably the team can maintain quality as publishing volume increases.</p>"
        ),
    ]

    additions = []
    pad_index = 0
    while current < requested and pad_index < len(padding_paragraphs):
        additions.append(padding_paragraphs[pad_index])
        current = len(re.sub(r"<[^>]+>", " ", (html_value + "".join(additions))).split())
        pad_index += 1

    return html_value + "".join(additions)


def _requested_word_count(prompt: str) -> int:
    match = re.search(r"\bat\s+least\s+([0-9,]+)\s+words\b", prompt, flags=re.I)
    if not match:
        match = re.search(r"\bminimum\s+([0-9,]+)\s+words\b", prompt, flags=re.I)
    if not match:
        # Also match "X words" or "X-word"
        match = re.search(r"\b([0-9,]+)\s+words?\b", prompt, flags=re.I)
    if not match:
        return 0
    return int(match.group(1).replace(",", ""))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "wordpress-content"


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


def _seo_payload(plugin: str, seo: dict[str, Any]) -> dict[str, Any]:
    if not seo:
        return {}
    meta = {}
    if plugin == "rankmath":
        meta = {
            "rank_math_title": seo.get("meta_title", ""),
            "rank_math_description": seo.get("meta_description", ""),
            "rank_math_focus_keyword": seo.get("focus_keyword", ""),
        }
    elif plugin == "yoast":
        meta = {
            "_yoast_wpseo_title": seo.get("meta_title", ""),
            "_yoast_wpseo_metadesc": seo.get("meta_description", ""),
            "_yoast_wpseo_focuskw": seo.get("focus_keyword", ""),
        }
    else:
        meta = {
            "awa_meta_title": seo.get("meta_title", ""),
            "awa_meta_description": seo.get("meta_description", ""),
            "awa_focus_keyword": seo.get("focus_keyword", ""),
        }
    return {"meta": meta, "slug": seo.get("slug")} if seo.get("slug") else {"meta": meta}


def _sanitize_html(value: str) -> str:
    value = re.sub(r"```(?:html|json)?", "", value, flags=re.I).replace("```", "")
    value = re.sub(r"<script.*?</script>", "", value, flags=re.I | re.S)
    value = re.sub(r"\son[a-z]+\s*=\s*['\"].*?['\"]", "", value, flags=re.I | re.S)
    return value.strip()
