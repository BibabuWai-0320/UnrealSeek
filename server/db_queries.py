"""Database query layer for UE 5.8 API tree, inheritance hierarchy, auto-locate, and details."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from .zh_translator import describe_param_zh, describe_return_zh, describe_symbol_zh

PRIORITY_MODULES = [
    "Engine", "Core", "CoreUObject", "UMG", "Slate", "SlateCore",
    "InputCore", "AIModule", "NavigationSystem", "PhysicsCore",
    "EnhancedInput", "Niagara", "GameplayAbilities", "GameplayTags",
    "PCG", "UnrealEd", "RenderCore", "RHI",
]

DEFAULT_HIERARCHY_ROOTS = [
    "UObjectBase", "SWidget", "IInterface", "FGCObject",
    "FTickFunction", "FComponentVisualizer", "IModuleInterface",
    "FRunnable", "FStreamableManager",
]


class ApiRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def get_stats(self) -> Dict[str, Any]:
        conn = self._connect()
        try:
            meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta_info")}
            cats = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT category, COUNT(*) AS module_count,
                           SUM(class_count) AS class_count,
                           SUM(struct_count) AS struct_count,
                           SUM(enum_count) AS enum_count,
                           SUM(func_count) AS func_count,
                           SUM(total_count) AS total_count
                    FROM modules
                    GROUP BY category
                    ORDER BY CASE category
                        WHEN 'Runtime' THEN 1
                        WHEN 'Plugins' THEN 2
                        WHEN 'Editor' THEN 3
                        WHEN 'Developer' THEN 4
                        WHEN 'Programs' THEN 5
                        ELSE 6 END
                    """
                )
            ]
            kinds = {
                r["kind"]: r["cnt"]
                for r in conn.execute("SELECT kind, COUNT(*) AS cnt FROM symbols GROUP BY kind")
            }
            return {"meta": meta, "categories": cats, "kinds": kinds}
        finally:
            conn.close()

    def get_modules(self, category: str = "", q: str = "", limit: int = 300) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            clauses = []
            params: List[Any] = []
            if category:
                clauses.append("category = ?")
                params.append(category)
            if q:
                clauses.append("(LOWER(module) LIKE ? OR LOWER(plugin_name) LIKE ?)")
                like = f"%{q.lower()}%"
                params.extend([like, like])
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM modules {where} ORDER BY total_count DESC, module ASC LIMIT ?",
                [*params, limit],
            ).fetchall()
            items = [dict(r) for r in rows]
            # Sort priority modules first, then alphabetical
            prio = {m: i for i, m in enumerate(PRIORITY_MODULES)}
            items.sort(key=lambda x: (prio.get(x["module"], 999), x["module"].lower()))
            return items
        finally:
            conn.close()

    def get_module_types(
        self,
        category: str,
        module: str,
        filter_q: str = "",
        public_only: bool = False,
        offset: int = 0,
        limit: int = 200,
    ) -> Dict[str, Any]:
        conn = self._connect()
        try:
            clauses = ["s.category = ?", "s.module = ?", "s.kind IN ('class','struct','interface','enum')"]
            params: List[Any] = [category, module]
            if public_only:
                clauses.append("s.visibility = 'Public'")
            if filter_q:
                clauses.append("s.name_lower LIKE ?")
                params.append(f"%{filter_q.lower()}%")
            where = " AND ".join(clauses)

            total = conn.execute(f"SELECT COUNT(*) FROM symbols s WHERE {where}", params).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT s.id, s.name, s.qualified_name, s.kind, s.parent_name,
                       s.visibility, s.is_blueprint, s.is_deprecated, s.doc_summary,
                       h.include_path, h.rel_path,
                       (SELECT COUNT(*) FROM symbols m
                        WHERE m.owner_lower = s.name_lower AND m.kind = 'function') AS func_count
                FROM symbols s
                JOIN headers h ON h.id = s.header_id
                WHERE {where}
                ORDER BY
                    CASE s.visibility WHEN 'Public' THEN 0 WHEN 'Internal' THEN 1 ELSE 2 END,
                    CASE s.kind WHEN 'class' THEN 0 WHEN 'interface' THEN 1 WHEN 'struct' THEN 2 ELSE 3 END,
                    s.name_lower ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
            return {"total": total, "items": [dict(r) for r in rows]}
        finally:
            conn.close()

    def get_class_members(
        self,
        class_name: str,
        owner_id: Optional[int] = None,
        filter_q: str = "",
        limit: int = 500,
    ) -> Dict[str, Any]:
        conn = self._connect()
        try:
            owner_row = None
            if owner_id:
                owner_row = conn.execute(
                    "SELECT id, name, header_id, parent_name, module, category FROM symbols WHERE id = ?",
                    (owner_id,),
                ).fetchone()
            if not owner_row and class_name:
                owner_row = self._find_primary_type(conn, class_name)

            target_name = owner_row["name"] if owner_row else class_name
            target_low = target_name.lower()
            header_id = owner_row["header_id"] if owner_row else None

            clauses = ["s.owner_lower = ?", "s.kind IN ('function','property','delegate')"]
            params: List[Any] = [target_low]
            if header_id:
                # Prefer same header if multiple classes share a name across plugins
                same_cnt = conn.execute(
                    "SELECT COUNT(*) FROM symbols WHERE owner_lower = ? AND header_id = ?",
                    (target_low, header_id),
                ).fetchone()[0]
                if same_cnt > 0:
                    clauses.append("s.header_id = ?")
                    params.append(header_id)
            if filter_q:
                clauses.append("s.name_lower LIKE ?")
                params.append(f"%{filter_q.lower()}%")

            rows = conn.execute(
                f"""
                SELECT s.id, s.name, s.kind, s.access, s.return_type, s.params_raw,
                       s.param_count, s.qualifiers, s.is_blueprint, s.is_deprecated,
                       s.doc_summary, s.line_start, h.include_path
                FROM symbols s
                JOIN headers h ON h.id = s.header_id
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE s.kind WHEN 'function' THEN 0 WHEN 'property' THEN 1 ELSE 2 END,
                    CASE s.access WHEN 'public' THEN 0 WHEN 'protected' THEN 1 ELSE 2 END,
                    s.name_lower ASC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

            ancestors = []
            if owner_row and owner_row["parent_name"]:
                chain = self._build_inheritance_chain(conn, owner_row["parent_name"])
                ancestors = [a for a in reversed(chain) if a.get("id")]

            return {
                "class_name": target_name,
                "parent_name": owner_row["parent_name"] if owner_row else "",
                "ancestors": ancestors,
                "items": [dict(r) for r in rows],
            }
        finally:
            conn.close()

    def get_hierarchy_roots(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            roots: List[Dict[str, Any]] = []
            seen = set()
            for rname in DEFAULT_HIERARCHY_ROOTS:
                row = self._find_primary_type(conn, rname)
                if row and row["name_lower"] not in seen:
                    seen.add(row["name_lower"])
                    child_cnt = conn.execute(
                        "SELECT COUNT(DISTINCT name_lower) FROM symbols WHERE parent_lower = ? AND kind IN ('class','struct','interface')",
                        (row["name_lower"],),
                    ).fetchone()[0]
                    d = dict(row)
                    d["child_count"] = child_cnt
                    roots.append(d)

            # Also append top most-inherited base classes not yet listed
            top_parents = conn.execute(
                """
                SELECT parent_lower, COUNT(DISTINCT name_lower) AS cnt
                FROM symbols
                WHERE parent_lower != '' AND kind IN ('class','struct','interface')
                GROUP BY parent_lower
                ORDER BY cnt DESC
                LIMIT 40
                """
            ).fetchall()
            for tp in top_parents:
                pl = tp["parent_lower"]
                if pl in seen:
                    continue
                row = self._find_primary_type(conn, pl)
                if row and not row["parent_name"]:
                    seen.add(pl)
                    d = dict(row)
                    d["child_count"] = tp["cnt"]
                    roots.append(d)
                if len(roots) >= 25:
                    break
            return roots
        finally:
            conn.close()

    def get_hierarchy_children(
        self, parent_name: str, filter_q: str = "", limit: int = 200, offset: int = 0
    ) -> Dict[str, Any]:
        conn = self._connect()
        try:
            pl = parent_name.lower()
            clauses = ["s.parent_lower = ?", "s.kind IN ('class','struct','interface')"]
            params: List[Any] = [pl]
            if filter_q:
                clauses.append("s.name_lower LIKE ?")
                params.append(f"%{filter_q.lower()}%")
            where = " AND ".join(clauses)

            rows = conn.execute(
                f"""
                SELECT s.id, s.name, s.kind, s.parent_name, s.module, s.category,
                       s.visibility, s.doc_summary, h.include_path,
                       (SELECT COUNT(DISTINCT c.name_lower) FROM symbols c
                        WHERE c.parent_lower = s.name_lower AND c.kind IN ('class','struct','interface')) AS child_count,
                       (SELECT COUNT(*) FROM symbols m
                        WHERE m.owner_lower = s.name_lower AND m.kind = 'function') AS func_count
                FROM symbols s
                JOIN headers h ON h.id = s.header_id
                WHERE {where}
                GROUP BY s.name_lower
                ORDER BY child_count DESC, s.popularity DESC, s.name_lower ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
            return {"parent": parent_name, "items": [dict(r) for r in rows]}
        finally:
            conn.close()

    def _find_primary_type(self, conn: sqlite3.Connection, name: str) -> Optional[sqlite3.Row]:
        return conn.execute(
            """
            SELECT s.id, s.header_id, s.name, s.name_lower, s.qualified_name, s.kind,
                   s.parent_name, s.parent_lower, s.base_classes, s.module, s.category,
                   s.visibility, s.doc_summary, h.include_path, h.rel_path
            FROM symbols s
            JOIN headers h ON h.id = s.header_id
            WHERE s.name_lower = ? AND s.kind IN ('class','struct','interface','enum')
            ORDER BY (s.visibility = 'Public' AND h.include_path != '') DESC, s.popularity DESC
            LIMIT 1
            """,
            (name.lower(),),
        ).fetchone()

    def _build_inheritance_chain(self, conn: sqlite3.Connection, start_class: str) -> List[Dict[str, Any]]:
        """Return inheritance chain ordered from root ancestor -> start_class."""
        chain: List[Dict[str, Any]] = []
        visited = set()
        cur_name = start_class
        while cur_name and cur_name.lower() not in visited and len(chain) < 16:
            visited.add(cur_name.lower())
            row = self._find_primary_type(conn, cur_name)
            if row:
                chain.append({
                    "id": row["id"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "module": row["module"],
                    "category": row["category"],
                    "include_path": row["include_path"],
                    "parent_name": row["parent_name"],
                })
                cur_name = row["parent_name"]
            else:
                chain.append({
                    "id": None,
                    "name": cur_name,
                    "kind": "class",
                    "module": "",
                    "category": "",
                    "include_path": "",
                    "parent_name": "",
                })
                break
        chain.reverse()
        return chain

    def get_symbol_detail(self, symbol_id: int) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT s.*, h.rel_path, h.abs_path, h.include_path, h.plugin_name, h.file_name
                FROM symbols s
                JOIN headers h ON h.id = s.header_id
                WHERE s.id = ?
                """,
                (symbol_id,),
            ).fetchone()
            if not row:
                return None

            data = dict(row)
            data["params"] = json.loads(data["params_json"]) if data["params_json"] else []
            data["base_classes_list"] = json.loads(data["base_classes"]) if data["base_classes"] else []
            data["doc_see_list"] = json.loads(data["doc_see"]) if data["doc_see"] else []

            # Owner class info & inheritance chain
            owner_info = None
            target_class_for_chain = ""
            if data["kind"] in ("class", "struct", "interface"):
                target_class_for_chain = data["name"]
            elif data["owner_name"]:
                ow = self._find_primary_type(conn, data["owner_name"])
                if ow:
                    owner_info = dict(ow)
                target_class_for_chain = data["owner_name"]

            data["owner_info"] = owner_info
            data["inheritance_chain"] = (
                self._build_inheritance_chain(conn, target_class_for_chain)
                if target_class_for_chain
                else []
            )

            # Sibling overloads for functions
            overloads = []
            if data["kind"] == "function":
                ov_rows = conn.execute(
                    """
                    SELECT id, name, return_type, params_raw, qualifiers, signature, line_start, doc_summary
                    FROM symbols
                    WHERE name_lower = ? AND owner_lower = ? AND kind = 'function' AND header_id = ?
                    ORDER BY line_start ASC
                    LIMIT 20
                    """,
                    (data["name_lower"], data["owner_lower"], data["header_id"]),
                ).fetchall()
                overloads = [dict(r) for r in ov_rows]
            data["overloads"] = overloads

            # Derived classes + members (own AND inherited from parent classes!) if class/struct/interface
            derived = []
            members = {"functions": [], "properties": [], "delegates": []}
            inherited_groups: List[Dict[str, Any]] = []
            if data["kind"] in ("class", "struct", "interface"):
                d_rows = conn.execute(
                    """
                    SELECT id, name, kind, module, category, doc_summary
                    FROM symbols
                    WHERE parent_lower = ? AND kind IN ('class','struct','interface')
                    GROUP BY name_lower
                    ORDER BY popularity DESC, name_lower ASC
                    LIMIT 80
                    """,
                    (data["name_lower"],),
                ).fetchall()
                derived = [dict(r) for r in d_rows]

                m_rows = conn.execute(
                    """
                    SELECT id, name, kind, access, return_type, params_raw, qualifiers,
                           is_blueprint, is_deprecated, doc_summary, line_start
                    FROM symbols
                    WHERE owner_lower = ? AND header_id = ? AND kind IN ('function','property','delegate')
                    ORDER BY
                        CASE access WHEN 'public' THEN 0 WHEN 'protected' THEN 1 ELSE 2 END,
                        name_lower ASC
                    LIMIT 600
                    """,
                    (data["name_lower"], data["header_id"]),
                ).fetchall()
                seen_fns = set()
                seen_props = set()
                for mr in m_rows:
                    md = dict(mr)
                    md["declared_in"] = data["name"]
                    md["declared_in_id"] = data["id"]
                    if md["kind"] == "function":
                        members["functions"].append(md)
                        seen_fns.add((md["name"].lower(), md["params_raw"]))
                    elif md["kind"] == "property":
                        members["properties"].append(md)
                        seen_props.add(md["name"].lower())
                    else:
                        members["delegates"].append(md)

                # Walk ancestor classes (nearest parent -> root) and collect inherited public/protected members!
                ancestors = [a for a in reversed(data["inheritance_chain"][:-1]) if a.get("id")]
                for anc in ancestors:
                    anc_row = conn.execute(
                        "SELECT id, name, header_id FROM symbols WHERE id = ?", (anc["id"],)
                    ).fetchone()
                    if not anc_row:
                        continue
                    anc_m_rows = conn.execute(
                        """
                        SELECT id, name, kind, access, return_type, params_raw, qualifiers,
                               is_blueprint, is_deprecated, doc_summary, line_start
                        FROM symbols
                        WHERE owner_lower = ? AND header_id = ?
                          AND kind IN ('function','property')
                          AND access IN ('public','protected')
                        ORDER BY
                            CASE access WHEN 'public' THEN 0 ELSE 1 END,
                            is_blueprint DESC,
                            popularity DESC,
                            name_lower ASC
                        LIMIT 450
                        """,
                        (anc_row["name"].lower(), anc_row["header_id"]),
                    ).fetchall()
                    g_fns = []
                    g_props = []
                    for ar in anc_m_rows:
                        ad = dict(ar)
                        # Skip parent constructors/destructors
                        if ad["name"] == anc_row["name"] or ad["name"] == "~" + anc_row["name"]:
                            continue
                        ad["declared_in"] = anc_row["name"]
                        ad["declared_in_id"] = anc_row["id"]
                        if ad["kind"] == "function":
                            key = (ad["name"].lower(), ad["params_raw"])
                            if key not in seen_fns:
                                seen_fns.add(key)
                                g_fns.append(ad)
                        elif ad["kind"] == "property":
                            key = ad["name"].lower()
                            if key not in seen_props:
                                seen_props.add(key)
                                g_props.append(ad)
                    if g_fns or g_props:
                        inherited_groups.append({
                            "from_class": anc_row["name"],
                            "from_id": anc_row["id"],
                            "include_path": anc.get("include_path", ""),
                            "module": anc.get("module", ""),
                            "functions": g_fns,
                            "properties": g_props,
                        })

            data["derived_classes"] = derived
            data["members"] = members
            data["inherited_members"] = inherited_groups

            # Attach plain-Chinese explanations for symbol, params, return value, and all own + inherited members!
            data["doc_zh"] = describe_symbol_zh(
                name=data["name"],
                kind=data["kind"],
                owner_name=data["owner_name"],
                return_type=data["return_type"],
                params=data["params"],
                parent_name=data["parent_name"],
                is_blueprint=data["is_blueprint"],
                is_deprecated=data["is_deprecated"],
                doc_summary=data["doc_summary"],
            )
            data["return_zh"] = describe_return_zh(data["return_type"], data["kind"])
            if data["kind"] == "function":
                for p in data["params"]:
                    p["doc_zh"] = describe_param_zh(p)
            for grp_key in ("functions", "properties", "delegates"):
                for m in members[grp_key]:
                    m["doc_zh"] = describe_symbol_zh(
                        name=m["name"],
                        kind=m["kind"],
                        owner_name=data["name"],
                        return_type=m.get("return_type", ""),
                        is_blueprint=m.get("is_blueprint", 0),
                        is_deprecated=m.get("is_deprecated", 0),
                        doc_summary=m.get("doc_summary", ""),
                    )
            for ig in inherited_groups:
                fc = ig["from_class"]
                for m in ig["functions"]:
                    m["doc_zh"] = describe_symbol_zh(
                        name=m["name"],
                        kind="function",
                        owner_name=fc,
                        return_type=m.get("return_type", ""),
                        is_blueprint=m.get("is_blueprint", 0),
                        is_deprecated=m.get("is_deprecated", 0),
                        doc_summary=m.get("doc_summary", ""),
                    )
                for m in ig["properties"]:
                    m["doc_zh"] = describe_symbol_zh(
                        name=m["name"],
                        kind="property",
                        owner_name=fc,
                        return_type=m.get("return_type", ""),
                        is_blueprint=m.get("is_blueprint", 0),
                        is_deprecated=m.get("is_deprecated", 0),
                        doc_summary=m.get("doc_summary", ""),
                    )

            # Source snippet from local UE 5.8 header
            data["source_snippet"] = self._read_source_snippet(
                data["abs_path"], int(data["line_start"]), int(data["line_end"])
            )
            # Don't expose raw disk path redundantly if not needed, keep rel_path clean
            return data
        finally:
            conn.close()

    def resolve_symbol_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Resolve a C++ type/symbol name (stripped of *, &, const, template args) to a symbol id."""
        clean = name.strip()
        for prefix in ("const ", "class ", "struct ", "enum ", "typename "):
            if clean.startswith(prefix):
                clean = clean[len(prefix) :].strip()
        clean = clean.split("<", 1)[0].rstrip("*& ").strip()
        if "::" in clean:
            clean = clean.rsplit("::", 1)[-1]
        if not clean:
            return None
        conn = self._connect()
        try:
            row = self._find_primary_type(conn, clean)
            if row:
                return {"id": row["id"], "name": row["name"], "kind": row["kind"]}
            r2 = conn.execute(
                "SELECT id, name, kind FROM symbols WHERE name_lower = ? ORDER BY popularity DESC LIMIT 1",
                (clean.lower(),),
            ).fetchone()
            return dict(r2) if r2 else None
        finally:
            conn.close()

    def locate_symbol(self, symbol_id: int) -> Optional[Dict[str, Any]]:
        """Return exact tree paths (module tree + inheritance tree) to auto-locate a symbol."""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT s.id, s.name, s.kind, s.owner_name, s.owner_lower, s.header_id,
                       s.category, s.module, s.parent_name
                FROM symbols s
                WHERE s.id = ?
                """,
                (symbol_id,),
            ).fetchone()
            if not row:
                return None

            owner_id = None
            owner_name = row["owner_name"]
            owner_category = row["category"]
            owner_module = row["module"]

            if row["kind"] in ("class", "struct", "interface", "enum"):
                owner_id = row["id"]
                owner_name = row["name"]
            elif owner_name:
                ow = conn.execute(
                    """
                    SELECT id, name, category, module FROM symbols
                    WHERE name_lower = ? AND header_id = ? AND kind IN ('class','struct','interface')
                    LIMIT 1
                    """,
                    (row["owner_lower"], row["header_id"]),
                ).fetchone()
                if not ow:
                    ow = self._find_primary_type(conn, owner_name)
                if ow:
                    owner_id = ow["id"]
                    owner_name = ow["name"]
                    owner_category = ow["category"]
                    owner_module = ow["module"]

            chain = self._build_inheritance_chain(conn, owner_name) if owner_name else []
            return {
                "symbol_id": row["id"],
                "symbol_name": row["name"],
                "kind": row["kind"],
                "category": owner_category,
                "module": owner_module,
                "owner_id": owner_id,
                "owner_name": owner_name,
                "inheritance_chain": chain,
            }
        finally:
            conn.close()

    @staticmethod
    def _read_source_snippet(abs_path: str, line_start: int, line_end: int) -> Dict[str, Any]:
        if not abs_path or not os.path.exists(abs_path):
            return {"start_line": 1, "highlight_start": line_start, "highlight_end": line_end, "lines": []}
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
            s_idx = max(0, line_start - 10)
            e_idx = min(len(all_lines), max(line_end + 8, line_start + 16))
            if e_idx - s_idx > 45:
                e_idx = s_idx + 45
            return {
                "start_line": s_idx + 1,
                "highlight_start": line_start,
                "highlight_end": min(line_end, e_idx),
                "lines": [l.rstrip("\r\n") for l in all_lines[s_idx:e_idx]],
            }
        except OSError:
            return {"start_line": 1, "highlight_start": line_start, "highlight_end": line_end, "lines": []}
