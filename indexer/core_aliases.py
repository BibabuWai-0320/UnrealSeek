"""UE 5.8 Core LWC math aliases (FVector, FRotator, FQuat, FTransform...) & .h.inl containers (FString, TSet, TMap)."""

from __future__ import annotations

import os
import re
from typing import Dict, List

from .cpp_parser import parse_header_file
from .header_meta import tokenize_identifier

# Official UE 5.8 Core math headers -> (Canonical Alias Name, Underlying Template Name, optional sibling header)
LWC_MATH_HEADERS: Dict[str, tuple[str, str, str]] = {
    "Math/Vector.h": ("FVector", "TVector", ""),
    "Math/Vector2D.h": ("FVector2D", "TVector2", ""),
    "Math/Vector4.h": ("FVector4", "TVector4", ""),
    "Math/Rotator.h": ("FRotator", "TRotator", ""),
    "Math/Quat.h": ("FQuat", "TQuat", ""),
    "Math/Transform.h": ("FTransform", "TTransform", "Math/TransformNonVectorized.h"),
    "Math/Matrix.h": ("FMatrix", "TMatrix", ""),
    "Math/Plane.h": ("FPlane", "TPlane", ""),
    "Math/Box.h": ("FBox", "TBox", ""),
    "Math/Box2D.h": ("FBox2D", "TBox2", ""),
    "Math/Sphere.h": ("FSphere", "TSphere", ""),
    "Math/Ray.h": ("FRay", "TRay", ""),
    "Math/BoxSphereBounds.h": ("FBoxSphereBounds", "TBoxSphereBounds", ""),
}

# UE 5.8 Core macro-injected .h.inl containers -> (inl_rel_to_same_dir, macro_replacements, target_class_name)
INL_CONTAINER_HEADERS: Dict[str, tuple[str, List[tuple[str, str]], str]] = {
    "Containers/UnrealString.h": (
        "UnrealString.h.inl",
        [
            (r"\bUE_STRING_CLASS\b", "FString"),
            (r"\bUE_STRING_CHARTYPE\b", "TCHAR"),
            (r"\bUE_STRING_DEPRECATED\s*\([^)]*\)", ""),
        ],
        "FString",
    ),
    "Containers/Set.h": (
        "SparseSet.h.inl",
        [(r"\bUE_TSPARSE_SET\b", "TSet")],
        "TSet",
    ),
    "Containers/Map.h": (
        "Map.h.inl",
        [
            (r"\bTMAPBASE\b", "TMapBase"),
            (r"\bTSORTABLEMAPBASE\b", "TSortableMapBase"),
            (r"\bTMULTIMAP\b", "TMultiMap"),
            (r"\bTMAP\b", "TMap"),
            (r"\bTSET\b", "TSet"),
        ],
        "TMap",
    ),
}


def enrich_core_header_symbols(
    meta: Dict[str, str],
    symbols: List[Dict[str, object]],
    engine_root: str,
) -> List[Dict[str, object]]:
    """Expand UE 5.8 LWC math aliases & .h.inl container definitions under their official #include header."""
    if meta.get("module") != "Core" or meta.get("visibility") != "Public":
        return symbols

    inc = meta.get("include_path", "")

    # 1. LWC Math Types (FVector -> Math/Vector.h, FTransform -> Math/Transform.h, etc.)
    if inc in LWC_MATH_HEADERS:
        alias_name, tmpl_name, sibling_rel = LWC_MATH_HEADERS[inc]
        existing_names = {str(s["name"]) for s in symbols if s["kind"] in ("class", "struct")}
        if alias_name not in existing_names:
            src_syms = symbols
            if sibling_rel:
                sib_path = os.path.join(os.path.dirname(os.path.dirname(meta["abs_path"])), sibling_rel.replace("/", os.sep))
                if os.path.exists(sib_path):
                    _, src_syms = parse_header_file(sib_path, engine_root, precomputed_meta=meta)

            tmpl_type = next(
                (s for s in src_syms if s["kind"] in ("class", "struct") and s["name"] == tmpl_name),
                None,
            )
            toks, inits = tokenize_identifier(alias_name)
            alias_sym: Dict[str, object] = {
                "name": alias_name,
                "qualified_name": alias_name,
                "kind": "struct",
                "owner_name": "",
                "owner_qualified": "",
                "parent_name": "",
                "base_classes": "[]",
                "access": "public",
                "return_type": "",
                "params_json": "[]",
                "params_raw": "",
                "param_count": 0,
                "required_param_count": 0,
                "qualifiers": "using",
                "macro_kind": "",
                "macro_specifiers": "",
                "is_blueprint": 1,
                "is_deprecated": 0,
                "deprecated_msg": "",
                "signature": f"using {alias_name} = UE::Math::{tmpl_name}<double>;",
                "doc_summary": str(tmpl_type["doc_summary"]) if tmpl_type else f"UE 5.8 LWC type alias for UE::Math::{tmpl_name}<double>.",
                "doc_remarks": str(tmpl_type["doc_remarks"]) if tmpl_type else "",
                "doc_return": "",
                "doc_see": "[]",
                "line_start": int(tmpl_type["line_start"]) if tmpl_type and not sibling_rel else 1,
                "line_end": int(tmpl_type["line_end"]) if tmpl_type and not sibling_rel else 1,
                "tokens": toks,
                "initials": inits,
            }
            symbols.append(alias_sym)

            # Mirror TVector/TRotator/TQuat/TTransform public methods under FVector/FRotator/FQuat/FTransform!
            for m in src_syms:
                if m["owner_name"] == tmpl_name and m["kind"] in ("function", "property") and m["access"] == "public":
                    m_copy = dict(m)
                    m_copy["owner_name"] = alias_name
                    m_copy["owner_qualified"] = alias_name
                    if m_copy["name"] == tmpl_name:
                        m_copy["name"] = alias_name
                        t2, i2 = tokenize_identifier(alias_name)
                        m_copy["tokens"] = t2
                        m_copy["initials"] = i2
                    m_copy["qualified_name"] = f"{alias_name}::{m_copy['name']}"
                    symbols.append(m_copy)

    # 2. Macro-injected Core containers (FString, TSet, TMap)
    if inc in INL_CONTAINER_HEADERS:
        inl_name, repls, target_cls = INL_CONTAINER_HEADERS[inc]
        inl_path = os.path.join(os.path.dirname(meta["abs_path"]), inl_name)
        if os.path.exists(inl_path):
            try:
                raw_inl = open(inl_path, "r", encoding="utf-8", errors="ignore").read()
                for pat, sub in repls:
                    raw_inl = re.sub(pat, sub, raw_inl)
                _, inl_syms = parse_header_file(inl_path, engine_root, precomputed_meta=meta, override_src=raw_inl)
                keep_names = {target_cls}
                if target_cls == "TMap":
                    keep_names.update({"TMapBase", "TSortableMapBase", "TMultiMap"})
                for s in inl_syms:
                    if s["name"] in keep_names or s["owner_name"] in keep_names:
                        symbols.append(s)
            except OSError:
                pass

    return symbols
