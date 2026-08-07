(() => {
  "use strict";

  const STAGES = [
    { stage: "crawl", label: "サイトを読み込み中" },
    { stage: "keywords", label: "対策キーワードを推定中" },
    { stage: "serp", label: "検索順位とAI Overviewを取得中" },
    { stage: "psi", label: "表示速度を計測中" },
    { stage: "competitors", label: "競合サイトを分析中" },
    { stage: "backlinks", label: "被リンクを調査中" },
    { stage: "score", label: "スコア算出・要約生成中" },
  ];

  const viewInput = document.getElementById("view-input");
  const viewProgress = document.getElementById("view-progress");
  const viewResult = document.getElementById("view-result");

  const form = document.getElementById("audit-form");
  const urlInput = document.getElementById("url-input");
  const urlError = document.getElementById("url-error");
  const submitBtn = document.getElementById("submit-btn");
  const progressUrlLabel = document.getElementById("progress-url-label");
  const progressRail = document.getElementById("progress-rail");

  let currentKeywords = [];
  let lastUrl = "";
  let lastDevices = ["desktop", "mobile"];

  function showView(view) {
    viewInput.classList.add("hidden");
    viewProgress.classList.add("hidden");
    viewResult.classList.add("hidden");
    view.classList.remove("hidden");
  }

  function normalizeUrl(raw) {
    const value = raw.trim();
    if (!value) return null;
    if (/^https?:\/\//i.test(value)) return value;
    return `https://${value}`;
  }

  function validateUrl(value) {
    try {
      const parsed = new URL(value);
      return parsed.hostname.includes(".");
    } catch (e) {
      return false;
    }
  }

  function selectedDevices() {
    const value = form.querySelector('input[name="device"]:checked').value;
    if (value === "both") return ["desktop", "mobile"];
    return [value];
  }

  function renderProgressRail() {
    progressRail.innerHTML = "";
    for (const { stage, label } of STAGES) {
      const li = document.createElement("li");
      li.className = "pending";
      li.dataset.stage = stage;
      li.innerHTML = `<span class="icon"></span><span class="label">${label}</span><span class="evidence"></span>`;
      progressRail.appendChild(li);
    }
  }

  function updateProgressRail(event) {
    const li = progressRail.querySelector(`li[data-stage="${event.stage}"]`);
    if (!li) return;

    if (event.done) {
      li.className = "done";
      const evidence = describeEvidence(event);
      li.querySelector(".evidence").textContent = evidence;
    } else {
      li.className = "active";
    }
  }

  function describeEvidence(event) {
    if (Array.isArray(event.keywords)) return `${event.keywords.length}件を推定`;
    if (Array.isArray(event.competitors)) return `${event.competitors.length}社を検出`;
    return "";
  }

  function markStageWarning(stage) {
    const li = progressRail.querySelector(`li[data-stage="${stage}"]`);
    if (li && li.className !== "done") {
      li.className = "warning";
    }
  }

  async function startAudit(url, devices, keywords) {
    lastUrl = url;
    lastDevices = devices;

    const payload = { url, devices };
    if (keywords) payload.keywords = keywords;

    const resp = await fetch("/api/audits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || "診断を開始できませんでした");
    }

    const { job_id: jobId } = await resp.json();
    return jobId;
  }

  function subscribeToJob(jobId) {
    progressUrlLabel.textContent = lastUrl;
    renderProgressRail();
    showView(viewProgress);

    const source = new EventSource(`/api/audits/${jobId}/events`);

    source.onmessage = (message) => {
      const event = JSON.parse(message.data);

      if (event.type === "progress") {
        updateProgressRail(event);
      } else if (event.type === "warning") {
        markStageWarning(event.stage);
      } else if (event.type === "done") {
        source.close();
        renderResult(event.result);
      } else if (event.type === "error") {
        source.close();
        showFatalError(event.message);
      }
    };

    source.onerror = () => {
      // ネットワーク瞬断時、ブラウザは自動再接続を試みる。
      // ジョブ側が既に完了/エラーで終了している場合は EventSource 側で接続が閉じられる。
    };
  }

  function showFatalError(message) {
    showView(viewInput);
    urlError.textContent = message;
    submitBtn.disabled = false;
  }

  function bandLabel(band) {
    return { good: "良好", fair: "標準", weak: "要改善", poor: "急ぎ改善" }[band] || band;
  }

  function renderResult(result) {
    currentKeywords = [...(result.keywords || [])];

    document.getElementById("result-domain").textContent = result.domain;
    document.getElementById("result-date").textContent = new Date().toLocaleDateString("ja-JP");

    renderWarnings(result.warnings || []);
    renderTotalScore(result.score);
    renderCategoryScores(result.score.categories);
    renderImprovements(result.score.improvements);
    renderSalesSummary(result.sales_summary);
    renderKeywordEditor();
    renderSerpTable(result);
    renderCompetitors(result);

    showView(viewResult);
  }

  function renderWarnings(warnings) {
    const wrap = document.getElementById("warning-banner-wrap");
    if (!warnings.length) {
      wrap.innerHTML = "";
      return;
    }
    const items = warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("");
    wrap.innerHTML = `<div class="warning-banner"><strong>一部の項目が取得できませんでした</strong><ul>${items}</ul></div>`;
  }

  function renderTotalScore(score) {
    document.getElementById("total-score-value").textContent = score.total_score;
    document.getElementById("score-fill").style.width = `${score.total_score}%`;
    document.getElementById("score-marker").style.left = `calc(${score.total_score}% - 1.5px)`;
    const badge = document.getElementById("band-badge");
    badge.textContent = bandLabel(score.band);
    badge.className = `band-badge ${score.band}`;
  }

  function renderCategoryScores(categories) {
    const container = document.getElementById("category-scores");
    container.innerHTML = "";

    categories.forEach((cat, index) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "category-row";
      const pct = cat.measured ? Math.round((cat.score / cat.weight) * 100) : 0;
      row.innerHTML = `
        <span class="name">${escapeHtml(cat.label)}</span>
        <span class="bar-track ${cat.measured ? "" : "unmeasured"}"><span class="fill" style="width:${pct}%"></span></span>
        <span class="score tabular-nums">${cat.measured ? `${cat.score.toFixed(1)}/${cat.weight}` : "未計測"}</span>
        <span class="toggle">▾</span>
      `;

      const detail = document.createElement("div");
      detail.className = "category-detail hidden";
      detail.innerHTML = cat.items
        .map(
          (item) =>
            `<div class="item-row"><span class="item-name">${escapeHtml(item.label)}</span><span>${escapeHtml(item.evidence)} ・ ${item.score.toFixed(1)}/${item.max_score}</span></div>`
        )
        .join("");

      row.addEventListener("click", () => detail.classList.toggle("hidden"));

      container.appendChild(row);
      container.appendChild(detail);
    });
  }

  function renderImprovements(improvements) {
    const list = document.getElementById("improvement-list");
    if (!improvements.length) {
      list.innerHTML = "<li>改善優先度の高い項目はありませんでした。</li>";
      return;
    }
    list.innerHTML = improvements
      .map(
        (imp) =>
          `<li>${escapeHtml(imp.label)}<span class="impact">（${escapeHtml(imp.category)}）</span></li>`
      )
      .join("");
  }

  function renderSalesSummary(text) {
    document.getElementById("sales-summary-text").textContent = text;
  }

  function renderKeywordEditor() {
    const editor = document.getElementById("keyword-editor");
    editor.innerHTML = "";
    currentKeywords.forEach((kw, i) => {
      const chip = document.createElement("span");
      chip.className = "keyword-chip";
      chip.innerHTML = `${escapeHtml(kw)} <button type="button" aria-label="削除">×</button>`;
      chip.querySelector("button").addEventListener("click", () => {
        currentKeywords.splice(i, 1);
        renderKeywordEditor();
      });
      editor.appendChild(chip);
    });
  }

  function renderSerpTable(result) {
    const head = document.getElementById("serp-table-head");
    const body = document.getElementById("serp-table-body");
    const devices = result.devices || [];
    const showPc = devices.includes("desktop");
    const showMobile = devices.includes("mobile");

    let headHtml = "<th>キーワード</th>";
    if (showPc) headHtml += "<th>PC</th>";
    if (showMobile) headHtml += "<th>スマホ</th>";
    headHtml += "<th>AI Overview</th>";
    head.innerHTML = headHtml;

    const serpResults = result.serp_results || [];
    const byKeyword = {};
    for (const r of serpResults) {
      byKeyword[r.keyword] = byKeyword[r.keyword] || {};
      byKeyword[r.keyword][r.device] = r;
    }

    body.innerHTML = "";
    for (const keyword of result.keywords || []) {
      const row = byKeyword[keyword] || {};
      const tr = document.createElement("tr");

      let anyAiOverview = null;
      for (const r of Object.values(row)) {
        if (r && r.ai_overview_shown) anyAiOverview = r;
      }
      if (anyAiOverview && anyAiOverview.ai_overview_cited) tr.className = "cited";

      let html = `<td>${escapeHtml(keyword)}</td>`;
      if (showPc) html += `<td>${rankText(row.desktop)}</td>`;
      if (showMobile) html += `<td>${rankText(row.mobile)}</td>`;
      html += `<td>${aiOverviewText(anyAiOverview)}</td>`;
      tr.innerHTML = html;
      body.appendChild(tr);
    }
  }

  function rankText(r) {
    if (!r) return "-";
    return r.rank ? `${r.rank}位` : "20位圏外";
  }

  function aiOverviewText(r) {
    if (!r || !r.ai_overview_shown) return "表示なし";
    return r.ai_overview_cited ? "表示あり・引用されている" : "表示あり";
  }

  function renderCompetitors(result) {
    const card = document.getElementById("competitors-card");
    const competitors = result.competitors || [];
    if (!competitors.length) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");

    const backlinkData = result.competitor_backlinks || {};
    const ownBacklinks = result.score.categories.find((c) => c.key === "authority");

    const head = document.getElementById("competitors-table-head");
    head.innerHTML = `<th>ドメイン</th><th>参照ドメイン数</th><th>ドメインランク</th>`;
    const body = document.getElementById("competitors-table-body");
    body.innerHTML = "";

    const ownRow = document.createElement("tr");
    ownRow.className = "own-row";
    ownRow.innerHTML = `<td>${escapeHtml(result.domain)}（自社）</td><td colspan="2">${ownBacklinks && ownBacklinks.measured ? "カテゴリ別スコアを参照" : "未計測"}</td>`;
    body.appendChild(ownRow);

    for (const domain of competitors) {
      const bl = backlinkData[domain];
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${escapeHtml(domain)}</td><td>${bl ? bl.referring_domains : "未計測"}</td><td>${bl ? bl.rank : "未計測"}</td>`;
      body.appendChild(tr);
    }

    document.getElementById("competitors-explanation").textContent =
      "詳細な指標比較（本文量・見出し構成・E-E-A-T要素等）は今後のアップデートで追加予定です。";
  }

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = String(value ?? "");
    return div.innerHTML;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    urlError.textContent = "";

    const normalized = normalizeUrl(urlInput.value);
    if (!normalized || !validateUrl(normalized)) {
      urlError.textContent = "有効なURLを入力してください（例：https://example.com）";
      return;
    }

    submitBtn.disabled = true;

    try {
      const jobId = await startAudit(normalized, selectedDevices());
      subscribeToJob(jobId);
    } catch (err) {
      urlError.textContent = err.message;
      submitBtn.disabled = false;
    }
  });

  document.getElementById("keyword-add-btn").addEventListener("click", () => {
    const input = document.getElementById("keyword-add-input");
    const value = input.value.trim();
    if (value && !currentKeywords.includes(value)) {
      currentKeywords.push(value);
      renderKeywordEditor();
    }
    input.value = "";
  });

  document.getElementById("rerun-btn").addEventListener("click", async () => {
    if (!lastUrl) return;
    try {
      const jobId = await startAudit(lastUrl, lastDevices, currentKeywords);
      subscribeToJob(jobId);
    } catch (err) {
      showFatalError(err.message);
    }
  });
})();
