"""Multiprocessing UE 5.8 C++ API header indexer -> SQLite + FTS5 database."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sqlite3
import sys
import time
from collections import Counter
from typing import Dict, List, Tuple

from .build_cs import build_module_table, resolve_header_with_ubt
from .core_aliases import enrich_core_header_symbols
from .cpp_parser import parse_header_file

SKIP_DIRS = {
    "ThirdParty",
    "Intermediate",
    "Binaries",
    "Content",
    "Resources",
    "Shaders",
    "Saved",
    "DerivedDataCache",
}

CORE_MODULES = {
    "Engine",
    "Core",
    "CoreUObject",
    "InputCore",
    "Slate",
    "SlateCore",
    "UMG",
    "AIModule",
    "NavigationSystem",
    "PhysicsCore",
    "EnhancedInput",
    "GameplayAbilities",
    "GameplayTags",
    "Niagara",
    "PCG",
    "RenderCore",
    "RHI",
}


def discover_headers(engine_root: str) -> List[str]:
    """Collect all UE 5.8 C++ header paths across Source and Plugins."""
    roots = [
        os.path.join(engine_root, "Source", "Runtime"),
        os.path.join(engine_root, "Source", "Editor"),
        os.path.join(engine_root, "Source", "Developer"),
        os.path.join(engine_root, "Source", "Programs"),
        os.path.join(engine_root, "Plugins"),
    ]
    headers: List[str] = []
    for base in roots:
        if not os.path.exists(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in files:
                if fn.endswith(".h") and not fn.endswith(".generated.h"):
                    headers.append(os.path.join(root, fn))
    headers.sort()
    return headers


_WORKER_MOD_TABLE: Dict[str, Dict[str, object]] = {}


def _init_worker(mod_table: Dict[str, Dict[str, object]]) -> None:
    global _WORKER_MOD_TABLE
    _WORKER_MOD_TABLE = mod_table


def _worker_parse_batch(args: Tuple[List[str], str]) -> List[Tuple[Dict[str, str], List[Dict[str, object]]]]:
    paths, engine_root = args
    out = []
    for fp in paths:
        meta = resolve_header_with_ubt(fp, engine_root, _WORKER_MOD_TABLE)
        _, syms = parse_header_file(fp, engine_root, precomputed_meta=meta)
        syms = enrich_core_header_symbols(meta, syms, engine_root)
        out.append((meta, syms))
    return out


def _compute_popularity(sym: Dict[str, object], meta: Dict[str, str]) -> int:
    score = 0
    vis = meta["visibility"]
    cat = meta["category"]
    mod = meta["module"]
    if vis == "Public":
        score += 30
    elif vis == "Internal":
        score += 5
    elif vis == "Private":
        score -= 15

    if mod in ("Engine", "Core", "CoreUObject"):
        score += 28
    elif mod in CORE_MODULES:
        score += 18

    if cat == "Runtime":
        score += 15
    elif cat == "Plugins":
        score += 6
    elif cat == "Editor":
        score += 4

    if sym["access"] == "public":
        score += 8
    elif sym["access"] == "private":
        score -= 8

    if sym["is_blueprint"]:
        score += 10
    if sym["doc_summary"]:
        score += 8
    if sym["is_deprecated"]:
        score -= 22
    name = str(sym["name"])
    if name.startswith("K2_"):
        score -= 3
    if name.startswith("_") or name.endswith("_Implementation") or name.endswith("_Validate"):
        score -= 12
    # Prefer top-level global UE types over nested/namespaced helper types (e.g. DynamicRenderScaling::TMap)
    if sym["kind"] in ("class", "struct", "interface", "enum") and sym["owner_qualified"]:
        score -= 35
    return score


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;
        PRAGMA cache_size = -131072;

        DROP TABLE IF EXISTS meta_info;
        DROP TABLE IF EXISTS modules;
        DROP TABLE IF EXISTS vocab;
        DROP TABLE IF EXISTS fts_tokens;
        DROP TABLE IF EXISTS fts_trigram;
        DROP TABLE IF EXISTS symbols;
        DROP TABLE IF EXISTS headers;

        CREATE TABLE meta_info (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE headers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path TEXT UNIQUE NOT NULL,
            abs_path TEXT NOT NULL,
            category TEXT NOT NULL,
            plugin_name TEXT NOT NULL,
            module TEXT NOT NULL,
            visibility TEXT NOT NULL,
            include_path TEXT NOT NULL,
            file_name TEXT NOT NULL
        );

        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            header_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            name_lower TEXT NOT NULL,
            qualified_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            owner_name TEXT NOT NULL,
            owner_lower TEXT NOT NULL,
            owner_qualified TEXT NOT NULL,
            parent_name TEXT NOT NULL,
            parent_lower TEXT NOT NULL,
            base_classes TEXT NOT NULL,
            access TEXT NOT NULL,
            return_type TEXT NOT NULL,
            params_json TEXT NOT NULL,
            params_raw TEXT NOT NULL,
            param_count INTEGER NOT NULL,
            required_param_count INTEGER NOT NULL,
            qualifiers TEXT NOT NULL,
            macro_kind TEXT NOT NULL,
            macro_specifiers TEXT NOT NULL,
            is_blueprint INTEGER NOT NULL,
            is_deprecated INTEGER NOT NULL,
            deprecated_msg TEXT NOT NULL,
            signature TEXT NOT NULL,
            doc_summary TEXT NOT NULL,
            doc_remarks TEXT NOT NULL,
            doc_return TEXT NOT NULL,
            doc_see TEXT NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            tokens TEXT NOT NULL,
            initials TEXT NOT NULL,
            category TEXT NOT NULL,
            module TEXT NOT NULL,
            visibility TEXT NOT NULL,
            popularity INTEGER NOT NULL
        );

        CREATE TABLE modules (
            category TEXT NOT NULL,
            module TEXT NOT NULL,
            plugin_name TEXT NOT NULL,
            class_count INTEGER NOT NULL DEFAULT 0,
            struct_count INTEGER NOT NULL DEFAULT 0,
            enum_count INTEGER NOT NULL DEFAULT 0,
            func_count INTEGER NOT NULL DEFAULT 0,
            total_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (category, module)
        );

        CREATE TABLE vocab (
            word TEXT PRIMARY KEY,
            freq INTEGER NOT NULL
        );
        """
    )


