const form = document.querySelector("#agentForm");
const runButton = document.querySelector("#runButton");
const health = document.querySelector("#health");
const runId = document.querySelector("#runId");
const finalResponse = document.querySelector("#finalResponse");
const summaryCards = document.querySelector("#summaryCards");
const actionLinks = document.querySelector("#actionLinks");
const contentMeta = document.querySelector("#contentMeta");
const contentOutline = document.querySelector("#contentOutline");
const seoState = document.querySelector("#seoState");
const seoSummary = document.querySelector("#seoSummary");
const timelineState = document.querySelector("#timelineState");
const timeline = document.querySelector("#timeline");
const planOutput = document.querySelector("#planOutput");
const verifyOutput = document.querySelector("#verifyOutput");
const planCount = document.querySelector("#planCount");
const verifyState = document.querySelector("#verifyState");
const runs = document.querySelector("#runs");
const refreshRuns = document.querySelector("#refreshRuns");
const apiToken = document.querySelector("#apiToken");
const stepTemplate = document.querySelector("#stepTemplate");

apiToken.value = localStorage.getItem("awa_api_token") || "";
apiToken.addEventListener("change", () => {
  localStorage.setItem("awa_api_token", apiToken.value.trim());
});

async function request(path, options = {}) {
  const token = localStorage.getItem("awa_api_token") || "";
  const headers = { "content-type": "application/json", ...(options.headers || {}) };
  if (token) headers.authorization = `Bearer ${token}`;
  const response = await fetch(path, {
    headers,
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function setLoading(isLoading) {
  runButton.disabled = isLoading;
  runButton.querySelector("span").textContent = isLoading ? "Running" : "Run agent";
}

function renderResult(payload) {
  runId.textContent = payload.run_id ? `Run #${payload.run_id}` : "Unsaved run";
  finalResponse.textContent = payload.final_response || payload.error || "Execution finished.";
  renderOutputSummary(payload.output_summary || buildSummaryFromResults(payload));
  planOutput.textContent = pretty(payload.plan || {});
  verifyOutput.textContent = pretty(payload.verification || {});
  verifyState.textContent = payload.ok ? "Passed" : "Review";

  const actions = payload.plan?.actions || [];
  planCount.textContent = `${actions.length} ${actions.length === 1 ? "step" : "steps"}`;
  timeline.innerHTML = "";

  const results = payload.results || [];
  timelineState.textContent = results.length ? `${results.length} actions` : "Idle";
  results.forEach((result, index) => {
    const node = stepTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.ok = String(result.ok);
    node.querySelector(".step-index").textContent = String(index + 1).padStart(2, "0");
    node.querySelector("strong").textContent = result.action;
    node.querySelector("p").textContent = result.ok
      ? summarizeResult(result)
      : result.error || "Step failed.";
    timeline.appendChild(node);
  });
}

function renderOutputSummary(summary) {
  const status = summary.status || "review";
  const publicUrl = summary.public_url || "";
  const editUrl = summary.edit_url || "";
  const wordCount = summary.word_count_estimate || 0;
  const wpStatus = summary.wordpress_status || (summary.dry_run ? "dry run" : "unknown");
  const generationMode = summary.generation_mode || "unknown";
  const fallbackReason = summary.ai_fallback_reason || "";

  summaryCards.innerHTML = [
    metricCard("Status", status),
    metricCard("Deliverable", summary.deliverable || "content"),
    metricCard("WordPress", wpStatus),
    metricCard("Words", wordCount ? `${wordCount}` : "n/a"),
    metricCard("Generation", generationMode === "ai" ? "AI" : generationMode === "local_fallback" ? "Fallback" : generationMode),
  ].join("");

  actionLinks.innerHTML = "";
  if (publicUrl) actionLinks.appendChild(linkButton(publicUrl, "Open page"));
  if (editUrl) actionLinks.appendChild(linkButton(editUrl, "Edit in WordPress"));
  if (!publicUrl && !editUrl) {
    actionLinks.innerHTML = `<span class="muted">Links appear after WordPress creates the draft or page.</span>`;
  }

  contentMeta.textContent = summary.title ? `${summary.title}${wordCount ? ` · ${wordCount} words` : ""}` : "No content";
  const outline = summary.content_outline || [];
  contentOutline.className = outline.length ? "outline" : "outline empty";
  contentOutline.innerHTML = outline.length
    ? `<ol>${outline.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol>`
    : "No outline was returned.";

  const seo = summary.seo || {};
  const research = summary.research || {};
  const categories = summary.categories || [];
  const tags = summary.tags || [];
  const sources = research.sources || [];
  seoState.textContent = seo.meta_title ? "Applied" : "Review";
  seoSummary.className = "seo-summary";
  seoSummary.innerHTML = `
    <div><span>Meta title</span><strong>${escapeHtml(seo.meta_title || "Not applied")}</strong></div>
    <div><span>Meta description</span><p>${escapeHtml(seo.meta_description || "Not applied")}</p></div>
    <div><span>Focus keyword</span><strong>${escapeHtml(seo.focus_keyword || "Not applied")}</strong></div>
    <div><span>Categories</span><p>${categories.length ? categories.map(escapeHtml).join(", ") : "None"}</p></div>
    <div><span>Tags</span><p>${tags.length ? tags.map(escapeHtml).join(", ") : "None"}</p></div>
    <div><span>Generation mode</span><p>${escapeHtml(generationMode === "ai" ? "AI model" : generationMode === "local_fallback" ? "Local fallback" : generationMode)}</p></div>
    <div><span>Research mode</span><p>${escapeHtml(research.mode || "Not used")}</p></div>
    ${research.summary ? `<div><span>Research summary</span><p>${escapeHtml(research.summary.slice(0, 320))}</p></div>` : ""}
    ${sources.length ? `<div><span>Sources reviewed</span><p>${sources.map((source) => escapeHtml(source.title || source.url || "Source")).join(", ")}</p></div>` : ""}
    ${fallbackReason ? `<div><span>Fallback reason</span><p>${escapeHtml(fallbackReason.slice(0, 260))}</p></div>` : ""}
    <div><span>Completed services</span><p>${(summary.services_completed || []).map(formatAction).join(", ")}</p></div>
  `;
}

function metricCard(label, value) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`;
}

function linkButton(url, label) {
  const anchor = document.createElement("a");
  anchor.className = "link-button";
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noreferrer";
  anchor.textContent = label;
  return anchor;
}

function buildSummaryFromResults(payload) {
  const results = payload.results || [];
  const created = [...results].reverse().find((result) => ["create_post", "create_page"].includes(result.action) && result.ok);
  const generated = results.find((result) => result.action === "generate_content" && result.ok);
  const seo = results.find((result) => result.action === "seo_optimize" && result.ok);
  const html = generated?.data?.html || "";
  const outline = [...html.matchAll(/<h[2-3][^>]*>(.*?)<\/h[2-3]>/gi)]
    .map((match) => match[1].replace(/<[^>]+>/g, ""))
    .slice(0, 12);
  return {
    status: payload.ok ? "passed" : "review",
    deliverable: created?.data?.kind || "content",
    title: generated?.data?.title || created?.data?.title || "",
    word_count_estimate: generated?.data?.word_count_estimate,
    public_url: created?.data?.link || "",
    edit_url: created?.data?.edit_link || "",
    wordpress_status: created?.data?.status || "",
    generation_mode: generated?.data?.generation_mode || "",
    ai_fallback_reason: generated?.data?.ai_fallback_reason || "",
    dry_run: Boolean(created?.dry_run),
    services_completed: results.filter((result) => result.ok).map((result) => result.action),
    content_outline: outline,
    seo: seo?.data || {},
    research: (() => {
      const researchResult = results.find((result) => result.action === "research_topic" && result.ok);
      return researchResult ? {
        mode: researchResult.data?.source_mode || "",
        summary: researchResult.data?.summary || "",
        sources: researchResult.data?.sources || [],
      } : {};
    })(),
    categories: results.filter((result) => result.action === "create_category" && result.ok).map((result) => result.data?.name).filter(Boolean),
    tags: results.filter((result) => result.action === "create_tag" && result.ok).map((result) => result.data?.name).filter(Boolean),
  };
}

function summarizeResult(result) {
  if (result.data?.message) return result.data.message;
  if (result.data?.link) return result.data.link;
  if (result.data?.summary) return result.data.summary;
  if (result.data?.meta_title) return result.data.meta_title;
  if (result.data?.title) return result.data.title;
  return "Completed.";
}

function formatAction(value) {
  return String(value).replace(/_/g, " ");
}

async function loadHealth() {
  try {
    const payload = await request("/api/health");
    const mode = payload.dry_run ? "dry run" : "live WordPress";
    const ai = payload.anthropic_ready ? "AI planner" : "local planner";
    health.textContent = `System online · ${mode} · ${ai}`;
  } catch (error) {
    health.textContent = "System offline";
  }
}

async function loadRuns() {
  try {
    const payload = await request("/api/runs");
    runs.innerHTML = "";
    if (!payload.runs.length) {
      runs.innerHTML = "<p>No runs recorded yet.</p>";
      return;
    }
    payload.runs.slice(0, 8).forEach((run) => {
      const item = document.createElement("div");
      item.className = "run";
      const status = run.ok ? "passed" : "review";
      item.innerHTML = `<strong>#${run.id} · ${status}</strong><p>${escapeHtml(run.prompt).slice(0, 150)}</p>`;
      item.addEventListener("click", () => {
        renderResult({
          run_id: run.id,
          ok: run.ok,
          final_response: run.result.final_response,
          plan: run.plan,
          results: run.result.results,
          verification: run.result.verification,
          output_summary: run.result.output_summary,
        });
      });
      runs.appendChild(item);
    });
  } catch (error) {
    runs.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  }
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setLoading(true);
  finalResponse.textContent = "Planning, executing, and verifying...";
  summaryCards.innerHTML = "";
  actionLinks.innerHTML = "";
  contentOutline.className = "outline empty";
  contentOutline.textContent = "Building content preview...";
  seoSummary.className = "seo-summary empty";
  seoSummary.textContent = "Preparing SEO verification...";
  timeline.innerHTML = "";
  try {
    const payload = await request("/api/run", {
      method: "POST",
      body: JSON.stringify({
        prompt: form.prompt.value,
        source_material: form.source.value,
      }),
    });
    renderResult(payload);
    await loadRuns();
  } catch (error) {
    finalResponse.textContent = error.message;
    verifyState.textContent = "Error";
  } finally {
    setLoading(false);
  }
});

refreshRuns.addEventListener("click", loadRuns);

loadHealth();
loadRuns();
