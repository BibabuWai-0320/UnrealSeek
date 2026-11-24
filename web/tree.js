/* Left Sidebar Dual-Mode Index Tree (Module Tree + Class Inheritance Tree) with Auto-Locate */
window.UEApp = window.UEApp || {};

(function (App) {
  let currentMode = "module"; // "module" | "hierarchy"
  let statsCache = null;

  const treeEl = () => document.getElementById("sidebarTree");
  const statusEl = () => document.getElementById("sidebarStatus");

  function setStatus(text) {
    const el = statusEl();
    if (el) el.textContent = text;
  }

  function makeNode({ key, depth, badgeHtml, label, parentHint, countText, expandable, onExpand, onClick }) {
    const wrap = document.createElement("div");
    wrap.className = "tree-node";
    wrap.dataset.nodeKey = key;

    const row = document.createElement("div");
    row.className = "tree-row";
    row.style.paddingLeft = `${6 + depth * 11}px`;

    const caret = document.createElement("span");
    caret.className = "tree-caret" + (expandable ? "" : " leaf");
    caret.innerHTML = "&#9654;";
    row.appendChild(caret);

    if (badgeHtml) {
      const b = document.createElement("span");
      b.innerHTML = badgeHtml;
      row.appendChild(b.firstElementChild);
    }

    const lbl = document.createElement("span");
    lbl.className = "tree-label mono";
    lbl.textContent = label;
    row.appendChild(lbl);

    if (parentHint) {
      const ph = document.createElement("span");
      ph.className = "tree-parent-hint mono";
      ph.textContent = `: ${parentHint}`;
      row.appendChild(ph);
    }

    if (countText) {
      const cnt = document.createElement("span");
      cnt.className = "tree-count";
      cnt.textContent = countText;
      row.appendChild(cnt);
    }

    wrap.appendChild(row);

    const childrenBox = document.createElement("div");
    childrenBox.className = "tree-children";
    wrap.appendChild(childrenBox);

    wrap._expand = async function (forceOpen = false) {
      if (!expandable) return;
      const isOpen = childrenBox.classList.contains("open");
      if (isOpen && !forceOpen) {
        childrenBox.classList.remove("open");
        caret.classList.remove("open");
        return;
      }
      childrenBox.classList.add("open");
      caret.classList.add("open");
      if (!wrap.dataset.loaded && onExpand) {
        wrap.dataset.loaded = "loading";
        childrenBox.innerHTML = `<div class="tree-loading" style="padding-left:${22 + depth * 14}px">加载中...</div>`;
        await onExpand(childrenBox, depth + 1);
        wrap.dataset.loaded = "1";
      }
    };

    caret.addEventListener("click", (e) => {
      e.stopPropagation();
      wrap._expand(false);
    });

    row.addEventListener("click", () => {
      if (onClick) onClick(row, wrap);
      else if (expandable) wrap._expand(false);
    });

    return wrap;
  }

  function clearSelected() {
    treeEl().querySelectorAll(".tree-row.selected").forEach((r) => r.classList.remove("selected"));
  }

  function highlightNode(nodeEl) {
    if (!nodeEl) return;
    clearSelected();
    const row = nodeEl.querySelector(":scope > .tree-row");
    if (!row) return;
    row.classList.add("selected");
    row.classList.remove("locate-pulse");
    void row.offsetWidth;
    row.classList.add("locate-pulse");
    row.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  /* ================= MODULE TREE MODE ================= */
  async function renderModuleRoots() {
    const root = treeEl();
    root.innerHTML = `<div class="tree-loading">正在加载 UE 5.8 模块目录...</div>`;
    if (!statsCache) {
      statsCache = await (await fetch("/api/stats")).json();
    }
    root.innerHTML = "";
    const cats = statsCache.categories || [];
    for (const c of cats) {
      const catNode = makeNode({
        key: `cat:${c.category}`,
        depth: 0,
        badgeHtml: `<span class="kbadge k-cat">DIR</span>`,
        label: c.category,
        countText: `${c.module_count} 模块`,
        expandable: true,
        onExpand: async (box, d) => {
          const data = await (await fetch(`/api/tree/modules?category=${encodeURIComponent(c.category)}&limit=400`)).json();
          box.innerHTML = "";
          for (const m of data.items) {
            box.appendChild(createModuleNode(c.category, m, d));
          }
        },
      });
      root.appendChild(catNode);
    }
    setStatus(`UE 5.8 · ${Number(statsCache.meta?.total_symbols || 0).toLocaleString()} 个符号`);
    // Auto-expand Runtime on first load for immediate classic UE4 feel
    const rt = root.querySelector('[data-node-key="cat:Runtime"]');
    if (rt && rt._expand) rt._expand(true);
  }

  function createModuleNode(category, modInfo, depth) {
    return makeNode({
      key: `mod:${category}:${modInfo.module}`,
      depth,
      badgeHtml: `<span class="kbadge k-module">M</span>`,
      label: modInfo.module,
      countText: `${modInfo.class_count + modInfo.struct_count} 类`,
      expandable: true,
      onExpand: async (box, d) => {
        await loadModuleTypesPage(box, category, modInfo.module, 0, d);
      },
    });
  }

  async function loadModuleTypesPage(box, category, moduleName, offset, depth) {
    const pubOnly = document.getElementById("chkPublicOnly")?.checked ? "1" : "0";
    const res = await (
      await fetch(`/api/tree/module-types?category=${encodeURIComponent(category)}&module=${encodeURIComponent(moduleName)}&public_only=${pubOnly}&offset=${offset}&limit=160`)
    ).json();
    if (offset === 0) box.innerHTML = "";
    else box.querySelector(".tree-more")?.remove();

    for (const item of res.items) {
      if (!box.querySelector(`[data-node-key="sym:${item.id}"]`)) {
        box.appendChild(createTypeNode(item, depth, false));
      }
    }
    const nextOff = offset + res.items.length;
    if (nextOff < res.total) {
      const more = document.createElement("div");
      more.className = "tree-more";
      more.style.paddingLeft = `${14 + depth * 14}px`;
      more.textContent = `加载更多... (已显示 ${nextOff} / ${res.total})`;
      more.addEventListener("click", () => loadModuleTypesPage(box, category, moduleName, nextOff, depth));
      box.appendChild(more);
    }
  }

  function createTypeNode(item, depth, includeSubclasses) {
    const hasChildren = item.kind !== "enum" && ((item.func_count || 0) > 0 || (item.child_count || 0) > 0 || includeSubclasses);
    return makeNode({
      key: `sym:${item.id}`,
      depth,
      badgeHtml: App.kindBadge(item.kind, false),
      label: item.name,
      parentHint: !includeSubclasses && item.parent_name ? item.parent_name : "",
      countText: includeSubclasses
        ? (item.child_count ? `${item.child_count} 子类` : item.func_count ? `${item.func_count} fn` : "")
        : (item.func_count ? `${item.func_count}` : ""),
      expandable: hasChildren,
      onClick: (row, wrap) => {
        clearSelected();
        row.classList.add("selected");
        App.openSymbol(item.id, false);
        if (hasChildren) wrap._expand(true);
      },
      onExpand: async (box, d) => {
        box.innerHTML = "";
        if (includeSubclasses) {
          const ch = await (await fetch(`/api/hierarchy/children?parent=${encodeURIComponent(item.name)}&limit=160`)).json();
          for (const sub of ch.items) {
            box.appendChild(createTypeNode(sub, d, true));
          }
        }
        const mem = await (await fetch(`/api/tree/class-members?class_name=${encodeURIComponent(item.name)}&owner_id=${item.id}&limit=400`)).json();
        for (const m of mem.items) {
          box.appendChild(createMemberNode(m, d));
        }
        // Add expandable "Inherited from Parent" folders so every component shows all parent-class functions!
        if (mem.ancestors && mem.ancestors.length) {
          for (const anc of mem.ancestors.slice(0, 4)) {
            box.appendChild(
              makeNode({
                key: `inh:${item.id}:${anc.id}`,
                depth: d,
                badgeHtml: `<span class="kbadge k-inherit">父</span>`,
                label: `继承自 ${anc.name}`,
                countText: anc.module || "",
                expandable: true,
                onExpand: async (inhBox, inhDepth) => {
                  const am = await (await fetch(`/api/tree/class-members?class_name=${encodeURIComponent(anc.name)}&owner_id=${anc.id}&limit=350`)).json();
                  inhBox.innerHTML = "";
                  for (const m of am.items) {
                    if (m.name === anc.name || m.name === "~" + anc.name) continue;
                    inhBox.appendChild(createMemberNode(m, inhDepth));
                  }
                  if (!inhBox.children.length) {
                    inhBox.innerHTML = `<div class="tree-loading" style="padding-left:${14 + inhDepth * 11}px">(无公开成员)</div>`;
                  }
                },
              })
            );
          }
        }
        if (!box.children.length) {
          box.innerHTML = `<div class="tree-loading" style="padding-left:${14 + d * 11}px">(无公开成员)</div>`;
        }
      },
    });
  }

  function createMemberNode(m, depth) {
    return makeNode({
      key: `sym:${m.id}`,
      depth,
      badgeHtml: App.kindBadge(m.kind, false),
      label: m.kind === "function" ? `${m.name}()` : m.name,
      countText: m.return_type ? m.return_type.slice(0, 18) : "",
      expandable: false,
      onClick: (row) => {
        clearSelected();
        row.classList.add("selected");
        App.openSymbol(m.id, false);
      },
    });
  }

  /* ================= CLASS INHERITANCE HIERARCHY MODE ================= */
  async function renderHierarchyRoots() {
    const root = treeEl();
    root.innerHTML = `<div class="tree-loading">正在加载 UE 5.8 类继承树...</div>`;
    const res = await (await fetch("/api/hierarchy/roots")).json();
    root.innerHTML = "";
    for (const r of res.items) {
      root.appendChild(createTypeNode(r, 0, true));
    }
    setStatus("UE 5.8 类继承层次树 (含子类与成员函数)");
  }

  /* ================= AUTO-LOCATE IN SIDEBAR ================= */
  App.locateInSidebar = async function (symbolId) {
    try {
      const locRes = await fetch(`/api/locate/${symbolId}`);
      if (!locRes.ok) return;
      const loc = await locRes.json();

      if (currentMode === "module") {
        await locateInModuleTree(loc);
      } else {
        await locateInHierarchyTree(loc);
      }
      const ownerPrefix = loc.owner_name && loc.owner_name !== loc.symbol_name ? `${loc.owner_name}::` : "";
      setStatus(`已定位: ${loc.category} > ${loc.module} > ${ownerPrefix}${loc.symbol_name}`);
    } catch (e) {
      console.error("Locate failed:", e);
    }
  };

  async function locateInModuleTree(loc) {
    const root = treeEl();
    // 1. Expand Category
    const catNode = root.querySelector(`[data-node-key="cat:${CSS.escape(loc.category)}"]`);
    if (!catNode) return;
    await catNode._expand(true);

    // 2. Expand Module
    const modKey = `mod:${loc.category}:${loc.module}`;
    const modNode = catNode.querySelector(`[data-node-key="${CSS.escape(modKey)}"]`);
    if (!modNode) return;
    await modNode._expand(true);

    // 3. Locate Owner Class (or the type itself)
    const ownerId = loc.owner_id || loc.symbol_id;
    let typeNode = modNode.querySelector(`[data-node-key="sym:${ownerId}"]`);
    if (!typeNode && loc.owner_name) {
      // Class may be beyond initial 160-item page -> inject directly at top of module's children so it's always locatable!
      const ownerDet = await (await fetch(`/api/symbol/${ownerId}`)).json();
      const modBox = modNode.querySelector(":scope > .tree-children");
      typeNode = createTypeNode(
        {
          id: ownerDet.id,
          name: ownerDet.name,
          kind: ownerDet.kind,
          parent_name: ownerDet.parent_name,
          func_count: (ownerDet.members?.functions || []).length || 1,
        },
        2,
        false
      );
      modBox.prepend(typeNode);
    }

    if (!typeNode) return;
    if (loc.symbol_id === ownerId) {
      await typeNode._expand(true);
      highlightNode(typeNode);
      return;
    }

    // 4. Expand Owner Class & highlight exact function/property!
    await typeNode._expand(true);
    let memNode = typeNode.querySelector(`[data-node-key="sym:${loc.symbol_id}"]`);
    if (!memNode) {
      const symDet = await (await fetch(`/api/symbol/${loc.symbol_id}`)).json();
      memNode = createMemberNode(symDet, 3);
      typeNode.querySelector(":scope > .tree-children").prepend(memNode);
    }
    highlightNode(memNode);
  }

  async function locateInHierarchyTree(loc) {
    const root = treeEl();
    const chain = loc.inheritance_chain || [];
    if (!chain.length) {
      // Fallback to module mode if symbol has no class hierarchy (e.g. free enum/function)
      switchMode("module");
      await locateInModuleTree(loc);
      return;
    }

    let container = root;
    let lastNode = null;
    for (let i = 0; i < chain.length; i++) {
      const step = chain[i];
      if (!step.id) continue;
      let node = container.querySelector(`[data-node-key="sym:${step.id}"]`);
      if (!node) {
        node = createTypeNode(
          { id: step.id, name: step.name, kind: step.kind || "class", child_count: 1, func_count: 1 },
          i,
          true
        );
        container.prepend(node);
      }
      await node._expand(true);
      container = node.querySelector(":scope > .tree-children");
      lastNode = node;
    }

    if (!lastNode) return;
    if (loc.symbol_id === (loc.owner_id || lastNode.dataset.nodeKey.replace("sym:", ""))) {
      highlightNode(lastNode);
      return;
    }
    let memNode = container.querySelector(`[data-node-key="sym:${loc.symbol_id}"]`);
    if (!memNode) {
      const symDet = await (await fetch(`/api/symbol/${loc.symbol_id}`)).json();
      memNode = createMemberNode(symDet, chain.length);
      container.prepend(memNode);
    }
    highlightNode(memNode);
  }

  function switchMode(mode) {
    if (currentMode === mode) return;
    currentMode = mode;
    document.getElementById("tabModule").classList.toggle("active", mode === "module");
    document.getElementById("tabHierarchy").classList.toggle("active", mode === "hierarchy");
    const done = mode === "module" ? renderModuleRoots() : renderHierarchyRoots();
    done.then(() => {
      if (App.currentSymbolId) App.locateInSidebar(App.currentSymbolId);
    });
  }

  /* ================= SIDEBAR FILTER & SPLITTER ================= */
  function initTreeControls() {
    document.getElementById("tabModule").addEventListener("click", () => switchMode("module"));
    document.getElementById("tabHierarchy").addEventListener("click", () => switchMode("hierarchy"));

    document.getElementById("btnCollapseAll").addEventListener("click", () => {
      treeEl().querySelectorAll(".tree-children.open").forEach((c) => c.classList.remove("open"));
      treeEl().querySelectorAll(".tree-caret.open").forEach((c) => c.classList.remove("open"));
    });

    const filterInput = document.getElementById("treeFilterInput");
    filterInput.addEventListener("input", () => {
      const kw = filterInput.value.trim().toLowerCase();
      treeEl().querySelectorAll(".tree-node").forEach((node) => {
        if (!kw) {
          node.style.display = "";
          return;
        }
        const txt = node.textContent.toLowerCase();
        node.style.display = txt.includes(kw) ? "" : "none";
      });
    });

    // Draggable sidebar splitter
    const splitter = document.getElementById("splitter");
    const sidebar = document.getElementById("sidebar");
    let dragging = false;
    splitter.addEventListener("mousedown", () => {
      dragging = true;
      splitter.classList.add("dragging");
      document.body.style.userSelect = "none";
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const w = Math.max(230, Math.min(window.innerWidth * 0.55, e.clientX));
      sidebar.style.width = `${w}px`;
    });
    window.addEventListener("mouseup", () => {
      if (!dragging) return;
      dragging = false;
      splitter.classList.remove("dragging");
      document.body.style.userSelect = "";
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTreeControls();
    renderModuleRoots();
  });
})(window.UEApp);
