"""C++ declaration helpers: parameter parsing, function/property/enum extraction for UE 5.8."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_API_MACRO_RE = re.compile(r"\b(?:UE_API|[A-Z][A-Z0-9_]*_API)\b")
_QUAL_PREFIX_RE = re.compile(
    r"\b(?:FORCEINLINE_DEBUGGABLE|FORCEINLINE|FORCENOINLINE|CONSTEXPR|constexpr|"
    r"inline|virtual|static|explicit|mutable|extern|FRIEND|typename|UE_NODISCARD|"
    r"NODISCARD|UE_NONNULL|NONNULL|UE_REWRITE|CA_ASSUME|PRAGMA_DISABLE_DEPRECATION_WARNINGS)\b"
)
_DEPRECATED_RE = re.compile(
    r'\bUE_DEPRECATED\s*\(\s*([^,]+)\s*,\s*"([^"]*)"\s*\)', re.S
)
_DEPRECATED_SIMPLE_RE = re.compile(r"\bDEPRECATED\s*\([^)]*\)", re.S)
_ATTR_RE = re.compile(r"\[\[[^\]]*\]\]")
_WS_RE = re.compile(r"\s+")
_IDENT_RE = re.compile(r"^[A-Za-z_~][A-Za-z0-9_]*$")

_SKIP_FUNC_NAMES = {
    "if", "for", "while", "switch", "return", "sizeof", "alignof", "decltype",
    "static_assert", "check", "ensure", "GENERATED_BODY", "GENERATED_UCLASS_BODY",
    "GENERATED_USTRUCT_BODY", "GENERATED_IINTERFACE_BODY", "UFUNCTION", "UPROPERTY",
    "UCLASS", "USTRUCT", "UENUM", "UINTERFACE", "UMETA", "UE_DEPRECATED",
    "DECLARE_CLASS", "DECLARE_FUNCTION", "DEFINE_FUNCTION", "SLATE_BEGIN_ARGS",
    "SLATE_END_ARGS", "SLATE_ARGUMENT", "SLATE_ATTRIBUTE", "SLATE_EVENT",
    "ENUM_CLASS_FLAGS", "FRIEND_TEST", "UE_DECLARE_LWC_TYPE",
}


def clean_ws(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def extract_deprecation(text: str) -> Tuple[bool, str, str]:
    """Return (is_deprecated, message, cleaned_text)."""
    m = _DEPRECATED_RE.search(text)
    if m:
        ver = m.group(1).strip()
        msg = m.group(2).strip()
        cleaned = text[: m.start()] + " " + text[m.end() :]
        return True, f"Deprecated in UE {ver}: {msg}" if msg else f"Deprecated in UE {ver}", cleaned
    m2 = _DEPRECATED_SIMPLE_RE.search(text)
    if m2:
        cleaned = text[: m2.start()] + " " + text[m2.end() :]
        return True, "Deprecated", cleaned
    return False, "", text


def split_top_level(text: str, sep: str = ",") -> List[str]:
    """Split text by `sep` outside (), <>, [], {}, and quotes."""
    parts: List[str] = []
    buf: List[str] = []
    p_depth = b_depth = c_depth = a_depth = 0
    in_str: Optional[str] = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                i += 1
                buf.append(text[i])
            elif ch == in_str:
                in_str = None
        else:
            if ch in ('"', "'"):
                in_str = ch
                buf.append(ch)
            elif ch == "(":
                p_depth += 1
                buf.append(ch)
            elif ch == ")":
                p_depth = max(0, p_depth - 1)
                buf.append(ch)
            elif ch == "[":
                b_depth += 1
                buf.append(ch)
            elif ch == "]":
                b_depth = max(0, b_depth - 1)
                buf.append(ch)
            elif ch == "{":
                c_depth += 1
                buf.append(ch)
            elif ch == "}":
                c_depth = max(0, c_depth - 1)
                buf.append(ch)
            elif ch == "<":
                a_depth += 1
                buf.append(ch)
            elif ch == ">":
                if a_depth > 0:
                    a_depth -= 1
                buf.append(ch)
            elif ch == sep and p_depth == 0 and b_depth == 0 and c_depth == 0 and a_depth == 0:
                part = "".join(buf).strip()
                if part:
                    parts.append(part)
                buf = []
            else:
                buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_params(params_raw: str, param_docs: Dict[str, str]) -> Tuple[List[Dict[str, str]], int, int]:
    """Parse C++ parameter list into structured items + (total_count, required_count)."""
    raw = clean_ws(params_raw)
    if not raw or raw == "void":
        return [], 0, 0

    items = split_top_level(raw, ",")
    parsed: List[Dict[str, str]] = []
    required = 0

    for item in items:
        item = re.sub(r"\bUPARAM\s*\([^)]*\)", "", item).strip()
        # Separate default value on top-level '='
        eq_parts = split_top_level(item, "=")
        decl_part = eq_parts[0].strip()
        default_val = "=".join(eq_parts[1:]).strip() if len(eq_parts) > 1 else ""
        if not default_val:
            required += 1

        # Extract trailing identifier as parameter name (skip unnamed or function ptrs)
        p_type = decl_part
        p_name = ""
        m = re.search(r"^(.*?)([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?$", decl_part)
        if m:
            prefix = m.group(1).strip()
            cand_name = m.group(2)
            arr_suffix = m.group(3) or ""
            # Avoid misclassifying a lone type like `const FVector` or `int32`
            if prefix and cand_name not in ("const", "volatile", "struct", "class", "enum", "typename", "unsigned", "signed", "long", "short", "int", "void", "bool", "float", "double"):
                p_type = (prefix + (" " + arr_suffix if arr_suffix else "")).strip()
                p_name = cand_name

        doc = param_docs.get(p_name, "")
        parsed.append({
            "type": p_type,
            "name": p_name,
            "default": default_val,
            "doc": doc,
        })

    return parsed, len(parsed), required


def find_matching_paren(text: str, open_idx: int) -> int:
    depth = 0
    in_str: Optional[str] = None
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == in_str:
                in_str = None
        else:
            if ch in ('"', "'"):
                in_str = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def parse_function_decl(
    stmt: str,
    owner_name: str,
    param_docs: Dict[str, str],
) -> Optional[Dict[str, object]]:
    """Attempt to parse a C++ function declaration header (before '{' or ';')."""
    s = _ATTR_RE.sub(" ", stmt)
    is_dep, dep_msg, s = extract_deprecation(s)
    s = clean_ws(s)
    if not s or s.startswith(("friend ", "typedef ", "using ", "return ", "#")):
        return None
    if re.match(r"^(?:DECLARE_|DEFINE_|IMPLEMENT_|SLATE_|BEGIN_|END_)[A-Z0-9_]+\s*\(", s):
        return None

    # Strip leading template<...>
    template_prefix = ""
    if s.startswith("template"):
        lt = s.find("<")
        if lt != -1:
            depth = 0
            for j in range(lt, len(s)):
                if s[j] == "<":
                    depth += 1
                elif s[j] == ">":
                    depth -= 1
                    if depth == 0:
                        template_prefix = s[: j + 1].strip()
                        s = s[j + 1 :].strip()
                        break

    # Find parameter list '(' - avoid '(' inside decltype(...) or operator()
    open_idx = -1
    op_paren = re.search(r"\boperator\s*\(\s*\)", s)
    search_from = op_paren.end() if op_paren else 0
    # Scan top-level outside <>
    a_depth = 0
    for idx in range(search_from, len(s)):
        ch = s[idx]
        if ch == "<":
            a_depth += 1
        elif ch == ">" and a_depth > 0:
            a_depth -= 1
        elif ch == "(" and a_depth == 0:
            open_idx = idx
            break
    if open_idx == -1:
        return None

    close_idx = find_matching_paren(s, open_idx)
    if close_idx == -1:
        return None

    before = s[:open_idx].strip()
    params_raw = s[open_idx + 1 : close_idx].strip()
    after = s[close_idx + 1 :].strip()

    # Strip trailing constructor initializer list ': Base(...)'
    if ":" in after and not after.startswith("::"):
        after_head = after.split(":", 1)[0].strip()
    else:
        after_head = after

    # Check qualifiers after ')'
    valid_after = re.sub(
        r"\b(?:const|override|final|noexcept|volatile|UE_LIFETIMEBOUND)\b|&&|&|=\s*(?:0|default|delete)\b|->\s*[^;{]+",
        "",
        after_head,
    ).strip()
    if valid_after:
        return None

    # Extract function name and return type from `before`
    before_clean = _API_MACRO_RE.sub(" ", before)
    quals: List[str] = []
    for q in ("static", "virtual", "constexpr", "FORCEINLINE", "explicit"):
        if re.search(rf"\b{q}\b", before_clean):
            quals.append(q)
    for q in ("const", "override", "final", "noexcept"):
        if re.search(rf"\b{q}\b", after_head):
            quals.append(q)
    if re.search(r"=\s*0\b", after_head):
        quals.append("pure-virtual")

    # Handle operator overloads
    op_m = re.search(r"(operator\s*(?:\(\)|\[\]|[^\s(]+))$", before_clean)
    if op_m:
        func_name = clean_ws(op_m.group(1))
        ret_raw = before_clean[: op_m.start()].strip()
    else:
        name_m = re.search(r"([~]?[A-Za-z_][A-Za-z0-9_]*)$", before_clean)
        if not name_m:
            return None
        func_name = name_m.group(1)
        ret_raw = before_clean[: name_m.start()].strip()

    if func_name in _SKIP_FUNC_NAMES:
        return None
    # Skip macro-like ALL_CAPS(...) calls without return type
    if not ret_raw and func_name.isupper() and func_name != owner_name:
        return None

    ret_type = clean_ws(_QUAL_PREFIX_RE.sub(" ", ret_raw))
    # Constructor / Destructor detection
    is_ctor = owner_name and (func_name == owner_name or func_name == "~" + owner_name)
    if is_ctor:
        ret_type = ""
    elif not ret_type and not func_name.startswith("operator "):
        # Likely a macro invocation, not a function declaration
        return None

    params, param_count, req_count = parse_params(params_raw, param_docs)
    sig_parts = []
    if template_prefix:
        sig_parts.append(template_prefix)
    sig_parts.append(clean_ws(before) + "(" + clean_ws(params_raw) + ")" + (" " + after_head if after_head else ""))
    signature = "\n".join(sig_parts)

    return {
        "name": func_name,
        "return_type": ret_type,
        "params": params,
        "params_raw": clean_ws(params_raw),
        "param_count": param_count,
        "required_param_count": req_count,
        "qualifiers": " ".join(quals),
        "signature": signature,
        "is_deprecated": 1 if is_dep else 0,
        "deprecated_msg": dep_msg,
        "is_ctor": is_ctor,
    }


def parse_property_decl(stmt: str) -> Optional[Dict[str, object]]:
    """Parse a member variable / UPROPERTY declaration."""
    s = _ATTR_RE.sub(" ", stmt)
    is_dep, dep_msg, s = extract_deprecation(s)
    s = clean_ws(_API_MACRO_RE.sub(" ", s))
    if not s or "(" in s.split("=")[0]:
        return None
    if s.startswith(("friend ", "typedef ", "using ", "static_assert", "GENERATED_", "#")):
        return None

    # Split default initializer on top-level '='
    eq_parts = split_top_level(s, "=")
    lhs = eq_parts[0].strip()
    default_val = "=".join(eq_parts[1:]).strip() if len(eq_parts) > 1 else ""

    # Strip bitfield ': 1'
    lhs = re.sub(r"\s*:\s*\d+$", "", lhs).strip()
    m = re.search(r"^(.*?)([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?$", lhs)
    if not m:
        return None
    p_type = clean_ws(_QUAL_PREFIX_RE.sub(" ", m.group(1)))
    p_name = m.group(2)
    arr = m.group(3) or ""
    if not p_type or p_name in _SKIP_FUNC_NAMES:
        return None
    if arr:
        p_type = (p_type + arr).strip()
    return {
        "name": p_name,
        "return_type": p_type,
        "default_val": default_val,
        "signature": s,
        "is_deprecated": 1 if is_dep else 0,
        "deprecated_msg": dep_msg,
    }


def parse_enum_values(body: str) -> List[Dict[str, str]]:
    """Extract enum enumerator names, values, and inline comments."""
    values: List[Dict[str, str]] = []
    pending_doc = ""
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("/**", "/*", "//")):
            clean_c = re.sub(r"^/\*\*?|\*/$|^///?", "", line).strip(" *")
            pending_doc = (pending_doc + " " + clean_c).strip() if pending_doc else clean_c
            continue
        # Strip inline comment
        inline_doc = ""
        cm = re.search(r"(?://|/\*)\s*(.*?)(?:\*/)?$", line)
        if cm:
            inline_doc = cm.group(1).strip()
            line = line[: cm.start()].strip()
        line = re.sub(r"\bUMETA\s*\([^)]*\)", "", line).strip().rstrip(",")
        if not line:
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\s*=\s*(.+))?$", line)
        if m:
            values.append({
                "name": m.group(1),
                "value": (m.group(2) or "").strip(),
                "doc": inline_doc or pending_doc,
            })
            pending_doc = ""
    return values
