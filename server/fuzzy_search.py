"""Multi-stage SQLite FTS5 + code-aware fuzzy search engine for UE 5.8 API docs."""

from __future__ import annotations

import json
import sqlite3
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

from .code_query import ParsedQuery, WordSegmenter, parse_code_query
from .zh_translator import describe_symbol_zh

STOP_WORDS = {"and", "or", "of", "to", "in", "at", "by", "for", "the", "a", "an", "with", "from"}

SYMBOL_SELECT_COLS = """
    s.id, s.header_id, s.name, s.name_lower, s.qualified_name, s.kind,
    s.owner_name, s.owner_lower, s.parent_name, s.access, s.return_type,
    s.params_raw, s.param_count, s.required_param_count, s.qualifiers,
    s.macro_kind, s.is_blueprint, s.is_deprecated, s.signature,
    s.doc_summary, s.line_start, s.tokens, s.initials,
    s.category, s.module, s.visibility, s.popularity,
    h.include_path, h.rel_path, h.plugin_name
"""


def _fts_quote(term: str) -> str:
    safe = term.replace('"', "")
    return f'"{safe}"'


def _is_subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(ch in it for ch in needle)


def _subsequence_ratio(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0
    matched = 0
    pos = 0
    for ch in needle:
        idx = haystack.find(ch, pos)
        if idx != -1:
            matched += 1
            pos = idx + 1
    return matched / len(needle)


class FuzzySearchEngine:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.segmenter = self._load_segmenter()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_segmenter(self) -> WordSegmenter:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT word, freq FROM vocab").fetchall()
            vocab = {r["word"]: int(r["freq"]) for r in rows}
        finally:
            conn.close()
        return WordSegmenter(vocab)

    def search(
        self,
        raw_query: str,
        limit: int = 40,
        kind: str = "",
        category: str = "",
        module: str = "",
        public_only: bool = False,
    ) -> Dict[str, Any]:
        pq = parse_code_query(raw_query, self.segmenter)
        if not pq.target:
            return {"query": raw_query, "parsed": None, "results": []}

        conn = self._connect()
        try:
            cand_ids = self._gather_candidates(conn, pq, kind, category, module, public_only)
            if not cand_ids:
                return {
                    "query": raw_query,
                    "parsed": self._parsed_dict(pq),
                    "results": [],
                }

            rows = self._fetch_rows(conn, list(cand_ids), kind, category, module, public_only)
        finally:
            conn.close()

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for r in rows:
            sc, reasons = self._score_row(r, pq)
            if sc <= 0:
                continue
            item = dict(r)
            item["score"] = round(sc, 1)
            item["match_reasons"] = reasons
            scored.append((sc, item))

        scored.sort(key=lambda x: (-x[0], len(x[1]["name"]), x[1]["name_lower"]))
        # Limit identical overload repeats in dropdown so diverse top symbols (e.g. FTimerHandle + SetTimer) all appear!
        max_per_key = 1 if pq.is_zh_query else 2
        seen_counts: Dict[Tuple[str, str, str], int] = {}
        results: List[Dict[str, Any]] = []
        for _, item in scored:
            k = (item["owner_lower"], item["name_lower"], item["kind"])
            c = seen_counts.get(k, 0)
            if c >= max_per_key:
                continue
            seen_counts[k] = c + 1
            results.append(item)
            if len(results) >= limit:
                break
        for item in results:
            item["doc_zh"] = describe_symbol_zh(
                name=item["name"],
                kind=item["kind"],
                owner_name=item["owner_name"],
                return_type=item["return_type"],
                parent_name=item["parent_name"],
                is_blueprint=item["is_blueprint"],
                is_deprecated=item["is_deprecated"],
                doc_summary=item["doc_summary"],
            )

        return {
            "query": raw_query,
            "parsed": self._parsed_dict(pq),
            "results": results,
        }

    def _parsed_dict(self, pq: ParsedQuery) -> Dict[str, Any]:
        return {
            "is_code_expr": pq.is_code_expr,
            "is_zh_query": pq.is_zh_query,
            "receiver": pq.receiver_raw,
            "receiver_hints": pq.receiver_hints,
            "operator": pq.operator,
            "target": pq.target,
            "words": pq.words,
            "arg_count": pq.arg_count,
            "explanation": pq.explanation,
        }

    def _gather_candidates(
        self,
        conn: sqlite3.Connection,
        pq: ParsedQuery,
        kind: str,
        category: str,
        module: str,
        public_only: bool,
    ) -> Set[int]:
        ids: Set[int] = set()
        t_low = pq.target_lower
        max_pool = 450

        filter_sql, filter_params = self._build_filters(kind, category, module, public_only, alias="s")

        # 0. Chinese intent boosted symbols (direct exact lookup in B-tree index!)
        if pq.is_zh_query and pq.zh_boost_symbols:
            for sym_ref in pq.zh_boost_symbols:
                if "::" in sym_ref:
                    ow, nm = sym_ref.split("::", 1)
                    q_zh = f"""
                        SELECT s.id FROM symbols s
                        WHERE s.owner_lower = ? AND s.name_lower = ? {filter_sql}
                        ORDER BY s.popularity DESC LIMIT 6
                    """
                    for r in conn.execute(q_zh, [ow.lower(), nm.lower(), *filter_params]):
                        ids.add(r[0])
                else:
                    q_zh = f"""
                        SELECT s.id FROM symbols s
                        WHERE s.name_lower = ? {filter_sql}
                        ORDER BY (s.kind IN ('class','struct','interface','enum')) DESC, s.popularity DESC LIMIT 8
                    """
                    for r in conn.execute(q_zh, [sym_ref.lower(), *filter_params]):
                        ids.add(r[0])

        # 1. Exact & Prefix match on name_lower
        if t_low and not pq.is_zh_query:
            prefix_hi = t_low + "\uffff"
            q = f"""
                SELECT s.id FROM symbols s
                WHERE s.name_lower >= ? AND s.name_lower <= ? {filter_sql}
                ORDER BY (s.name_lower = ?) DESC, s.popularity DESC
                LIMIT 160
            """
            for r in conn.execute(q, [t_low, prefix_hi, *filter_params, t_low]):
                ids.add(r[0])

            # 1b. Probe UE class prefixes (U/A/F/E/I/S) & Component suffix (e.g. `SpringArm` -> `USpringArmComponent`)
            if not pq.is_code_expr and len(t_low) >= 3:
                ue_cands = [
                    f"u{t_low}", f"a{t_low}", f"f{t_low}", f"e{t_low}", f"i{t_low}",
                    f"u{t_low}component", f"a{t_low}actor",
                ]
                ph = ",".join("?" for _ in ue_cands)
                q_ue = f"""
                    SELECT s.id FROM symbols s
                    WHERE s.name_lower IN ({ph}) AND s.kind IN ('class','struct','interface','enum') {filter_sql}
                    ORDER BY s.popularity DESC LIMIT 40
                """
                for r in conn.execute(q_ue, [*ue_cands, *filter_params]):
                    ids.add(r[0])
                # Also prefix probe for U<t_low>* / A<t_low>* / F<t_low>* classes
                for pfx in ("u", "a", "f"):
                    p_lo = pfx + t_low
                    p_hi = p_lo + "\uffff"
                    q_up = f"""
                        SELECT s.id FROM symbols s
                        WHERE s.name_lower >= ? AND s.name_lower <= ?
                          AND s.kind IN ('class','struct','interface') {filter_sql}
                        ORDER BY s.popularity DESC LIMIT 25
                    """
                    for r in conn.execute(q_up, [p_lo, p_hi, *filter_params]):
                        ids.add(r[0])

        # 2. If receiver hints exist, grab top matching members directly from hinted owner classes
        if pq.receiver_hints and pq.words:
            owner_lows = [h.lower() for h in pq.receiver_hints]
            placeholders = ",".join("?" for _ in owner_lows)
            q = f"""
                SELECT s.id FROM symbols s
                WHERE s.owner_lower IN ({placeholders}) {filter_sql}
                LIMIT 260
            """
            for r in conn.execute(q, [*owner_lows, *filter_params]):
                ids.add(r[0])

        # 3. FTS5 trigram substring search (fast for >= 3 chars)
        if len(t_low) >= 3 and len(ids) < max_pool:
            try:
                tri_q = _fts_quote(t_low)
                for r in conn.execute(
                    "SELECT rowid FROM fts_trigram WHERE name_lower MATCH ? LIMIT 180",
                    (tri_q,),
                ):
                    ids.add(r[0])
            except sqlite3.OperationalError:
                pass

        had_direct_hit = len(ids) > 0

        # 3b. Typo recovery BEFORE broad OR queries whenever exact/prefix/substring had 0 hits!
        if not had_direct_hit and len(t_low) >= 5:
            # Trimmed prefix catches end-of-word typos like `GetActorLocatoin` -> `getactorlocat*`
            for trim in (2, 3, 4):
                pref = t_low[:-trim]
                if len(pref) >= 4:
                    hi = pref + "\uffff"
                    q = f"""
                        SELECT s.id FROM symbols s
                        WHERE s.name_lower >= ? AND s.name_lower <= ? {filter_sql}
                        ORDER BY s.popularity DESC LIMIT 80
                    """
                    for r in conn.execute(q, [pref, hi, *filter_params]):
                        ids.add(r[0])
                    if len(ids) >= 25:
                        break

            # Trigram chunk pairs catch middle typos like `GetActroLocation` or `GetLocatonAndRotaion`
            chunks = self._extract_typo_chunks(t_low)
            suffix_chunk = t_low[-6:] if len(t_low) >= 8 else (t_low[-4:] if len(t_low) >= 5 else "")
            if chunks and suffix_chunk and suffix_chunk.isalnum():
                pair_q = f"{_fts_quote(chunks[0])} AND {_fts_quote(suffix_chunk)}"
                try:
                    for r in conn.execute(
                        "SELECT rowid FROM fts_trigram WHERE name_lower MATCH ? LIMIT 250",
                        (pair_q,),
                    ):
                        ids.add(r[0])
                except sqlite3.OperationalError:
                    pass
            if len(ids) < 30 and len(chunks) >= 2:
                pair_q2 = f"{_fts_quote(chunks[0])} AND {_fts_quote(chunks[-1])}"
                try:
                    for r in conn.execute(
                        "SELECT rowid FROM fts_trigram WHERE name_lower MATCH ? LIMIT 250",
                        (pair_q2,),
                    ):
                        ids.add(r[0])
                except sqlite3.OperationalError:
                    pass
            if len(ids) < 30 and chunks:
                tri_or = " OR ".join(_fts_quote(c) for c in chunks[:5])
                try:
                    for r in conn.execute(
                        "SELECT rowid FROM fts_trigram WHERE name_lower MATCH ? LIMIT 150",
                        (tri_or,),
                    ):
                        ids.add(r[0])
                except sqlite3.OperationalError:
                    pass

        # 4. FTS5 multi-word token search (handles broken-case like GetLocationandRotation & Chinese translated tokens!)
        words = [w for w in pq.words if len(w) >= 2]
        if pq.is_zh_query and words and len(ids) < max_pool:
            # For Chinese queries, pq.words may include synonyms (e.g. local/relative, actor/character/pawn).
            # Run targeted AND pairs of strong tokens + OR fallback so exact multi-concept APIs always surface!
            non_verb = [w for w in words if w not in ("get", "set", "add", "is", "has", "can") and w.isalnum()]
            verbs = [w for w in words if w in ("get", "set", "add", "remove", "clear", "find", "spawn", "create", "bind", "attach")]
            queries_fts: List[str] = []
            if len(non_verb) >= 2:
                # Try pairs combining first/last non-verb tokens with optional verb
                for idx_a in range(min(3, len(non_verb))):
                    for idx_b in range(idx_a + 1, min(6, len(non_verb))):
                        if verbs:
                            queries_fts.append(f"{verbs[0]}* AND {non_verb[idx_a]}* AND {non_verb[idx_b]}*")
                        queries_fts.append(f"{non_verb[idx_a]}* AND {non_verb[idx_b]}*")
            elif verbs and non_verb:
                queries_fts.append(f"{verbs[0]}* AND {non_verb[0]}*")
            for fq in queries_fts[:8]:
                try:
                    for r in conn.execute(
                        "SELECT rowid FROM fts_tokens WHERE tokens MATCH ? LIMIT 90",
                        (fq,),
                    ):
                        ids.add(r[0])
                except sqlite3.OperationalError:
                    pass
            if len(ids) < 60 and non_verb:
                fts_or = " OR ".join(f"{w}*" for w in non_verb[:6])
                try:
                    for r in conn.execute(
                        "SELECT rowid FROM fts_tokens WHERE tokens MATCH ? LIMIT 150",
                        (fts_or,),
                    ):
                        ids.add(r[0])
                except sqlite3.OperationalError:
                    pass
        elif words and len(ids) < max_pool:
            # 4a. All words AND prefix
            fts_all = " AND ".join(f"{w}*" for w in words[:8] if w.isalnum())
            if fts_all:
                try:
                    for r in conn.execute(
                        "SELECT rowid FROM fts_tokens WHERE tokens MATCH ? LIMIT 220",
                        (fts_all,),
                    ):
                        ids.add(r[0])
                except sqlite3.OperationalError:
                    pass

            # 4b. Content words (excluding stopwords) AND
            content_words = [w for w in words if w not in STOP_WORDS and w.isalnum()]
            if len(content_words) >= 2 and len(content_words) != len(words):
                fts_content = " AND ".join(f"{w}*" for w in content_words[:6])
                try:
                    for r in conn.execute(
                        "SELECT rowid FROM fts_tokens WHERE tokens MATCH ? LIMIT 180",
                        (fts_content,),
                    ):
                        ids.add(r[0])
                except sqlite3.OperationalError:
                    pass

            # 4c. Broaden if still sparse: drop action verb ('get'/'set') or match doc/owner too
            if len(ids) < 35 and len(content_words) >= 2:
                strong_words = [w for w in content_words if w not in ("get", "set", "is", "has", "add", "find")]
                if len(strong_words) >= 2:
                    fts_strong = " AND ".join(f"{w}*" for w in strong_words[:5])
                    try:
                        for r in conn.execute(
                            "SELECT rowid FROM fts_tokens WHERE tokens MATCH ? LIMIT 160",
                            (fts_strong,),
                        ):
                            ids.add(r[0])
                    except sqlite3.OperationalError:
                        pass

            # 4d. Still sparse -> OR across content words (for partial/missing words)
            if len(ids) < 25 and content_words:
                fts_or = " OR ".join(f"{w}*" for w in content_words[:5])
                try:
                    for r in conn.execute(
                        "SELECT rowid FROM fts_tokens WHERE fts_tokens MATCH ? LIMIT 180",
                        (fts_or,),
                    ):
                        ids.add(r[0])
                except sqlite3.OperationalError:
                    pass

        # 5. Acronym / CamelCase initials match (e.g. `gal` -> GetActorLocation)
        if 2 <= len(t_low) <= 8 and t_low.isalpha() and len(ids) < max_pool:
            hi = t_low + "\uffff"
            q = f"""
                SELECT s.id FROM symbols s
                WHERE s.initials >= ? AND s.initials <= ? {filter_sql}
                ORDER BY (s.initials = ?) DESC, s.popularity DESC
                LIMIT 60
            """
            for r in conn.execute(q, [t_low, hi, *filter_params, t_low]):
                ids.add(r[0])

        # 6. Typo-tolerant trigram chunk fallback (e.g. `GetActorLocatoin` -> chunks `geta`, `acto`, `loca`)
        if len(ids) < 20 and len(t_low) >= 5:
            chunks = self._extract_typo_chunks(t_low)
            if chunks:
                tri_or = " OR ".join(_fts_quote(c) for c in chunks[:6])
                try:
                    for r in conn.execute(
                        "SELECT rowid FROM fts_trigram WHERE name_lower MATCH ? LIMIT 200",
                        (tri_or,),
                    ):
                        ids.add(r[0])
                except sqlite3.OperationalError:
                    pass

        return ids

    @staticmethod
    def _extract_typo_chunks(s: str) -> List[str]:
        chunks: List[str] = []
        n = len(s)
        step = max(2, n // 4)
        for pos in range(0, n - 2, step):
            chunk = s[pos : pos + 4]
            if len(chunk) >= 3 and chunk.isalnum():
                chunks.append(chunk)
        if n >= 4 and s[-4:].isalnum() and s[-4:] not in chunks:
            chunks.append(s[-4:])
        return chunks

    @staticmethod
    def _build_filters(
        kind: str, category: str, module: str, public_only: bool, alias: str = "s"
    ) -> Tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if kind:
            if kind == "type":
                clauses.append(f"{alias}.kind IN ('class','struct','interface','enum')")
            else:
                clauses.append(f"{alias}.kind = ?")
                params.append(kind)
        if category:
            clauses.append(f"{alias}.category = ?")
            params.append(category)
        if module:
            clauses.append(f"{alias}.module = ?")
            params.append(module)
        if public_only:
            clauses.append(f"{alias}.visibility = 'Public'")
        if not clauses:
            return "", []
        return " AND " + " AND ".join(clauses), params

    def _fetch_rows(
        self,
        conn: sqlite3.Connection,
        cand_ids: List[int],
        kind: str,
        category: str,
        module: str,
        public_only: bool,
    ) -> List[sqlite3.Row]:
        filter_sql, filter_params = self._build_filters(kind, category, module, public_only, alias="s")
        # Cap to 600 candidates to guarantee <25ms latency
        limited = cand_ids[:600]
        placeholders = ",".join("?" for _ in limited)
        sql = f"""
            SELECT {SYMBOL_SELECT_COLS}
            FROM symbols s
            JOIN headers h ON h.id = s.header_id
            WHERE s.id IN ({placeholders}) {filter_sql}
        """
        return conn.execute(sql, [*limited, *filter_params]).fetchall()

    def _score_row(self, row: sqlite3.Row, pq: ParsedQuery) -> Tuple[float, List[str]]:
        name_low: str = row["name_lower"]
        owner_low: str = row["owner_lower"]
        sym_tokens = row["tokens"].split() if row["tokens"] else []
        sym_token_set = set(sym_tokens)
        initials: str = row["initials"]
        t_low = pq.target_lower

        score = 0.0
        reasons: List[str] = []

        # 0. Dedicated scoring path for Chinese / Mixed-Language semantic queries
        if pq.is_zh_query:
            full_ref = f"{row['owner_name']}::{row['name']}" if row["owner_name"] else row["name"]
            boost_lows = [b.lower() for b in pq.zh_boost_symbols]
            matched_boost = False
            if full_ref.lower() in boost_lows:
                b_idx = boost_lows.index(full_ref.lower())
                score += max(260.0, 440.0 - b_idx * 18.0)
                reasons.append("中文核心匹配")
                matched_boost = True
            elif name_low in boost_lows:
                b_idx = boost_lows.index(name_low)
                score += max(220.0, 390.0 - b_idx * 18.0)
                reasons.append("中文核心匹配")
                matched_boost = True

            # Match translated English tokens against symbol tokens & owner_name
            q_words = pq.words
            matched_words = 0
            owner_toks = set(owner_low.split()) if owner_low else set()
            for qw in q_words:
                if qw in sym_token_set:
                    matched_words += 1
                elif any(st.startswith(qw) for st in sym_token_set):
                    matched_words += 0.7
                elif owner_low and qw in owner_low:
                    matched_words += 0.45
            if matched_words > 0:
                score += matched_words * 115.0
                if matched_words >= 3:
                    reasons.append(f"多关键词匹配 ({int(matched_words)}词)")
                elif matched_words >= 2 and not matched_boost:
                    reasons.append("语义关键词匹配")
                # Compactness bonus
                extra = max(0, len(sym_tokens) - int(matched_words))
                score += max(0.0, 35.0 - extra * 5.0)

            # Action verb alignment (`设置` -> prefers Set*, `获取` -> prefers Get*, `添加` -> Add*)
            if q_words and sym_tokens:
                if sym_tokens[0] in q_words and sym_tokens[0] in ("set", "get", "add", "clear", "find", "spawn", "bind", "attach", "play"):
                    score += 85.0

            # Avoid random constructor / delegate / obscure K2_ outranking primary C++ API
            if row["kind"] == "function" and owner_low and name_low == owner_low:
                score -= 120.0
            if row["kind"] == "delegate" and not matched_boost:
                score -= 60.0

            if score < 110.0:
                return 0.0, []
            score += float(row["popularity"]) * 1.15
            return score, reasons[:3]

        # 1. Name exact / UE class prefix-stripped / prefix / substring
        is_type = row["kind"] in ("class", "struct", "interface", "enum")
        orig_name: str = row["name"]
        ue_stripped = ""
        if is_type and len(orig_name) >= 3 and orig_name[0] in "AUFEIST" and orig_name[1].isupper():
            ue_stripped = name_low[1:]

        if name_low == t_low:
            score += 520.0
            reasons.append("精确匹配")
        elif is_type and ue_stripped and ue_stripped == t_low:
            score += 545.0
            reasons.append("UE 类型匹配")
        elif is_type and ue_stripped and ue_stripped == t_low + "component":
            score += 560.0
            reasons.append("UE 组件类匹配")
        elif is_type and ue_stripped and ue_stripped.startswith(t_low):
            score += 365.0 + 45.0 * (len(t_low) / max(1, len(ue_stripped)))
            reasons.append("UE 类前缀匹配")
        elif name_low.startswith(t_low):
            score += 310.0 + 40.0 * (len(t_low) / max(1, len(name_low)))
            reasons.append("前缀匹配")
        elif t_low in name_low:
            score += 225.0 + 45.0 * (len(t_low) / max(1, len(name_low)))
            reasons.append("子串匹配")

        # 2. Multi-word token matching (handles `GetLocationandRotation` -> `GetTargetLocationAndRotation`!)
        q_words = pq.words
        if q_words and sym_tokens:
            exact_hits = 0
            prefix_hits = 0
            ordered_hits = 0
            last_idx = -1
            for qw in q_words:
                if qw in sym_token_set:
                    exact_hits += 1
                    # Track ordering
                    for idx, st in enumerate(sym_tokens):
                        if st == qw and idx > last_idx:
                            ordered_hits += 1
                            last_idx = idx
                            break
                elif any(st.startswith(qw) for st in sym_tokens):
                    prefix_hits += 1
            coverage = (exact_hits + 0.65 * prefix_hits) / len(q_words)
            if coverage > 0:
                score += coverage * 230.0
                if ordered_hits == len(q_words):
                    score += 55.0
                    reasons.append("全词顺序匹配")
                elif exact_hits == len(q_words):
                    score += 35.0
                    reasons.append("全词匹配")
                # Compactness bonus: prefer tighter names with fewer extra words
                extra = max(0, len(sym_tokens) - len(q_words))
                score += max(0.0, 40.0 - extra * 8.0)

        # 3. Fuzzy character similarity + subsequence match (great for typos!)
        sim = SequenceMatcher(None, t_low, name_low).ratio()
        score += sim * 145.0
        if len(t_low) >= 4 and _is_subsequence(t_low, name_low):
            score += 60.0
            if not reasons:
                reasons.append("模糊子序列匹配")
        elif sim >= 0.68 and not reasons:
            reasons.append(f"模糊相似度 {int(sim * 100)}%")

        # 4. Initials / acronym match
        if 2 <= len(t_low) <= 8 and initials:
            if initials == t_low:
                score += 180.0
                reasons.append("首字母缩写匹配")
            elif initials.startswith(t_low):
                score += 90.0

        # 5. Receiver class hint / qualified owner matching (prioritize hints[0] over secondary hints)
        if pq.receiver_hints:
            hint_lows = [h.lower() for h in pq.receiver_hints]
            if owner_low in hint_lows:
                h_idx = hint_lows.index(owner_low)
                score += max(180.0, 270.0 - h_idx * 40.0)
                reasons.append(f"类匹配 {row['owner_name']}")
            elif any(h in owner_low for h in hint_lows):
                score += 120.0
                reasons.append(f"相关类 {row['owner_name']}")
        elif pq.receiver_raw and len(pq.receiver_raw) >= 3:
            r_low = pq.receiver_raw.lower()
            if owner_low and (r_low == owner_low or r_low in owner_low):
                score += 150.0
                reasons.append(f"类匹配 {row['owner_name']}")

        # 6. Argument count match (when user pasted parentheses like `foo(a, b)`)
        if pq.arg_count is not None and row["kind"] == "function":
            req = int(row["required_param_count"])
            tot = int(row["param_count"])
            if req <= pq.arg_count <= tot:
                score += 65.0
                reasons.append(f"参数数量匹配 ({pq.arg_count})")
            else:
                diff = min(abs(pq.arg_count - req), abs(pq.arg_count - tot))
                score -= min(45.0, diff * 15.0)

        # 7. Code expression kind preference (`X->Foo` strongly prefers member functions)
        if pq.is_code_expr:
            if row["kind"] == "function":
                score += 75.0
                if pq.operator in ("->", ".") and row["owner_name"]:
                    score += 20.0
            elif row["kind"] == "property":
                score += 18.0
            else:
                score -= 40.0
        else:
            # Plain query: prioritize real UE classes/structs/enums over member properties & constructors
            if is_type and (name_low == t_low or ue_stripped in (t_low, t_low + "component")):
                score += 140.0
            elif is_type and ue_stripped and ue_stripped.startswith(t_low):
                score += 75.0
            elif row["kind"] == "property" and row["owner_name"] and name_low != t_low:
                score -= 60.0
            elif row["kind"] == "function" and owner_low and name_low == owner_low:
                score -= 45.0
            elif row["kind"] == "delegate" and name_low == t_low:
                score -= 30.0

        # 8. Filter out pure noise if text match is too weak
        if score < 70.0:
            return 0.0, []

        # 9. Tie-break by UE 5.8 API prominence (Public / Runtime / Engine / Documented)
        score += float(row["popularity"]) * 0.9

        return score, reasons[:3]
