"""Single-pass UE 5.8 C++ header parser extracting types, inheritance, functions, and properties."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .cpp_decl import (
    clean_ws,
    extract_deprecation,
    find_matching_paren,
    parse_enum_values,
    parse_function_decl,
    parse_property_decl,
    split_top_level,
)
from .header_meta import extract_header_meta, parse_doxygen, tokenize_identifier

_UE_MACRO_RE = re.compile(
    r"^(UCLASS|USTRUCT|UENUM|UINTERFACE|UFUNCTION|UPROPERTY)\s*\(", re.S
)
_CLASS_RE = re.compile(
    r"^(?:template\s*<[^>]*>\s*)?(class|struct)\s+"
    r"(?:(?:UE_API|[A-Z][A-Z0-9_]*_API|alignas\([^)]*\)|MS_ALIGN\([^)]*\)|"
    r"GCC_ALIGN\([^)]*\)|UE_DEPRECATED\([^)]*\)|DEPRECATED\([^)]*\)|final)\s+)*"
    r"([A-Za-z_][A-Za-z0-9_]*)(?:\s+final)?"
    r"(?:\s*:\s*(.+))?$",
    re.S,
)
_ENUM_RE = re.compile(
    r"^enum(?:\s+(class|struct))?\s+"
    r"(?:(?:UE_DEPRECATED\([^)]*\)|DEPRECATED\([^)]*\))\s+)*"
    r"([A-Za-z_][A-Za-z0-9_]*)(?:\s*:\s*([A-Za-z0-9_:\s]+))?$",
    re.S,
)
_NS_RE = re.compile(r"^namespace\s+([A-Za-z_][A-Za-z0-9_:]*)$")
_ACCESS_RE = re.compile(r"^(public|protected|private)\s*:$")
_DELEGATE_RE = re.compile(
    r"^(DECLARE_(?:DYNAMIC_)?(?:MULTICAST_)?(?:SPARSE_)?(?:DELEGATE|EVENT)[A-Za-z0-9_]*)\s*\((.*)\)$",
    re.S,
)


@dataclass
class Scope:
    kind: str  # 'namespace', 'class', 'struct', 'interface', 'other'
    name: str
    qualified: str
    access: str = "public"
    brace_depth: int = 0


def _parse_base_classes(bases_raw: Optional[str]) -> Tuple[List[str], str]:
    if not bases_raw:
        return [], ""
    bases: List[str] = []
    for part in split_top_level(bases_raw, ","):
        b = re.sub(r"\b(?:public|protected|private|virtual)\b", "", part).strip()
        b = clean_ws(b)
        if b:
            bases.append(b)
    if not bases:
        return [], ""
    # Primary parent name without template args or namespace prefix
    first = bases[0].split("<", 1)[0].strip()
    if "::" in first:
        first = first.rsplit("::", 1)[-1]
    return bases, first


def _skip_balanced_braces(src: str, open_brace_idx: int) -> int:
    """Return index right after matching '}' for src[open_brace_idx] == '{'."""
    depth = 0
    i = open_brace_idx
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == "/" and i + 1 < n:
            nxt = src[i + 1]
            if nxt == "/":
                nl = src.find("\n", i + 2)
                i = n if nl == -1 else nl + 1
                continue
            if nxt == "*":
                end = src.find("*/", i + 2)
                i = n if end == -1 else end + 2
                continue
        elif ch in ('"', "'"):
            q = ch
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == q:
                    i += 1
                    break
                i += 1
            continue
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _strip_not_cpp_blocks(src: str) -> str:
    """Blank out `#if !CPP` / `#if CPP == 0` UHT-only mirror blocks while preserving line numbers."""
    if "!CPP" not in src and "CPP ==" not in src and "CPP==" not in src:
        return src
    out_lines: List[str] = []
    skip_depth = 0
    for line in src.splitlines(keepends=True):
        s = line.strip()
        if skip_depth > 0:
            if re.match(r"^#\s*if(?:def|ndef)?\b", s):
                skip_depth += 1
            elif re.match(r"^#\s*endif\b", s):
                skip_depth -= 1
            elif skip_depth == 1 and re.match(r"^#\s*(?:else|elif)\b", s):
                skip_depth = 0
                out_lines.append(line)
                continue
            out_lines.append("\n")
            continue
        if re.match(r"^#\s*if\s+(?:!CPP\b|!\s*\(\s*CPP\s*\)|CPP\s*==\s*0\b)", s):
            skip_depth = 1
            out_lines.append("\n")
            continue
        out_lines.append(line)
    return "".join(out_lines)


def parse_header_file(
    abs_path: str,
    engine_root: str,
    precomputed_meta: Optional[Dict[str, str]] = None,
    override_src: Optional[str] = None,
) -> Tuple[Dict[str, str], List[Dict[str, object]]]:
    """Parse one UE 5.8 C++ header file into (header_meta, list_of_symbols)."""
    meta = precomputed_meta if precomputed_meta is not None else extract_header_meta(abs_path, engine_root)
    if override_src is not None:
        src = override_src
    else:
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                src = f.read()
        except OSError:
            return meta, []

    src = _strip_not_cpp_blocks(src)

    symbols: List[Dict[str, object]] = []
    scopes: List[Scope] = []
    brace_depth = 0

    pending_comment = ""
    pending_macro_kind = ""
    pending_macro_args = ""
    stmt_buf: List[str] = []
    stmt_start_line = 1

    i = 0
    n = len(src)
    line_no = 1

    def current_owner() -> Tuple[str, str, str]:
        """Return (owner_name, owner_qualified, access)."""
        for sc in reversed(scopes):
            if sc.kind in ("class", "struct", "interface"):
                return sc.name, sc.qualified, sc.access
        ns = "::".join(sc.name for sc in scopes if sc.kind == "namespace")
        return "", ns, "public"

    def build_qualified(name: str) -> str:
        parts = [sc.name for sc in scopes if sc.name]
        parts.append(name)
        return "::".join(parts)

    def clear_pending() -> None:
        nonlocal pending_comment, pending_macro_kind, pending_macro_args
        pending_comment = ""
        pending_macro_kind = ""
        pending_macro_args = ""

    def flush_statement(end_char: str, end_line: int, open_brace_pos: int = -1) -> int:
        """Process accumulated statement ending with ';' or '{'. Returns next index if body skipped."""
        nonlocal brace_depth, pending_comment, pending_macro_kind, pending_macro_args, stmt_buf
        raw_stmt = "".join(stmt_buf).strip()
        stmt_buf = []

        if not raw_stmt:
            if end_char == "{":
                brace_depth += 1
                scopes.append(Scope("other", "", "", "public", brace_depth))
            return -1

        # Strip bare PRAGMA_*/GENERATED_*BODY/ENUM_CLASS_FLAGS macros before UE reflection macros
        if re.match(r"^GENERATED_UCLASS_BODY\s*\([^)]*\)", raw_stmt):
            for sc in reversed(scopes):
                if sc.kind in ("class", "struct", "interface"):
                    sc.access = "public"
                    break
        raw_stmt = re.sub(
            r"^(?:(?:GENERATED_(?:UCLASS_|USTRUCT_|IINTERFACE_)?BODY\s*\([^)]*\)|"
            r"ENUM_CLASS_FLAGS\s*\([^)]*\)|"
            r"DECLARE_LOG_CATEGORY_EXTERN\s*\([^)]*\)|"
            r"DEFINE_LOG_CATEGORY(?:_STATIC)?\s*\([^)]*\)|"
            r"UE_DECLARE_GAMEPLAY_TAG_EXTERN\s*\([^)]*\)|"
            r"PRAGMA_[A-Z0-9_]+|THIRD_PARTY_INCLUDES_(?:START|END)|"
            r"UE_INTERNAL|UE_NONCOPYABLE\s*\([^)]*\))\s*)+",
            "",
            raw_stmt,
        ).strip()
        if not raw_stmt:
            if end_char == "{":
                brace_depth += 1
                scopes.append(Scope("other", "", "", "public", brace_depth))
            return -1

        # Handle UE reflection macros that don't end with ';' (e.g. UFUNCTION(...) on its own line)
        # Check if raw_stmt starts with UCLASS/USTRUCT/UENUM/UFUNCTION/UPROPERTY followed by actual decl
        while True:
            mm = _UE_MACRO_RE.match(raw_stmt)
            if not mm:
                break
            open_p = raw_stmt.find("(", mm.start())
            close_p = find_matching_paren(raw_stmt, open_p)
            if close_p == -1:
                break
            pending_macro_kind = mm.group(1)
            pending_macro_args = clean_ws(raw_stmt[open_p + 1 : close_p])
            raw_stmt = raw_stmt[close_p + 1 :].strip()
            if not raw_stmt:
                if end_char == "{":
                    brace_depth += 1
                    scopes.append(Scope("other", "", "", "public", brace_depth))
                return -1

        # Strip GENERATED_BODY() and bare PRAGMA_*/THIRD_PARTY_INCLUDES_* macros glued without ';'
        raw_stmt = re.sub(
            r"^(?:(?:GENERATED_(?:UCLASS_|USTRUCT_|IINTERFACE_)?BODY\s*\([^)]*\)|"
            r"PRAGMA_[A-Z0-9_]+|THIRD_PARTY_INCLUDES_(?:START|END)|"
            r"UE_INTERNAL|UE_NONCOPYABLE\s*\([^)]*\)|"
            r"DEFINE_LOG_CATEGORY_STATIC\s*\([^)]*\))\s*)+",
            "",
            raw_stmt,
        ).strip()
        if not raw_stmt:
            if end_char == "{":
                brace_depth += 1
                scopes.append(Scope("other", "", "", "public", brace_depth))
            return -1

        # Access specifiers like `public:` may be glued before next declaration if we didn't split on ':'
        acc_prefix = re.match(r"^(public|protected|private)\s*:\s*(.*)$", raw_stmt, re.S)
        if acc_prefix:
            for sc in reversed(scopes):
                if sc.kind in ("class", "struct", "interface"):
                    sc.access = acc_prefix.group(1)
                    break
            raw_stmt = acc_prefix.group(2).strip()
            if not raw_stmt:
                if end_char == "{":
                    brace_depth += 1
                    scopes.append(Scope("other", "", "", "public", brace_depth))
                return -1

        owner_name, owner_qual, access = current_owner()
        doc = parse_doxygen(pending_comment)

        # 1. Namespace opening
        if end_char == "{":
            ns_m = _NS_RE.match(clean_ws(raw_stmt))
            if ns_m:
                brace_depth += 1
                ns_name = ns_m.group(1)
                scopes.append(Scope("namespace", ns_name, build_qualified(ns_name), "public", brace_depth))
                clear_pending()
                return -1

            # 2. Enum definition
            enum_m = _ENUM_RE.match(clean_ws(raw_stmt))
            if enum_m:
                after_pos = _skip_balanced_braces(src, open_brace_pos)
                body = src[open_brace_pos + 1 : max(open_brace_pos + 1, after_pos - 1)]
                enum_name = enum_m.group(2)
                underlying = (enum_m.group(3) or "").strip()
                values = parse_enum_values(body)
                is_dep, dep_msg, _ = extract_deprecation(raw_stmt)
                toks, inits = tokenize_identifier(enum_name)
                symbols.append({
                    "name": enum_name,
                    "qualified_name": build_qualified(enum_name),
                    "kind": "enum",
                    "owner_name": owner_name,
                    "owner_qualified": owner_qual,
                    "parent_name": underlying,
                    "base_classes": json.dumps([], ensure_ascii=False),
                    "access": access,
                    "return_type": underlying,
                    "params_json": json.dumps(values, ensure_ascii=False),
                    "params_raw": "",
                    "param_count": len(values),
                    "required_param_count": 0,
                    "qualifiers": "enum class" if enum_m.group(1) else "enum",
                    "macro_kind": pending_macro_kind,
                    "macro_specifiers": pending_macro_args,
                    "is_blueprint": 1 if "BlueprintType" in pending_macro_args else 0,
                    "is_deprecated": 1 if is_dep else 0,
                    "deprecated_msg": dep_msg,
                    "signature": clean_ws(raw_stmt),
                    "doc_summary": doc["summary"],
                    "doc_remarks": doc["remarks"],
                    "doc_return": "",
                    "doc_see": json.dumps(doc["see"], ensure_ascii=False),
                    "line_start": stmt_start_line,
                    "line_end": end_line + body.count("\n"),
                    "tokens": toks,
                    "initials": inits,
                })
                clear_pending()
                return after_pos

            # 3. Class / Struct / Interface definition
            clean_header = clean_ws(raw_stmt)
            is_dep_c, dep_msg_c, clean_no_dep = extract_deprecation(clean_header)
            clean_no_attr = re.sub(
                r"\b(?:alignas|MS_ALIGN|GCC_ALIGN)\s*\((?:[^()]|\([^()]*\))*\)|\[\[[^\]]*\]\]",
                " ",
                clean_no_dep,
            )
            cls_m = _CLASS_RE.match(clean_ws(clean_no_attr))
            if cls_m and "(" not in clean_no_attr.split(":")[0]:
                kw = cls_m.group(1)
                cname = cls_m.group(2)
                bases, primary_parent = _parse_base_classes(cls_m.group(3))
                kind = "interface" if pending_macro_kind == "UINTERFACE" else kw
                default_access = "private" if kw == "class" else "public"
                qname = build_qualified(cname)
                toks, inits = tokenize_identifier(cname)
                symbols.append({
                    "name": cname,
                    "qualified_name": qname,
                    "kind": kind,
                    "owner_name": owner_name,
                    "owner_qualified": owner_qual,
                    "parent_name": primary_parent,
                    "base_classes": json.dumps(bases, ensure_ascii=False),
                    "access": access,
                    "return_type": "",
                    "params_json": "[]",
                    "params_raw": "",
                    "param_count": 0,
                    "required_param_count": 0,
                    "qualifiers": kw,
                    "macro_kind": pending_macro_kind,
                    "macro_specifiers": pending_macro_args,
                    "is_blueprint": 1 if ("Blueprintable" in pending_macro_args or "BlueprintType" in pending_macro_args) else 0,
                    "is_deprecated": 1 if is_dep_c else 0,
                    "deprecated_msg": dep_msg_c,
                    "signature": clean_header,
                    "doc_summary": doc["summary"],
                    "doc_remarks": doc["remarks"],
                    "doc_return": "",
                    "doc_see": json.dumps(doc["see"], ensure_ascii=False),
                    "line_start": stmt_start_line,
                    "line_end": end_line,
                    "tokens": toks,
                    "initials": inits,
                })
                brace_depth += 1
                scopes.append(Scope(kind, cname, qname, default_access, brace_depth))
                clear_pending()
                return -1

            # 4. Inline function definition: parse signature and skip body!
            fn = parse_function_decl(raw_stmt, owner_name, doc["params"])
            after_pos = _skip_balanced_braces(src, open_brace_pos)
            body_lines = src[open_brace_pos:after_pos].count("\n")
            if fn:
                toks, inits = tokenize_identifier(fn["name"])
                is_bp = 1 if ("BlueprintCallable" in pending_macro_args or "BlueprintPure" in pending_macro_args or "BlueprintImplementableEvent" in pending_macro_args or "BlueprintNativeEvent" in pending_macro_args) else 0
                is_dep_f = fn["is_deprecated"] or (1 if "DeprecatedFunction" in pending_macro_args else 0)
                symbols.append({
                    "name": fn["name"],
                    "qualified_name": build_qualified(fn["name"]),
                    "kind": "function",
                    "owner_name": owner_name,
                    "owner_qualified": owner_qual,
                    "parent_name": "",
                    "base_classes": "[]",
                    "access": access,
                    "return_type": fn["return_type"],
                    "params_json": json.dumps(fn["params"], ensure_ascii=False),
                    "params_raw": fn["params_raw"],
                    "param_count": fn["param_count"],
                    "required_param_count": fn["required_param_count"],
                    "qualifiers": fn["qualifiers"],
                    "macro_kind": pending_macro_kind,
                    "macro_specifiers": pending_macro_args,
                    "is_blueprint": is_bp,
                    "is_deprecated": is_dep_f,
                    "deprecated_msg": fn["deprecated_msg"],
                    "signature": fn["signature"],
                    "doc_summary": doc["summary"],
                    "doc_remarks": doc["remarks"],
                    "doc_return": doc["returns"],
                    "doc_see": json.dumps(doc["see"], ensure_ascii=False),
                    "line_start": stmt_start_line,
                    "line_end": end_line + body_lines,
                    "tokens": toks,
                    "initials": inits,
                })
            clear_pending()
            return after_pos

        # end_char == ';'
        # 5. Delegate macros
        del_m = _DELEGATE_RE.match(clean_ws(raw_stmt))
        if del_m:
            d_macro = del_m.group(1)
            d_args = split_top_level(del_m.group(2), ",")
            if d_args:
                d_name = d_args[0].strip()
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", d_name):
                    toks, inits = tokenize_identifier(d_name)
                    symbols.append({
                        "name": d_name,
                        "qualified_name": build_qualified(d_name),
                        "kind": "delegate",
                        "owner_name": owner_name,
                        "owner_qualified": owner_qual,
                        "parent_name": "",
                        "base_classes": "[]",
                        "access": access,
                        "return_type": d_macro,
                        "params_json": "[]",
                        "params_raw": clean_ws(del_m.group(2)),
                        "param_count": max(0, len(d_args) - 1),
                        "required_param_count": 0,
                        "qualifiers": d_macro,
                        "macro_kind": pending_macro_kind,
                        "macro_specifiers": pending_macro_args,
                        "is_blueprint": 1 if "DYNAMIC" in d_macro else 0,
                        "is_deprecated": 0,
                        "deprecated_msg": "",
                        "signature": clean_ws(raw_stmt),
                        "doc_summary": doc["summary"],
                        "doc_remarks": doc["remarks"],
                        "doc_return": "",
                        "doc_see": json.dumps(doc["see"], ensure_ascii=False),
                        "line_start": stmt_start_line,
                        "line_end": end_line,
                        "tokens": toks,
                        "initials": inits,
                    })
            clear_pending()
            return -1

        # 6. Function prototype
        fn = parse_function_decl(raw_stmt, owner_name, doc["params"])
        if fn:
            toks, inits = tokenize_identifier(fn["name"])
            is_bp = 1 if ("BlueprintCallable" in pending_macro_args or "BlueprintPure" in pending_macro_args or "BlueprintImplementableEvent" in pending_macro_args or "BlueprintNativeEvent" in pending_macro_args) else 0
            is_dep_f = fn["is_deprecated"] or (1 if "DeprecatedFunction" in pending_macro_args else 0)
            symbols.append({
                "name": fn["name"],
                "qualified_name": build_qualified(fn["name"]),
                "kind": "function",
                "owner_name": owner_name,
                "owner_qualified": owner_qual,
                "parent_name": "",
                "base_classes": "[]",
                "access": access,
                "return_type": fn["return_type"],
                "params_json": json.dumps(fn["params"], ensure_ascii=False),
                "params_raw": fn["params_raw"],
                "param_count": fn["param_count"],
                "required_param_count": fn["required_param_count"],
                "qualifiers": fn["qualifiers"],
                "macro_kind": pending_macro_kind,
                "macro_specifiers": pending_macro_args,
                "is_blueprint": is_bp,
                "is_deprecated": is_dep_f,
                "deprecated_msg": fn["deprecated_msg"],
                "signature": fn["signature"],
                "doc_summary": doc["summary"],
                "doc_remarks": doc["remarks"],
                "doc_return": doc["returns"],
                "doc_see": json.dumps(doc["see"], ensure_ascii=False),
                "line_start": stmt_start_line,
                "line_end": end_line,
                "tokens": toks,
                "initials": inits,
            })
            clear_pending()
            return -1

        # 7. Member property (inside a class/struct, UPROPERTY or public/protected)
        if owner_name and (pending_macro_kind == "UPROPERTY" or access in ("public", "protected")):
            prop = parse_property_decl(raw_stmt)
            if prop:
                toks, inits = tokenize_identifier(prop["name"])
                is_bp = 1 if ("BlueprintReadWrite" in pending_macro_args or "BlueprintReadOnly" in pending_macro_args or "EditAnywhere" in pending_macro_args) else 0
                symbols.append({
                    "name": prop["name"],
                    "qualified_name": build_qualified(prop["name"]),
                    "kind": "property",
                    "owner_name": owner_name,
                    "owner_qualified": owner_qual,
                    "parent_name": "",
                    "base_classes": "[]",
                    "access": access,
                    "return_type": prop["return_type"],
                    "params_json": "[]",
                    "params_raw": prop["default_val"],
                    "param_count": 0,
                    "required_param_count": 0,
                    "qualifiers": "",
                    "macro_kind": pending_macro_kind,
                    "macro_specifiers": pending_macro_args,
                    "is_blueprint": is_bp,
                    "is_deprecated": prop["is_deprecated"],
                    "deprecated_msg": prop["deprecated_msg"],
                    "signature": prop["signature"],
                    "doc_summary": doc["summary"],
                    "doc_remarks": doc["remarks"],
                    "doc_return": "",
                    "doc_see": json.dumps(doc["see"], ensure_ascii=False),
                    "line_start": stmt_start_line,
                    "line_end": end_line,
                    "tokens": toks,
                    "initials": inits,
                })

        clear_pending()
        return -1

    # Main character scan
    paren_depth = 0
    while i < n:
        ch = src[i]

        # Line comment
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            nl = src.find("\n", i + 2)
            end = n if nl == -1 else nl
            c_text = src[i:end]
            if not "".join(stmt_buf).strip():
                pending_comment = (pending_comment + "\n" + c_text).strip() if pending_comment else c_text
            i = end
            continue

        # Block comment
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            end_pos = n if end == -1 else end + 2
            c_text = src[i:end_pos]
            line_no += c_text.count("\n")
            if not "".join(stmt_buf).strip():
                pending_comment = c_text
            i = end_pos
            continue

        # Preprocessor directive (at line start or with whitespace before '#')
        if ch == "#" and (i == 0 or src[i - 1] in "\n\r\t "):
            j = i
            while j < n:
                nl = src.find("\n", j)
                if nl == -1:
                    j = n
                    break
                line_no += 1
                if nl > 0 and src[nl - 1] == "\\":
                    j = nl + 1
                elif nl > 1 and src[nl - 1] == "\r" and src[nl - 2] == "\\":
                    j = nl + 1
                else:
                    j = nl + 1
                    break
            i = j
            continue

        # String / char literal
        if ch in ('"', "'"):
            q = ch
            start = i
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "\n":
                    line_no += 1
                if src[i] == q:
                    i += 1
                    break
                i += 1
            stmt_buf.append(src[start:i])
            continue

        if ch == "\n":
            line_no += 1
            if not "".join(stmt_buf).strip():
                stmt_start_line = line_no
            else:
                stmt_buf.append(" ")
            i += 1
            continue

        if ch == "(":
            paren_depth += 1
            stmt_buf.append(ch)
            i += 1
            continue

        if ch == ")":
            paren_depth = max(0, paren_depth - 1)
            stmt_buf.append(ch)
            # Check if buffer is purely a standalone UE macro like UFUNCTION(...)
            if paren_depth == 0:
                cur = "".join(stmt_buf).strip()
                mm = _UE_MACRO_RE.match(cur)
                if mm and cur.endswith(")"):
                    open_p = cur.find("(")
                    if find_matching_paren(cur, open_p) == len(cur) - 1:
                        pending_macro_kind = mm.group(1)
                        pending_macro_args = clean_ws(cur[open_p + 1 : -1])
                        stmt_buf = []
                        stmt_start_line = line_no
            i += 1
            continue

        # Access specifier at top of class/struct: `public:`, `protected:`, `private:`
        if ch == ":" and paren_depth == 0 and (i + 1 >= n or src[i + 1] != ":"):
            cur = "".join(stmt_buf).strip()
            if cur in ("public", "protected", "private"):
                for sc in reversed(scopes):
                    if sc.kind in ("class", "struct", "interface"):
                        sc.access = cur
                        break
                stmt_buf = []
                clear_pending()
                stmt_start_line = line_no
                i += 1
                continue

        if ch == ";" and paren_depth == 0:
            flush_statement(";", line_no)
            stmt_start_line = line_no
            i += 1
            continue

        if ch == "{" and paren_depth == 0:
            next_idx = flush_statement("{", line_no, open_brace_pos=i)
            if next_idx != -1:
                line_no += src[i:next_idx].count("\n")
                i = next_idx
                stmt_start_line = line_no
                continue
            stmt_start_line = line_no
            i += 1
            continue

        if ch == "}" and paren_depth == 0:
            stmt_buf = []
            clear_pending()
            if scopes and scopes[-1].brace_depth == brace_depth:
                scopes.pop()
            brace_depth = max(0, brace_depth - 1)
            stmt_start_line = line_no
            i += 1
            continue

        if not stmt_buf and ch.isspace():
            i += 1
            continue

        if not stmt_buf:
            stmt_start_line = line_no
        stmt_buf.append(ch)
        i += 1

    return meta, symbols
