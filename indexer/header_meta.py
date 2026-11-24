"""Header path metadata, Doxygen comment parsing, and identifier tokenization for UE 5.8."""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple


def extract_header_meta(abs_path: str, engine_root: str) -> Dict[str, str]:
    """Extract category, plugin, module, visibility, and #include path from a UE header path."""
    rel = os.path.relpath(abs_path, engine_root).replace("\\", "/")
    parts = rel.split("/")

    category = "Other"
    plugin_name = ""
    module_name = "Unknown"
    visibility = "Other"
    include_path = parts[-1]

    if len(parts) >= 3 and parts[0] == "Source":
        category = parts[1]  # Runtime, Editor, Developer, Programs
        if len(parts) >= 3:
            module_name = parts[2]
        rest = parts[3:]
        visibility, include_path = _extract_visibility_and_include(rest, module_name)
    elif len(parts) >= 2 and parts[0] == "Plugins":
        category = "Plugins"
        # Find 'Source' segment inside Plugins/.../Source/<Module>/...
        if "Source" in parts:
            src_idx = parts.index("Source")
            plugin_parts = parts[1:src_idx]
            plugin_name = "/".join(plugin_parts) if plugin_parts else parts[1]
            if src_idx + 1 < len(parts):
                module_name = parts[src_idx + 1]
                rest = parts[src_idx + 2 :]
                visibility, include_path = _extract_visibility_and_include(rest, module_name)
        else:
            plugin_name = parts[1]
            module_name = parts[1]
            include_path = "/".join(parts[2:]) if len(parts) > 2 else parts[-1]

    return {
        "rel_path": "Engine/" + rel,
        "abs_path": abs_path,
        "category": category,
        "plugin_name": plugin_name,
        "module": module_name,
        "visibility": visibility,
        "include_path": include_path,
        "file_name": parts[-1],
    }


def _extract_visibility_and_include(rest: List[str], module_name: str) -> Tuple[str, str]:
    if not rest:
        return "Other", module_name
    for idx, seg in enumerate(rest[:-1]):
        if seg in ("Public", "Classes"):
            return "Public", "/".join(rest[idx + 1 :])
        if seg == "Internal":
            return "Internal", "/".join(rest[idx + 1 :])
        if seg == "Private":
            return "Private", "/".join(rest[idx + 1 :])
    # No Public/Classes/Private folder (common in some Runtime modules)
    return "Public", "/".join(rest)


_TAG_RE = re.compile(r"^[@\\](param(?:\[[^\]]*\])?|return|returns|retval|see|note|warning|deprecated)\b\s*(.*)$", re.I)


def parse_doxygen(raw_comment: str) -> Dict[str, object]:
    """Parse a raw C++ / Doxygen comment block into structured fields."""
    if not raw_comment:
        return {"summary": "", "remarks": "", "params": {}, "returns": "", "see": []}

    lines: List[str] = []
    for raw_line in raw_comment.splitlines():
        line = raw_line.strip()
        if line.startswith("/**"):
            line = line[3:]
        elif line.startswith("/*"):
            line = line[2:]
        if line.endswith("*/"):
            line = line[:-2]
        if line.startswith("///"):
            line = line[3:]
        elif line.startswith("//"):
            line = line[2:]
        elif line.startswith("*"):
            line = line[1:]
        lines.append(line.strip())

    # Trim leading/trailing empty lines
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    desc_lines: List[str] = []
    params: Dict[str, str] = {}
    returns_parts: List[str] = []
    see_list: List[str] = []

    current_tag: Optional[str] = None
    current_param: Optional[str] = None

    for line in lines:
        if not line:
            if current_tag is None and desc_lines and desc_lines[-1] != "":
                desc_lines.append("")
            continue

        m = _TAG_RE.match(line)
        if m:
            tag = m.group(1).lower()
            body = m.group(2).strip()
            if tag.startswith("param"):
                current_tag = "param"
                parts = body.split(None, 1)
                if parts:
                    current_param = parts[0].strip(",:")
                    params[current_param] = parts[1].strip() if len(parts) > 1 else ""
                else:
                    current_param = None
            elif tag in ("return", "returns", "retval"):
                current_tag = "return"
                current_param = None
                if body:
                    returns_parts.append(body)
            elif tag == "see":
                current_tag = "see"
                current_param = None
                if body:
                    see_list.append(body)
            elif tag in ("note", "warning"):
                current_tag = None
                current_param = None
                desc_lines.append(f"{tag.capitalize()}: {body}" if body else f"{tag.capitalize()}:")
            else:
                current_tag = None
                current_param = None
                if body:
                    desc_lines.append(body)
        else:
            if current_tag == "param" and current_param:
                prev = params.get(current_param, "")
                params[current_param] = (prev + " " + line).strip()
            elif current_tag == "return":
                returns_parts.append(line)
            elif current_tag == "see":
                see_list.append(line)
            else:
                desc_lines.append(line)

    # Split description into first paragraph (summary) and remaining (remarks)
    paragraphs: List[str] = []
    buf: List[str] = []
    for dl in desc_lines:
        if dl == "":
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
        else:
            buf.append(dl)
    if buf:
        paragraphs.append(" ".join(buf))

    summary = paragraphs[0] if paragraphs else ""
    remarks = "\n\n".join(paragraphs[1:]) if len(paragraphs) > 1 else ""

    return {
        "summary": summary,
        "remarks": remarks,
        "params": params,
        "returns": " ".join(returns_parts).strip(),
        "see": see_list,
    }


_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")


def tokenize_identifier(name: str) -> Tuple[str, str]:
    """Return (space_separated_lower_tokens, camel_initials_lower) for search indexing."""
    if not name:
        return "", ""
    parts = re.split(r"[^A-Za-z0-9]+", name)
    tokens: List[str] = []
    for part in parts:
        if not part:
            continue
        sub = _CAMEL_RE.findall(part)
        if sub:
            tokens.extend(t.lower() for t in sub)
        else:
            tokens.append(part.lower())
    initials = "".join(t[0] for t in tokens if t)
    return " ".join(tokens), initials