def create_indexes(conn: sqlite3.Connection) -> None:
    print("Creating B-tree indexes...", flush=True)
    t0 = time.time()
    conn.executescript(
        """
        CREATE INDEX idx_headers_cat_mod ON headers(category, module);
        CREATE INDEX idx_sym_header ON symbols(header_id);
        CREATE INDEX idx_sym_name_lower ON symbols(name_lower);
        CREATE INDEX idx_sym_initials ON symbols(initials);
        CREATE INDEX idx_sym_owner_kind ON symbols(owner_lower, kind, name_lower);
        CREATE INDEX idx_sym_parent_kind ON symbols(parent_lower, kind, name_lower);
        CREATE INDEX idx_sym_cat_mod_kind ON symbols(category, module, kind, name_lower);
        CREATE INDEX idx_sym_kind_name ON symbols(kind, name_lower);
        CREATE INDEX idx_sym_pop ON symbols(popularity DESC);
        """
    )
    print(f"B-tree indexes built in {time.time() - t0:.1f}s", flush=True)

    print("Aggregating module statistics...", flush=True)
    conn.execute(
        """
        INSERT INTO modules(category, module, plugin_name, class_count, struct_count, enum_count, func_count, total_count)
        SELECT
            s.category,
            s.module,
            MAX(h.plugin_name),
            SUM(CASE WHEN s.kind IN ('class', 'interface') THEN 1 ELSE 0 END),
            SUM(CASE WHEN s.kind = 'struct' THEN 1 ELSE 0 END),
            SUM(CASE WHEN s.kind = 'enum' THEN 1 ELSE 0 END),
            SUM(CASE WHEN s.kind = 'function' THEN 1 ELSE 0 END),
            COUNT(*)
        FROM symbols s
        JOIN headers h ON h.id = s.header_id
        GROUP BY s.category, s.module
        """
    )

    print("Building FTS5 token index...", flush=True)
    t1 = time.time()
    conn.execute(
        """
        CREATE VIRTUAL TABLE fts_tokens USING fts5(
            name,
            owner_name,
            tokens,
            doc_summary,
            content='symbols',
            content_rowid='id',
            tokenize='unicode61'
        )
        """
    )
    conn.execute("INSERT INTO fts_tokens(fts_tokens) VALUES('rebuild')")
    print(f"FTS5 token index built in {time.time() - t1:.1f}s", flush=True)

    print("Building FTS5 trigram index...", flush=True)
    t2 = time.time()
    conn.execute(
        """
        CREATE VIRTUAL TABLE fts_trigram USING fts5(
            name_lower,
            content='symbols',
            content_rowid='id',
            tokenize='trigram'
        )
        """
    )
    conn.execute("INSERT INTO fts_trigram(fts_trigram) VALUES('rebuild')")
    print(f"FTS5 trigram index built in {time.time() - t2:.1f}s", flush=True)
    conn.commit()


