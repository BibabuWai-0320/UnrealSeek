"""Flask web server for UE 5.8 C++ API Documentation."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "web")
DB_PATH = os.environ.get("UE58_API_DB", os.path.join(BASE_DIR, "data", "ue58_api.db"))

from .db_queries import ApiRepository
from .fuzzy_search import FuzzySearchEngine

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")

_repo: ApiRepository | None = None
_search: FuzzySearchEngine | None = None


def get_repo() -> ApiRepository:
    global _repo
    if _repo is None:
        _repo = ApiRepository(DB_PATH)
    return _repo


def get_search() -> FuzzySearchEngine:
    global _search
    if _search is None:
        _search = FuzzySearchEngine(DB_PATH)
    return _search


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/api/stats")
def api_stats():
    return jsonify(get_repo().get_stats())


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    kind = request.args.get("kind", "").strip()
    category = request.args.get("category", "").strip()
    module = request.args.get("module", "").strip()
    public_only = request.args.get("public_only", "0") == "1"
    limit = min(80, max(1, int(request.args.get("limit", "35"))))
    res = get_search().search(
        q,
        limit=limit,
        kind=kind,
        category=category,
        module=module,
        public_only=public_only,
    )
    return jsonify(res)


@app.route("/api/tree/modules")
def api_tree_modules():
    category = request.args.get("category", "").strip()
    q = request.args.get("q", "").strip()
    limit = min(500, max(1, int(request.args.get("limit", "300"))))
    return jsonify({"items": get_repo().get_modules(category=category, q=q, limit=limit)})


@app.route("/api/tree/module-types")
def api_tree_module_types():
    category = request.args.get("category", "Runtime").strip()
    module = request.args.get("module", "Engine").strip()
    filter_q = request.args.get("filter", "").strip()
    public_only = request.args.get("public_only", "0") == "1"
    offset = max(0, int(request.args.get("offset", "0")))
    limit = min(400, max(1, int(request.args.get("limit", "200"))))
    return jsonify(
        get_repo().get_module_types(
            category=category,
            module=module,
            filter_q=filter_q,
            public_only=public_only,
            offset=offset,
            limit=limit,
        )
    )


@app.route("/api/tree/class-members")
def api_tree_class_members():
    class_name = request.args.get("class_name", "").strip()
    owner_id_str = request.args.get("owner_id", "").strip()
    owner_id = int(owner_id_str) if owner_id_str.isdigit() else None
    filter_q = request.args.get("filter", "").strip()
    limit = min(800, max(1, int(request.args.get("limit", "500"))))
    return jsonify(
        get_repo().get_class_members(
            class_name=class_name, owner_id=owner_id, filter_q=filter_q, limit=limit
        )
    )


@app.route("/api/hierarchy/roots")
def api_hierarchy_roots():
    return jsonify({"items": get_repo().get_hierarchy_roots()})


@app.route("/api/hierarchy/children")
def api_hierarchy_children():
    parent = request.args.get("parent", "").strip()
    filter_q = request.args.get("filter", "").strip()
    offset = max(0, int(request.args.get("offset", "0")))
    limit = min(400, max(1, int(request.args.get("limit", "200"))))
    return jsonify(
        get_repo().get_hierarchy_children(
            parent_name=parent, filter_q=filter_q, limit=limit, offset=offset
        )
    )


@app.route("/api/symbol/<int:symbol_id>")
def api_symbol_detail(symbol_id: int):
    detail = get_repo().get_symbol_detail(symbol_id)
    if not detail:
        return jsonify({"error": "Symbol not found"}), 404
    return jsonify(detail)


@app.route("/api/symbol/by-name")
def api_symbol_by_name():
    name = request.args.get("name", "").strip()
    res = get_repo().resolve_symbol_by_name(name)
    if not res:
        return jsonify({"found": False}), 404
    return jsonify({"found": True, **res})


@app.route("/api/locate/<int:symbol_id>")
def api_locate(symbol_id: int):
    loc = get_repo().locate_symbol(symbol_id)
    if not loc:
        return jsonify({"error": "Symbol not found"}), 404
    return jsonify(loc)


def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def _open_browser_when_ready(host: str, port: int) -> None:
    url = f"http://{host}:{port}"
    for _ in range(60):
        if _is_port_open(host, port):
            webbrowser.open(url)
            return
        time.sleep(0.15)


def main() -> None:
    port = int(os.environ.get("PORT", "5858"))
    host = os.environ.get("HOST", "127.0.0.1")
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}. Run `python -m indexer.build_db` first.", file=sys.stderr)
        sys.exit(1)
    if _is_port_open(host, port):
        print(f"UE 5.8 C++ API Docs server is already running at http://{host}:{port}", flush=True)
        if "--open-browser" in sys.argv:
            webbrowser.open(f"http://{host}:{port}")
        return
    # Warm up search segmenter on startup
    get_search()
    if "--open-browser" in sys.argv:
        threading.Thread(target=_open_browser_when_ready, args=(host, port), daemon=True).start()
    print(f"UE 5.8 C++ API Docs server running at http://{host}:{port}", flush=True)
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
