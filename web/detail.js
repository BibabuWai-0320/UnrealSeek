/* UE 5.8 C++ API Detail View Renderer (Classic UE 4.7 Documentation Layout) */
window.UEApp = window.UEApp || {};

(function (App) {
  const KIND_LABELS = {
    function: ["F", "Function", "k-function"],
    class: ["C", "Class", "k-class"],
    struct: ["S", "Struct", "k-struct"],
    enum: ["E", "Enum", "k-enum"],
    interface: ["I", "Interface", "k-interface"],
    property: ["P", "Property", "k-property"],
    delegate: ["D", "Delegate", "k-delegate"],
  };

  App.currentSymbolId = null;

  App.esc = function (str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  };

  App.kindBadge = function (kind, full) {
    const info = KIND_LABELS[kind] || ["?", kind, "k-module"];
    return `<span class="kbadge ${info[2]}" title="${info[1]}">${full ? info[1] : info[0]}</span>`;
  };

  App.showToast = function (msg) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(App._toastTimer);
    App._toastTimer = setTimeout(() => el.classList.add("hidden"), 2200);
  };

  App.copyText = function (text, label) {
    navigator.clipboard.writeText(text).then(
      () => App.showToast("已复制 " + (label || text)),
      () => App.showToast("复制失败，请手动选中复制")
    );
  };

  App.linkifyType = function (typeStr) {
    if (!typeStr) return "";
    const escaped = App.esc(typeStr);
    // Turn Unreal type tokens (A*, U*, F*, E*, I*, S*, T*) into clickable links
    return escaped.replace(/\b([AUFEIST][A-Z][A-Za-z0-9_]{2,})\b/g, (m) => {
      return `<a class="type-link" data-type-jump="${m}" title="跳转到 ${m} 文档">${m}</a>`;
    });
  };

  App.highlightCpp = function (code) {
    const escaped = App.esc(code);
    // Single-pass tokenizer so injected <span> tags are never re-matched
    const tokenRe =
      /(\/\/[^\n]*)|(&quot;(?:\\.|[^&]|&(?!quot;))*&quot;)|\b(UCLASS|USTRUCT|UENUM|UINTERFACE|UFUNCTION|UPROPERTY|UE_API|[A-Z][A-Z0-9_]*_API|FORCEINLINE|UE_DEPRECATED)\b|\b(class|struct|enum|public|protected|private|virtual|static|const|override|final|inline|constexpr|explicit|void|bool|float|double|int32|uint32|int64|uint64|uint8|template|typename|return|auto|nullptr)\b/g;
    return escaped.replace(tokenRe, (m, cmt, str, macro, kw) => {
      if (cmt) return `<span style="color:var(--text-muted)">${cmt}</span>`;
      if (str) return `<span class="sy-str">${str}</span>`;
      if (macro) return `<span class="sy-macro">${macro}</span>`;
      if (kw) return `<span class="sy-kw">${kw}</span>`;
      return m;
    });
  };

  App.jumpToTypeName = async function (name) {
    try {
      const res = await fetch(`/api/symbol/by-name?name=${encodeURIComponent(name)}`);
      if (!res.ok) {
        App.showToast(`未找到类型 ${name} 的定义`);
        return;
      }
      const data = await res.json();
      if (data.found && data.id) {
        App.openSymbol(data.id, true);
      }
    } catch (e) {
      console.error(e);
    }
  };

  App.openSymbol = async function (symbolId, locateInTree = false) {
    if (!symbolId) return;
    App.currentSymbolId = Number(symbolId);
    if (location.hash !== `#symbol/${symbolId}`) {
      history.pushState(null, "", `#symbol/${symbolId}`);
    }
    const container = document.getElementById("docContainer");
    container.innerHTML = `<div class="tree-loading">正在加载 UE 5.8 API 文档...</div>`;

    try {
      const [detailRes] = await Promise.all([
        fetch(`/api/symbol/${symbolId}`),
        locateInTree && App.locateInSidebar ? App.locateInSidebar(symbolId) : Promise.resolve(),
      ]);
      if (!detailRes.ok) {
        container.innerHTML = `<div class="dep-banner">未找到 ID=${symbolId} 的符号文档。</div>`;
        return;
      }
      const d = await detailRes.json();
      renderDetail(d);
      document.getElementById("contentPane").scrollTop = 0;
    } catch (err) {
      container.innerHTML = `<div class="dep-banner">加载失败: ${App.esc(err.message)}</div>`;
    }
  };

  function buildUsageExample(d) {
    const inc = d.include_path
      ? `#include "${d.include_path}"`
      : `// 注意：该符号位于引擎内部 ${d.visibility} 头文件，外部项目不可直接 #include`;
    if (d.kind === "function") {
      const args = (d.params || []).map((p, i) => p.name || `Arg${i + 1}`).join(", ");
      const isStatic = (d.qualifiers || "").includes("static");
      const retPrefix = d.return_type && d.return_type !== "void" ? `auto Result = ` : "";
      if (d.owner_name) {
        const call = isStatic
          ? `${d.owner_name}::${d.name}(${args});`
          : `Target->${d.name}(${args});`;
        return `${inc}\n\n// 调用示例 (${d.owner_name})\n${retPrefix}${call}`;
      }
      return `${inc}\n\n${retPrefix}${d.name}(${args});`;
    }
    if (d.kind === "class" || d.kind === "struct") {
      return `${inc}\n\n// 使用 ${d.name}\n${d.name}${d.kind === "class" ? "* Instance = nullptr;" : " Value;"}`;
    }
    if (d.kind === "enum") {
      const firstVal = d.params && d.params[0] ? d.params[0].name : "Value";
      const sep = (d.qualifiers || "").includes("class") ? `${d.name}::${firstVal}` : firstVal;
      return `${inc}\n\n${d.name} Mode = ${sep};`;
    }
    return inc;
  }

  function renderDetail(d) {
    const container = document.getElementById("docContainer");
    const isPublicInc = d.visibility === "Public" && Boolean(d.include_path);
    const incLine = isPublicInc ? `#include "${d.include_path}"` : "";
    const defaultCoreMods = new Set(["Core", "CoreUObject", "Engine", "InputCore"]);
    const needBuildCs = isPublicInc && d.module && !defaultCoreMods.has(d.module);
    const buildCsLine = needBuildCs ? `PublicDependencyModuleNames.Add("${d.module}");` : "";
    const macroLine = d.macro_kind ? `${d.macro_kind}(${d.macro_specifiers || ""})\n` : "";
    const fullSyntax = macroLine + d.signature;
    const ownerId = d.owner_info ? d.owner_info.id : null;
    const parentCls = d.kind === "function" || d.kind === "property" || d.kind === "delegate"
      ? (d.owner_info ? d.owner_info.parent_name : "")
      : d.parent_name;

    let html = "";

    // 1. Breadcrumbs
    html += `<div class="breadcrumbs">
      <span class="bc-link" id="bcHome">UE 5.8 API</span> <span>&rsaquo;</span>
      <span>${App.esc(d.category)}</span> <span>&rsaquo;</span>
      <span class="bc-link" data-mod-cat="${App.esc(d.category)}" data-mod-name="${App.esc(d.module)}">${App.esc(d.module)}</span>
      ${d.owner_name ? `<span>&rsaquo;</span> <span class="bc-link" data-sym-id="${ownerId || ""}">${App.esc(d.owner_name)}</span>` : ""}
      <span>&rsaquo;</span> <strong>${App.esc(d.name)}</strong>
    </div>`;

    // 2. Title & Action Buttons
    html += `<div class="doc-header">
      <div class="doc-title-row">
        ${App.kindBadge(d.kind, true)}
        ${d.owner_name ? `<span class="doc-owner-prefix mono" data-sym-id="${ownerId || ""}">${App.esc(d.owner_name)}::</span>` : ""}
        <h1 class="doc-title mono">${App.esc(d.name)}</h1>
        <span class="pill ${d.visibility === "Public" ? "pill-pub" : "pill-priv"}">${App.esc(d.visibility)}</span>
        ${d.access ? `<span class="pill">${App.esc(d.access)}</span>` : ""}
        ${d.is_blueprint ? `<span class="pill pill-bp">Blueprint</span>` : ""}
        ${d.is_deprecated ? `<span class="pill pill-dep">Deprecated</span>` : ""}
      </div>
      <div class="doc-actions">
        <button class="act-btn" id="btnCopyInc">复制 #include</button>
        <button class="act-btn" id="btnCopySig">复制声明</button>
        <button class="act-btn" id="btnLocateNow">在左侧索引定位</button>
      </div>
    </div>`;

    if (d.is_deprecated) {
      html += `<div class="dep-banner"><strong>&#9888; 已废弃接口 (Deprecated)：</strong> ${App.esc(d.deprecated_msg || "该接口在 UE 5.8 中标记为已废弃，请查看推荐替代接口。")}</div>`;
    }

    // 3. Classic UE 4.7 References Box (Official #include, Build.cs dependency, Header, Module, Owner/Parent)
    html += `<div class="ref-box"><div class="ref-grid">
      <div class="ref-label">Include 头文件</div>
      <div class="ref-val">${isPublicInc
        ? `<code class="mono">${App.esc(incLine)}</code> <button class="copy-inline" id="btnCopyInc2">复制</button>`
        : `<span class="pill pill-priv">⚠️ 引擎内部 ${App.esc(d.visibility)} 头文件，外部项目不可直接 #include</span>`}</div>
      ${needBuildCs ? `<div class="ref-label">Build.cs 依赖</div>
      <div class="ref-val"><code class="mono">${App.esc(buildCsLine)}</code> <button class="copy-inline" id="btnCopyBuildCs">复制</button></div>` : ""}
      <div class="ref-label">Header 路径</div>
      <div class="ref-val"><code class="mono">/${App.esc(d.rel_path)}</code> <span class="pill">Line ${d.line_start}</span></div>
      <div class="ref-label">Module 模块</div>
      <div class="ref-val"><strong>${App.esc(d.module)}</strong> <span class="pill">${App.esc(d.category)}${d.plugin_name ? " / Plugin: " + App.esc(d.plugin_name) : ""}</span></div>
      ${d.owner_name ? `<div class="ref-label">所属类 / 父类</div><div class="ref-val">
        <a class="type-link mono" data-sym-id="${ownerId || ""}">${App.esc(d.owner_name)}</a>
        ${parentCls ? `<span>&nbsp;继承自&nbsp;</span><a class="type-link mono" data-type-jump="${App.esc(parentCls)}">${App.esc(parentCls)}</a>` : ""}
      </div>` : (d.parent_name ? `<div class="ref-label">直接父类</div><div class="ref-val">
        <a class="type-link mono" data-type-jump="${App.esc(d.parent_name)}">${App.esc(d.parent_name)}</a>
        ${d.base_classes_list.length > 1 ? `<span class="sr-owner">(全部基类: ${App.esc(d.base_classes_list.join(", "))})</span>` : ""}
      </div>` : "")}
    </div></div>`;

    // 4. Inheritance Hierarchy Chain
    if (d.inheritance_chain && d.inheritance_chain.length > 0) {
      html += `<section class="doc-section">
        <h2 class="sec-title">继承关系 (Inheritance Hierarchy)</h2>
        <div class="chain-list">
          ${d.inheritance_chain.map((node, idx) => {
            const isCur = idx === d.inheritance_chain.length - 1;
            const sep = idx > 0 ? `<span class="chain-arrow">&rarr;</span>` : "";
            return `${sep}<span class="chain-node ${isCur ? "current" : ""}" data-sym-id="${node.id || ""}" data-type-jump="${App.esc(node.name)}" title="${App.esc(node.module || "")}">${App.esc(node.name)}</span>`;
          }).join("")}
        </div>
      </section>`;
    }

    // 5. Syntax Block
    html += `<section class="doc-section">
      <h2 class="sec-title">语法声明 (Syntax)</h2>
      <pre class="code-block mono">${App.highlightCpp(fullSyntax)}</pre>
    </section>`;

    // 6. Usage / Description (Plain Chinese + collapsible original English UE 5.8 comment)
    const origEn = [d.doc_summary, d.doc_remarks].filter(Boolean).join("\n\n");
    html += `<section class="doc-section">
      <h2 class="sec-title">通俗中文说明与用法 (Description &amp; Usage)</h2>
      <p style="margin:0 0 8px 0;line-height:1.7;font-size:14px;">${App.esc(d.doc_zh || d.doc_summary || "该接口暂无额外说明，可结合下方参数、返回值与源码上下文查看具体用法。")}</p>
      ${origEn ? `<details class="orig-en-box"><summary>查看 UE 5.8 头文件英文原注释</summary><div class="orig-en-body">${App.esc(origEn)}</div></details>` : ""}
      <pre class="code-block mono">${App.highlightCpp(buildUsageExample(d))}</pre>
    </section>`;

    // 7. Parameters Table (Function) OR Enum Values (Enum)
    if (d.kind === "function") {
      html += `<section class="doc-section">
        <h2 class="sec-title">参数列表 (Parameters · ${d.params.length})</h2>
        ${d.params.length ? `<table class="ue-table">
          <thead><tr><th style="width:26%">类型 (Type)</th><th style="width:20%">参数名 (Name)</th><th style="width:16%">默认值 (Default)</th><th>通俗说明 (Description)</th></tr></thead>
          <tbody>
            ${d.params.map((p) => `<tr>
              <td class="mono">${App.linkifyType(p.type)}</td>
              <td class="mono"><strong>${App.esc(p.name || "-")}</strong></td>
              <td class="mono">${App.esc(p.default || "-")}</td>
              <td>
                <div>${App.esc(p.doc_zh || p.doc || "-")}</div>
                ${p.doc ? `<div style="font-size:11.5px;color:var(--text-muted);margin-top:2px;">原注: ${App.esc(p.doc)}</div>` : ""}
              </td>
            </tr>`).join("")}
          </tbody>
        </table>` : `<p style="color:var(--text-muted);margin:0;">无参数 (void)</p>`}
      </section>`;

      // 8. Return Value
      html += `<section class="doc-section">
        <h2 class="sec-title">返回值 (Return Value)</h2>
        <div class="ref-box" style="margin-bottom:0;">
          <div class="mono" style="font-size:13.5px;margin-bottom:4px;"><strong>${d.return_type ? App.linkifyType(d.return_type) : "Constructor / Destructor"}</strong></div>
          <div style="line-height:1.6;">${App.esc(d.return_zh || "无返回值")}</div>
          ${d.doc_return ? `<div style="font-size:12px;color:var(--text-muted);margin-top:4px;">原注: ${App.esc(d.doc_return)}</div>` : ""}
        </div>
      </section>`;
    } else if (d.kind === "enum" && d.params && d.params.length) {
      html += `<section class="doc-section">
        <h2 class="sec-title">枚举值 (Enum Values · ${d.params.length})</h2>
        <table class="ue-table">
          <thead><tr><th style="width:30%">名称 (Enumerator)</th><th style="width:16%">值 (Value)</th><th>说明 (Description)</th></tr></thead>
          <tbody>
            ${d.params.map((ev) => `<tr>
              <td class="mono"><strong>${App.esc(ev.name)}</strong></td>
              <td class="mono">${App.esc(ev.value || "-")}</td>
              <td>${App.esc(ev.doc || "-")}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </section>`;
    } else if (d.kind === "property") {
      html += `<section class="doc-section">
        <h2 class="sec-title">属性类型与默认值 (Property Type)</h2>
        <div class="ref-box" style="margin-bottom:0;">
          <div class="mono">类型: <strong>${App.linkifyType(d.return_type)}</strong>${d.params_raw ? ` &nbsp;|&nbsp; 默认值: <code>${App.esc(d.params_raw)}</code>` : ""}</div>
        </div>
      </section>`;
    }

    // 9. Overloads
    if (d.overloads && d.overloads.length > 1) {
      html += `<section class="doc-section">
        <h2 class="sec-title">同名重载函数 (Overloads · ${d.overloads.length})</h2>
        <table class="ue-table">
          <thead><tr><th>签名 (Signature)</th><th style="width:90px">行号</th></tr></thead>
          <tbody>
            ${d.overloads.map((ov) => `<tr style="${ov.id === d.id ? "background:var(--sidebar-active);" : ""}">
              <td class="mono"><a class="type-link" data-sym-id="${ov.id}">${App.esc(ov.signature)}</a></td>
              <td class="mono">L${ov.line_start}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </section>`;
    }

    // 10. Derived Classes & Class Members (when viewing a class/struct/interface)
    if (d.derived_classes && d.derived_classes.length > 0) {
      html += `<section class="doc-section">
        <h2 class="sec-title">直接派生子类 (Derived Classes · ${d.derived_classes.length})</h2>
        <div class="chip-row">
          ${d.derived_classes.map((c) => `<span class="example-chip" data-sym-id="${c.id}" title="${App.esc(c.module)}">${App.esc(c.name)}</span>`).join("")}
        </div>
      </section>`;
    }

    // 10b. Unified Member Explorer (Own + Inherited Functions & Properties from Parent Classes!)
    const ownFns = (d.members && d.members.functions) || [];
    const ownProps = (d.members && d.members.properties) || [];
    const inhGroups = d.inherited_members || [];
    const allMembers = [];
    ownProps.forEach((p) => allMembers.push({ ...p, kind: "property", isOwn: true, declared_in: d.name, declared_in_id: d.id }));
    ownFns.forEach((f) => allMembers.push({ ...f, kind: "function", isOwn: true, declared_in: d.name, declared_in_id: d.id }));
    inhGroups.forEach((g) => {
      (g.functions || []).forEach((f) => allMembers.push({ ...f, kind: "function", isOwn: false, declared_in: g.from_class, declared_in_id: g.from_id }));
      (g.properties || []).forEach((p) => allMembers.push({ ...p, kind: "property", isOwn: false, declared_in: g.from_class, declared_in_id: g.from_id }));
    });

    if (allMembers.length > 0) {
      const totalFns = allMembers.filter((m) => m.kind === "function").length;
      const totalProps = allMembers.filter((m) => m.kind === "property").length;
      const ownTotal = ownFns.length + ownProps.length;
      const sourceClasses = [d.name, ...inhGroups.map((g) => g.from_class)];

      html += `<section class="doc-section" id="memberExplorerSec">
        <h2 class="sec-title">
          <span>可用函数与属性总览 (含父类继承 · 共 ${allMembers.length} 项)</span>
          <input type="text" class="member-filter" id="unifiedMemberFilter" placeholder="支持中英文过滤，如：俯仰、延迟、旋转、SetRelativeLocationAndRotation..." />
        </h2>
        <div class="member-controls">
          <div class="member-tabs" id="memberKindTabs">
            <button class="mtab-btn active" data-mkind="all">全部可用 (${allMembers.length})</button>
            <button class="mtab-btn" data-mkind="function">函数 (${totalFns})</button>
            <button class="mtab-btn" data-mkind="property">属性 (${totalProps})</button>
            <button class="mtab-btn" data-mkind="own">仅本类 (${ownTotal})</button>
          </div>
          <div class="member-tabs" id="memberSrcTabs">
            <button class="mtab-btn active" data-msrc="">全部父类</button>
            ${sourceClasses.map((sc, i) => `<button class="mtab-btn" data-msrc="${App.esc(sc)}">${i === 0 ? "本类 " : "继承 "}${App.esc(sc)}</button>`).join("")}
          </div>
        </div>
        <table class="ue-table">
          <thead>
            <tr>
              <th style="width:15%">来源类</th>
              <th style="width:18%">类型 / 返回值</th>
              <th style="width:37%">函数名 / 属性名与参数</th>
              <th>说明 (Description)</th>
            </tr>
          </thead>
          <tbody id="unifiedMemberBody"></tbody>
        </table>
        <div id="unifiedMemberMore" class="tree-more hidden" style="padding:8px 0;"></div>
      </section>`;
    }

    // 11. Source Code Context Preview
    if (d.source_snippet && d.source_snippet.lines && d.source_snippet.lines.length) {
      const s0 = d.source_snippet.start_line;
      const hs = d.source_snippet.highlight_start;
      const he = d.source_snippet.highlight_end;
      html += `<section class="doc-section">
        <h2 class="sec-title">UE 5.8 头文件源码上下文 (${App.esc(d.file_name)} : L${hs})</h2>
        <div class="src-box mono">
          ${d.source_snippet.lines.map((line, i) => {
            const ln = s0 + i;
            const isHl = ln >= hs && ln <= he;
            return `<div class="src-line ${isHl ? "hl" : ""}"><span class="src-ln">${ln}</span><span class="src-code">${App.esc(line)}</span></div>`;
          }).join("")}
        </div>
      </section>`;
    }

    container.innerHTML = html;

    // Bind buttons & interactive links
    document.getElementById("bcHome")?.addEventListener("click", () => App.renderWelcome());
    document.getElementById("btnCopyInc")?.addEventListener("click", () => {
      if (incLine) App.copyText(incLine, incLine);
      else App.showToast("该符号为引擎内部头文件，外部项目不可直接 #include");
    });
    document.getElementById("btnCopyInc2")?.addEventListener("click", () => App.copyText(incLine, incLine));
    document.getElementById("btnCopyBuildCs")?.addEventListener("click", () => App.copyText(buildCsLine, buildCsLine));
    document.getElementById("btnCopySig")?.addEventListener("click", () => App.copyText(fullSyntax, "函数/类声明"));
    document.getElementById("btnLocateNow")?.addEventListener("click", () => {
      if (App.locateInSidebar) App.locateInSidebar(d.id);
    });

    container.querySelectorAll("[data-sym-id]").forEach((el) => {
      const sid = el.getAttribute("data-sym-id");
      if (!sid) return;
      el.addEventListener("click", (e) => {
        e.preventDefault();
        App.openSymbol(Number(sid), true);
      });
    });

    container.querySelectorAll("[data-type-jump]").forEach((el) => {
      if (el.getAttribute("data-sym-id")) return;
      el.addEventListener("click", (e) => {
        e.preventDefault();
        App.jumpToTypeName(el.getAttribute("data-type-jump"));
      });
    });

    // Unified Member Explorer interactive rendering (own + inherited parent functions/properties)
    const uBody = document.getElementById("unifiedMemberBody");
    if (uBody && allMembers.length) {
      let curKind = "all";
      let curSrc = "";
      let curKw = "";
      let showLimit = 180;

      const renderMemberRows = () => {
        const filtered = allMembers.filter((m) => {
          if (curKind === "function" && m.kind !== "function") return false;
          if (curKind === "property" && m.kind !== "property") return false;
          if (curKind === "own" && !m.isOwn) return false;
          if (curSrc && m.declared_in !== curSrc) return false;
          if (curKw) {
            const hay = `${m.name} ${m.params_raw || ""} ${m.doc_zh || ""} ${m.doc_summary || ""} ${m.declared_in}`.toLowerCase();
            if (!hay.includes(curKw)) return false;
          }
          return true;
        });

        const visible = filtered.slice(0, showLimit);
        uBody.innerHTML = visible.length
          ? visible.map((m) => {
              const pr = m.params_raw || "";
              const sigCell = m.kind === "function"
                ? `${App.kindBadge("function", false)} <a class="type-link" data-sym-id="${m.id}"><strong>${App.esc(m.name)}</strong></a>(${App.esc(pr.slice(0, 80))}${pr.length > 80 ? "..." : ""})`
                : `${App.kindBadge("property", false)} <a class="type-link" data-sym-id="${m.id}"><strong>${App.esc(m.name)}</strong></a>`;
              return `<tr>
                <td><span class="src-pill ${m.isOwn ? "own" : ""} mono" data-sym-id="${m.declared_in_id || ""}" title="查看 ${App.esc(m.declared_in)}">${m.isOwn ? "本类" : App.esc(m.declared_in)}</span></td>
                <td class="mono">${App.linkifyType(m.return_type || "-")}</td>
                <td class="mono">${sigCell}</td>
                <td>${App.esc(m.doc_zh || m.doc_summary || "")}</td>
              </tr>`;
            }).join("")
          : `<tr><td colspan="4" style="color:var(--text-muted);text-align:center;padding:14px;">未找到匹配的成员函数或属性</td></tr>`;

        // Bind newly rendered links
        uBody.querySelectorAll("[data-sym-id]").forEach((el) => {
          const sid = el.getAttribute("data-sym-id");
          if (sid) el.addEventListener("click", (e) => { e.preventDefault(); App.openSymbol(Number(sid), true); });
        });
        uBody.querySelectorAll("[data-type-jump]").forEach((el) => {
          el.addEventListener("click", (e) => { e.preventDefault(); App.jumpToTypeName(el.getAttribute("data-type-jump")); });
        });

        const moreEl = document.getElementById("unifiedMemberMore");
        if (filtered.length > visible.length) {
          moreEl.classList.remove("hidden");
          moreEl.textContent = `显示更多... (当前 ${visible.length} / 匹配共 ${filtered.length} 项，点击展开全部)`;
          moreEl.onclick = () => { showLimit = filtered.length; renderMemberRows(); };
        } else {
          moreEl.classList.add("hidden");
        }
      };

      document.getElementById("unifiedMemberFilter")?.addEventListener("input", (e) => {
        curKw = e.target.value.trim().toLowerCase();
        showLimit = 180;
        renderMemberRows();
      });
      document.querySelectorAll("#memberKindTabs .mtab-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          document.querySelectorAll("#memberKindTabs .mtab-btn").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          curKind = btn.dataset.mkind;
          showLimit = 180;
          renderMemberRows();
        });
      });
      document.querySelectorAll("#memberSrcTabs .mtab-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          document.querySelectorAll("#memberSrcTabs .mtab-btn").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          curSrc = btn.dataset.msrc;
          showLimit = 180;
          renderMemberRows();
        });
      });

      renderMemberRows();
    }
  }

  App.renderWelcome = async function () {
    App.currentSymbolId = null;
    if (location.hash) history.pushState(null, "", "/");
    const container = document.getElementById("docContainer");
    let stats = null;
    try {
      stats = await (await fetch("/api/stats")).json();
    } catch (e) {}

    const meta = stats?.meta || {};
    const kinds = stats?.kinds || {};
    const demoZhQueries = [
      "设置时间令牌",
      "设置角色的local旋转与location",
      "弹簧臂俯仰角跟随鼠标",
      "摄像机平滑延迟",
      "射线检测",
      "获取插槽位置和旋转",
      "挂载组件",
    ];
    const demoQueries = [
      "SpringArm",
      "X->GetLocationandRotation",
      "PC->GetPlayerViewPoint(Loc, Rot);",
      "const FVector Pos = MyActor->GetActorLoc();",
      "GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility)",
      "GetActorLocatoin",
    ];
    const quickClasses = [
      "AActor", "UObject", "USceneComponent", "UActorComponent", "UWorld",
      "APlayerController", "ACharacter", "APawn", "USplineComponent",
      "UPhysicsHandleComponent", "UGameplayStatics", "UEnhancedInputComponent", "FVector", "FTransform",
    ];

    container.innerHTML = `
      <div class="hero-box">
        <h1 style="margin:0 0 6px 0;font-size:22px;">Unreal Engine 5.8 C++ API 接口文档库</h1>
        <p style="margin:0;color:var(--text-muted);line-height:1.6;">
          直接基于本地 <strong>UE 5.8</strong> 引擎源码头文件完整索引，还原经典 UE 4.7 风格双栏接口文档体验。支持顶部代码行粘贴模糊检索、自动定位左侧父类与目录索引树、右侧展示头文件 <code>#include</code>、参数、返回值、继承链与源码上下文。
        </p>
        <div class="stats-grid">
          <div class="stat-card"><div class="stat-num">${Number(meta.total_symbols || 0).toLocaleString()}</div><div class="stat-lbl">收录 API 符号总数</div></div>
          <div class="stat-card"><div class="stat-num">${Number(kinds.function || 0).toLocaleString()}</div><div class="stat-lbl">C++ 函数 / 方法</div></div>
          <div class="stat-card"><div class="stat-num">${(Number(kinds.class || 0) + Number(kinds.struct || 0)).toLocaleString()}</div><div class="stat-lbl">类 &amp; 结构体</div></div>
          <div class="stat-card"><div class="stat-num">${Number(meta.total_headers || 0).toLocaleString()}</div><div class="stat-lbl">解析头文件 (.h)</div></div>
        </div>
      </div>

      <section class="doc-section">
        <h2 class="sec-title">试试中文自然语言检索 (支持简繁体与中英混合，点击体验)</h2>
        <p style="margin:0 0 8px 0;color:var(--text-muted);">不知道英文函数名也没关系，直接输入你想实现的功能描述即可匹配对应 UE 5.8 函数与结构体：</p>
        <div class="chip-row">
          ${demoZhQueries.map((q) => `<span class="example-chip" data-demo-q="${App.esc(q)}">${App.esc(q)}</span>`).join("")}
        </div>
      </section>

      <section class="doc-section">
        <h2 class="sec-title">试试代码行智能模糊检索 (点击即刻体验)</h2>
        <p style="margin:0 0 8px 0;color:var(--text-muted);">直接粘贴项目中的 C++ 调用代码（支持大小写错误、拼写错误、缩写、任意对象前缀 <code>X-&gt;</code> 与参数匹配）：</p>
        <div class="chip-row">
          ${demoQueries.map((q) => `<span class="example-chip" data-demo-q="${App.esc(q)}">${App.esc(q)}</span>`).join("")}
        </div>
      </section>

      <section class="doc-section">
        <h2 class="sec-title">常用核心类快捷入口</h2>
        <div class="chip-row">
          ${quickClasses.map((c) => `<span class="example-chip" data-type-jump="${c}">${c}</span>`).join("")}
        </div>
      </section>
    `;

    container.querySelectorAll("[data-demo-q]").forEach((el) => {
      el.addEventListener("click", () => {
        const q = el.getAttribute("data-demo-q");
        if (App.triggerSearch) App.triggerSearch(q, true);
      });
    });
    container.querySelectorAll("[data-type-jump]").forEach((el) => {
      el.addEventListener("click", () => App.jumpToTypeName(el.getAttribute("data-type-jump")));
    });
  };
})(window.UEApp);
