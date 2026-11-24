/* Top Intelligent Fuzzy & Code-Line Search Bar + App Initialization */
window.UEApp = window.UEApp || {};

(function (App) {
  let results = [];
  let activeIdx = -1;
  let debounceTimer = null;
  let abortCtrl = null;

  const inputEl = () => document.getElementById("searchInput");
  const kindEl = () => document.getElementById("searchKind");
  const pubEl = () => document.getElementById("chkPublicOnly");
  const dropEl = () => document.getElementById("searchDropdown");
  const listEl = () => document.getElementById("searchResultsList");
  const parsedEl = () => document.getElementById("searchParsedBar");
  const timingEl = () => document.getElementById("searchTiming");
  const clearBtn = () => document.getElementById("btnClearSearch");

  function closeDropdown() {
    dropEl().classList.add("hidden");
  }

  function openDropdown() {
    dropEl().classList.remove("hidden");
  }

  function setActive(idx) {
    if (!results.length) {
      activeIdx = -1;
      return;
    }
    activeIdx = ((idx % results.length) + results.length) % results.length;
    listEl().querySelectorAll(".sr-item").forEach((el, i) => {
      el.classList.toggle("active", i === activeIdx);
      if (i === activeIdx) el.scrollIntoView({ block: "nearest" });
    });
  }

  function selectResult(idx) {
    const item = results[idx];
    if (!item) return;
    closeDropdown();
    App.openSymbol(item.id, true);
  }

  async function runSearch(autoSelectFirst = false) {
    const q = inputEl().value.trim();
    clearBtn().classList.toggle("hidden", !q);
    if (!q) {
      results = [];
      closeDropdown();
      return;
    }

    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();

    const params = new URLSearchParams({
      q,
      kind: kindEl().value,
      public_only: pubEl().checked ? "1" : "0",
      limit: "30",
    });

    const t0 = performance.now();
    try {
      const res = await fetch(`/api/search?${params.toString()}`, { signal: abortCtrl.signal });
      const data = await res.json();
      const dt = Math.round(performance.now() - t0);

      results = data.results || [];
      activeIdx = results.length ? 0 : -1;
      renderDropdown(data, dt);
      openDropdown();

      if (autoSelectFirst && results.length > 0) {
        selectResult(0);
      }
    } catch (err) {
      if (err.name !== "AbortError") console.error("Search error:", err);
    }
  }

  function renderDropdown(data, dtMs) {
    const parsed = data.parsed;
    const pBar = parsedEl();
    if (parsed && parsed.is_zh_query) {
      pBar.innerHTML = `<strong>&#127760; 中文语义匹配:</strong> <span>${App.esc(parsed.explanation || parsed.target)}</span>`;
      pBar.classList.remove("hidden");
    } else if (parsed && (parsed.is_code_expr || parsed.explanation)) {
      pBar.innerHTML = `<strong>&#9889; 代码智能解析:</strong> <span>${App.esc(parsed.explanation || parsed.target)}</span>`;
      pBar.classList.remove("hidden");
    } else if (parsed && parsed.words && parsed.words.length > 1) {
      pBar.innerHTML = `<strong>&#128270; 模糊分词:</strong> <span>${App.esc(parsed.words.join(" / "))}</span>`;
      pBar.classList.remove("hidden");
    } else {
      pBar.classList.add("hidden");
    }

    timingEl().textContent = `找到 ${results.length} 条最佳匹配 (${dtMs} ms)`;

    const list = listEl();
    if (!results.length) {
      list.innerHTML = `<div class="tree-loading">未找到匹配项，请尝试更换中英文关键词或取消“仅Public”筛选。</div>`;
      return;
    }

    list.innerHTML = results
      .map((r, i) => {
        const ownerHtml = r.owner_name ? `<span class="sr-owner mono">${App.esc(r.owner_name)}::</span>` : "";
        const paramsHtml = r.kind === "function" ? `<span class="sr-sig mono">(${App.esc(r.params_raw.slice(0, 75))}${r.params_raw.length > 75 ? "..." : ""})</span>` : "";
        const tagsHtml = (r.match_reasons || []).map((t) => `<span class="sr-tag">${App.esc(t)}</span>`).join("");
        const zhHtml = r.doc_zh ? `<div class="sr-desc-zh">${App.esc(r.doc_zh)}</div>` : "";
        return `<div class="sr-item ${i === 0 ? "active" : ""}" data-idx="${i}">
          <div class="sr-row1">
            ${App.kindBadge(r.kind, false)}
            ${ownerHtml}<span class="sr-name mono">${App.esc(r.name)}</span>
            ${paramsHtml}
          </div>
          ${zhHtml}
          <div class="sr-row2">
            <span class="mono">${r.include_path ? `#include "${App.esc(r.include_path)}"` : `[引擎内部 ${App.esc(r.visibility)} 头文件，不可直接 #include]`}</span>
            <span>&middot;</span>
            <span>${App.esc(r.module)} (${App.esc(r.category)})</span>
            ${r.return_type ? `<span>&middot; 返回: <code class="mono">${App.esc(r.return_type.slice(0, 28))}</code></span>` : ""}
            ${tagsHtml}
          </div>
        </div>`;
      })
      .join("");

    list.querySelectorAll(".sr-item").forEach((el) => {
      el.addEventListener("mousedown", (e) => {
        e.preventDefault();
        selectResult(Number(el.dataset.idx));
      });
    });
  }

  App.triggerSearch = function (queryText, autoSelectFirst = false) {
    inputEl().value = queryText;
    inputEl().focus();
    runSearch(autoSelectFirst);
  };

  function initApp() {
    const inp = inputEl();
    inp.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => runSearch(false), 120);
    });
    inp.addEventListener("focus", () => {
      if (inp.value.trim() && results.length) openDropdown();
    });
    inp.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive(activeIdx + 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(activeIdx - 1);
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (results.length && !dropEl().classList.contains("hidden") && activeIdx >= 0) {
          selectResult(activeIdx);
        } else {
          runSearch(true);
        }
      } else if (e.key === "Escape") {
        closeDropdown();
      }
    });

    // Global shortcut '/' focuses search bar
    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
        e.preventDefault();
        inp.focus();
        inp.select();
      }
    });

    document.getElementById("btnSearchGo").addEventListener("click", () => runSearch(true));
    kindEl().addEventListener("change", () => { if (inp.value.trim()) runSearch(false); });
    pubEl().addEventListener("change", () => { if (inp.value.trim()) runSearch(false); });

    clearBtn().addEventListener("click", () => {
      inp.value = "";
      results = [];
      clearBtn().classList.add("hidden");
      closeDropdown();
      inp.focus();
    });

    document.addEventListener("click", (e) => {
      if (!document.getElementById("searchWrapper").contains(e.target)) {
        closeDropdown();
      }
    });

    // Home button
    document.getElementById("btnHome").addEventListener("click", () => App.renderWelcome());

    // Theme toggle (Classic UE 4.7 Light default, persisted in localStorage)
    const savedTheme = localStorage.getItem("ue58_theme");
    if (savedTheme === "dark") {
      document.body.classList.add("dark");
      document.getElementById("themeIcon").innerHTML = "&#9728;";
    }
    document.getElementById("btnThemeToggle").addEventListener("click", () => {
      const isDark = document.body.classList.toggle("dark");
      localStorage.setItem("ue58_theme", isDark ? "dark" : "light");
      document.getElementById("themeIcon").innerHTML = isDark ? "&#9728;" : "&#9789;";
    });

    // Hash routing (#symbol/<id>)
    function handleHash() {
      const m = location.hash.match(/^#symbol\/(\d+)$/);
      if (m) {
        App.openSymbol(Number(m[1]), true);
      } else {
        App.renderWelcome();
      }
    }
    window.addEventListener("hashchange", handleHash);
    handleHash();
  }

  document.addEventListener("DOMContentLoaded", initApp);
})(window.UEApp);
