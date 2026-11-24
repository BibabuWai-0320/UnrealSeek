"""UnrealBuildTool (*.Build.cs) module & official #include path resolver for UE 5.8."""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

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

_PUB_INC_STR_RE = re.compile(r'"([^"]+)"')
_PUB_INC_BLOCK_RE = re.compile(r"PublicIncludePaths\.Add(?:Range)?\s*\(([^;]+)\)", re.S)


def build_module_table(engine_root: str) -> Dict[str, Dict[str, object]]:
    """Scan all *.Build.cs files under Engine/Source and Engine/Plugins.

    Returns dict keyed by normcase(module_dir) -> {
        'module': str,
        'mod_dir': str,
        'public_roots': List[str],   # normcase absolute directories valid as #include roots
    }
    """
    table: Dict[str, Dict[str, object]] = {}
    for sub in ("Source", "Plugins"):
        base = os.path.join(engine_root, sub)
        if not os.path.exists(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in files:
                if not fn.endswith(".Build.cs"):
                    continue
                mod_name = fn[: -len(".Build.cs")]
                fp = os.path.join(root, fn)
                pub_roots: List[str] = []
                # Standard UBT automatic public include roots: Public/ and Classes/
                for std_dir in ("Public", "Classes"):
                    cand = os.path.join(root, std_dir)
                    if os.path.isdir(cand):
                        pub_roots.append(os.path.normpath(cand))

                # Parse explicit PublicIncludePaths in *.Build.cs
                try:
                    txt = open(fp, "r", encoding="utf-8", errors="ignore").read()
                except OSError:
                    txt = ""
                for blk in _PUB_INC_BLOCK_RE.findall(txt):
                    for s in _PUB_INC_STR_RE.findall(blk):
                        s_clean = s.replace("\\", "/").strip("/")
                        if not s_clean or "Private" in s_clean.split("/"):
                            continue
                        # Resolve relative to Engine/Source or ModuleDirectory
                        for base_cand in (os.path.join(engine_root, "Source"), root):
                            full = os.path.normpath(os.path.join(base_cand, s_clean))
                            if os.path.isdir(full) and full not in pub_roots:
                                pub_roots.append(full)

                table[os.path.normcase(os.path.normpath(root))] = {
                    "module": mod_name,
                    "mod_dir": os.path.normpath(root),
                    "public_roots": pub_roots,
                }
    return table


def resolve_header_with_ubt(
    abs_path: str,
    engine_root: str,
    module_table: Dict[str, Dict[str, object]],
) -> Dict[str, str]:
    """Compute authentic UE 5.8 module, visibility, and official #include path using *.Build.cs."""
    norm_abs = os.path.normpath(abs_path)
    rel = os.path.relpath(norm_abs, engine_root).replace("\\", "/")
    parts = rel.split("/")

    category = "Other"
    plugin_name = ""
    if len(parts) >= 2 and parts[0] == "Source":
        category = parts[1]
    elif len(parts) >= 2 and parts[0] == "Plugins":
        category = "Plugins"
        if "Source" in parts:
            src_idx = parts.index("Source")
            plugin_name = "/".join(parts[1:src_idx]) if src_idx > 1 else parts[1]
        else:
            plugin_name = parts[1]

    # Walk upward to find the owning *.Build.cs directory
    mod_info: Optional[Dict[str, object]] = None
    cur = os.path.dirname(norm_abs)
    eng_norm = os.path.normcase(os.path.normpath(engine_root))
    while len(os.path.normcase(cur)) >= len(eng_norm):
        nc = os.path.normcase(cur)
        if nc in module_table:
            mod_info = module_table[nc]
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    module_name = str(mod_info["module"]) if mod_info else (parts[2] if len(parts) >= 3 else "Unknown")
    visibility = "Private"
    include_path = ""

    if mod_info:
        mod_dir = str(mod_info["mod_dir"])
        rel_from_mod = os.path.relpath(norm_abs, mod_dir).replace("\\", "/")
        segs = rel_from_mod.split("/")
        dir_segs = set(segs[:-1])

        if "Private" in dir_segs or "Tests" in dir_segs or "ThirdParty" in dir_segs:
            visibility = "Private"
            include_path = ""
        elif "Internal" in dir_segs:
            visibility = "Internal"
            include_path = ""
        elif segs[0] in ("Public", "Classes") and len(segs) >= 2:
            visibility = "Public"
            include_path = "/".join(segs[1:])
        else:
            # Check explicit PublicIncludePaths roots (prefer deepest matching root)
            matched_rel = ""
            best_len = -1
            abs_nc = os.path.normcase(norm_abs)
            for proot in mod_info["public_roots"]:  # type: ignore[union-attr]
                pr_nc = os.path.normcase(str(proot)) + os.sep
                if abs_nc.startswith(pr_nc) and len(pr_nc) > best_len:
                    best_len = len(pr_nc)
                    matched_rel = os.path.relpath(norm_abs, str(proot)).replace("\\", "/")
            if matched_rel:
                visibility = "Public"
                include_path = matched_rel
            else:
                # Header directly in module root with no Public/Classes/Private folder (legacy module)
                has_std_dirs = any(
                    os.path.isdir(os.path.join(mod_dir, d)) for d in ("Public", "Classes", "Private")
                )
                if not has_std_dirs:
                    visibility = "Public"
                    include_path = rel_from_mod
                else:
                    visibility = "Private"
                    include_path = ""
    else:
        # Fallback if header is outside any *.Build.cs (rare)
        if "Private" in parts[:-1] or "Tests" in parts[:-1]:
            visibility = "Private"
        elif "Internal" in parts[:-1]:
            visibility = "Internal"
        elif "Public" in parts[:-1]:
            idx = parts.index("Public")
            visibility = "Public"
            include_path = "/".join(parts[idx + 1 :])
        elif "Classes" in parts[:-1]:
            idx = parts.index("Classes")
            visibility = "Public"
            include_path = "/".join(parts[idx + 1 :])

    return {
        "rel_path": "Engine/" + rel,
        "abs_path": norm_abs,
        "category": category,
        "plugin_name": plugin_name,
        "module": module_name,
        "visibility": visibility,
        "include_path": include_path,
        "file_name": parts[-1],
    }