def build_database(engine_root: str, db_path: str, workers: int = 16) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    t_start = time.time()
    print(f"Scanning *.Build.cs module rules under {engine_root} ...", flush=True)
    mod_table = build_module_table(engine_root)
    print(f"Loaded {len(mod_table):,} UBT modules in {time.time() - t_start:.1f}s", flush=True)
    print(f"Discovering UE 5.8 headers under {engine_root} ...", flush=True)
    headers = discover_headers(engine_root)
    print(f"Found {len(headers):,} headers in {time.time() - t_start:.1f}s", flush=True)

    tmp_db = db_path + ".building"
    if os.path.exists(tmp_db):
        os.remove(tmp_db)

    conn = sqlite3.connect(tmp_db)
    init_schema(conn)

    batch_size = 150
    batches = [(headers[i : i + batch_size], engine_root) for i in range(0, len(headers), batch_size)]

    insert_header_sql = """
        INSERT INTO headers(rel_path, abs_path, category, plugin_name, module, visibility, include_path, file_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    insert_sym_sql = """
        INSERT INTO symbols(
            header_id, name, name_lower, qualified_name, kind,
            owner_name, owner_lower, owner_qualified, parent_name, parent_lower,
            base_classes, access, return_type, params_json, params_raw,
            param_count, required_param_count, qualifiers, macro_kind, macro_specifiers,
            is_blueprint, is_deprecated, deprecated_msg, signature,
            doc_summary, doc_remarks, doc_return, doc_see,
            line_start, line_end, tokens, initials,
            category, module, visibility, popularity
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """

    vocab_counter: Counter = Counter()
    total_headers = 0
    total_symbols = 0
    cur = conn.cursor()

    n_workers = max(1, min(workers, mp.cpu_count()))
    print(f"Parsing headers with {n_workers} worker processes...", flush=True)
    with mp.Pool(processes=n_workers, initializer=_init_worker, initargs=(mod_table,)) as pool:
        for batch_res in pool.imap_unordered(_worker_parse_batch, batches, chunksize=1):
            sym_rows = []
            for meta, syms in batch_res:
                cur.execute(
                    insert_header_sql,
                    (
                        meta["rel_path"],
                        meta["abs_path"],
                        meta["category"],
                        meta["plugin_name"],
                        meta["module"],
                        meta["visibility"],
                        meta["include_path"],
                        meta["file_name"],
                    ),
                )
                hid = cur.lastrowid
                total_headers += 1
                for s in syms:
                    pop = _compute_popularity(s, meta)
                    toks = str(s["tokens"])
                    for w in toks.split():
                        if len(w) >= 2:
                            vocab_counter[w] += 1
                    sym_rows.append(
                        (
                            hid,
                            s["name"],
                            str(s["name"]).lower(),
                            s["qualified_name"],
                            s["kind"],
                            s["owner_name"],
                            str(s["owner_name"]).lower(),
                            s["owner_qualified"],
                            s["parent_name"],
                            str(s["parent_name"]).lower(),
                            s["base_classes"],
                            s["access"],
                            s["return_type"],
                            s["params_json"],
                            s["params_raw"],
                            s["param_count"],
                            s["required_param_count"],
                            s["qualifiers"],
                            s["macro_kind"],
                            s["macro_specifiers"],
                            s["is_blueprint"],
                            s["is_deprecated"],
                            s["deprecated_msg"],
                            s["signature"],
                            s["doc_summary"],
                            s["doc_remarks"],
                            s["doc_return"],
                            s["doc_see"],
                            s["line_start"],
                            s["line_end"],
                            toks,
                            s["initials"],
                            meta["category"],
                            meta["module"],
                            meta["visibility"],
                            pop,
                        )
                    )
            if sym_rows:
                cur.executemany(insert_sym_sql, sym_rows)
                total_symbols += len(sym_rows)
            if total_headers % 4500 < batch_size:
                conn.commit()
                elapsed = time.time() - t_start
                print(
                    f"  Progress: {total_headers:,}/{len(headers):,} headers | {total_symbols:,} symbols ({elapsed:.1f}s)",
                    flush=True,
                )

    conn.commit()
    print(f"Parsed all {total_headers:,} headers -> {total_symbols:,} symbols in {time.time() - t_start:.1f}s", flush=True)

    # Save top vocabulary words for broken-case CamelCase word segmentation
    top_vocab = [(w, c) for w, c in vocab_counter.most_common(60000) if c >= 2]
    cur.executemany("INSERT OR REPLACE INTO vocab(word, freq) VALUES (?, ?)", top_vocab)
    cur.executemany(
        "INSERT OR REPLACE INTO meta_info(key, value) VALUES (?, ?)",
        [
            ("engine_version", "5.8"),
            ("engine_root", engine_root),
            ("total_headers", str(total_headers)),
            ("total_symbols", str(total_symbols)),
            ("built_at", time.strftime("%Y-%m-%d %H:%M:%S")),
        ],
    )
    conn.commit()

    create_indexes(conn)
    conn.close()

    if os.path.exists(db_path):
        os.remove(db_path)
    os.replace(tmp_db, db_path)
    size_mb = os.path.getsize(db_path) / (1024 * 1024)
    print(f"DONE! Database saved to {db_path} ({size_mb:.1f} MB) in {time.time() - t_start:.1f}s total.", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build UE 5.8 C++ API SQLite database")
    ap.add_argument("--engine", default=r"D:\Epic Games\UE_5.8\Engine", help="Path to UE 5.8 Engine directory")
    ap.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ue58_api.db"),
        help="Output SQLite DB path",
    )
    ap.add_argument("--workers", type=int, default=20, help="Number of worker processes")
    args = ap.parse_args()
    build_database(args.engine, args.out, args.workers)


if __name__ == "__main__":
    main()
