"""C++ code-line query parser, receiver class hints, and UE vocabulary word segmenter."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .zh_curated import CURATED_ZH
from .zh_glossary import TRAD_TO_SIMP, ZH_INTENT_MAP, ZH_WORD_MAP

_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
_HAS_ZH_RE = re.compile(r"[\u4e00-\u9fff]")
_ZH_FILLER_RE = re.compile(
    r"(如何|怎么|怎样|我想要|我想|想要|知道|请问|帮忙|找一下|查一下|怎么用|"
    r"一个|一下|哪些|哪个|什么|对应|相关|函数|接口|方法|属性|实现|用来|可以|能够|怎么让|让|把|给|的|与|和|及|并|跟)"
)

# Common Unreal Engine variable / abbreviation -> likely owner classes
RECEIVER_HINTS: Dict[str, List[str]] = {
    "world": ["UWorld"],
    "getworld": ["UWorld"],
    "actor": ["AActor"],
    "myactor": ["AActor"],
    "owner": ["AActor"],
    "getowner": ["AActor"],
    "pawn": ["APawn", "ACharacter"],
    "character": ["ACharacter", "APawn"],
    "char": ["ACharacter"],
    "pc": ["APlayerController", "AController"],
    "playercontroller": ["APlayerController", "AController"],
    "controller": ["AController", "APlayerController", "AAIController"],
    "ai": ["AAIController"],
    "aicontroller": ["AAIController"],
    "camera": ["UCameraComponent", "APlayerCameraManager"],
    "cameracomp": ["UCameraComponent"],
    "cameramanager": ["APlayerCameraManager"],
    "pcm": ["APlayerCameraManager"],
    "springarm": ["USpringArmComponent"],
    "springarmcomp": ["USpringArmComponent"],
    "springarmcomponent": ["USpringArmComponent"],
    "cameraboom": ["USpringArmComponent"],
    "boom": ["USpringArmComponent"],
    "mesh": ["USkeletalMeshComponent", "UStaticMeshComponent", "UMeshComponent", "USceneComponent"],
    "skeletalmesh": ["USkeletalMeshComponent"],
    "skmesh": ["USkeletalMeshComponent"],
    "staticmesh": ["UStaticMeshComponent"],
    "smc": ["UStaticMeshComponent"],
    "spline": ["USplineComponent"],
    "splinecomp": ["USplineComponent"],
    "splinecomponent": ["USplineComponent"],
    "root": ["USceneComponent"],
    "rootcomp": ["USceneComponent"],
    "rootcomponent": ["USceneComponent"],
    "scene": ["USceneComponent"],
    "scenecomp": ["USceneComponent"],
    "comp": ["UActorComponent", "USceneComponent", "UPrimitiveComponent"],
    "component": ["UActorComponent", "USceneComponent", "UPrimitiveComponent"],
    "prim": ["UPrimitiveComponent"],
    "primitive": ["UPrimitiveComponent"],
    "movement": ["UCharacterMovementComponent", "UMovementComponent", "UPawnMovementComponent"],
    "cmc": ["UCharacterMovementComponent"],
    "charmovement": ["UCharacterMovementComponent"],
    "anim": ["UAnimInstance", "UAnimSequence"],
    "animinst": ["UAnimInstance"],
    "animinstance": ["UAnimInstance"],
    "hud": ["AHUD"],
    "gm": ["AGameModeBase", "AGameMode"],
    "gamemode": ["AGameModeBase", "AGameMode"],
    "gs": ["AGameStateBase", "AGameState"],
    "gamestate": ["AGameStateBase", "AGameState"],
    "gi": ["UGameInstance"],
    "gameinstance": ["UGameInstance"],
    "ps": ["APlayerState"],
    "playerstate": ["APlayerState"],
    "widget": ["UUserWidget", "UWidget"],
    "input": ["UEnhancedInputComponent", "UInputComponent"],
    "enhancedinput": ["UEnhancedInputComponent"],
    "asc": ["UAbilitySystemComponent"],
    "abilitysystem": ["UAbilitySystemComponent"],
    "physicshandle": ["UPhysicsHandleComponent"],
    "capsule": ["UCapsuleComponent"],
    "sphere": ["USphereComponent"],
    "box": ["UBoxComponent"],
    "niagara": ["UNiagaraComponent", "UNiagaraFunctionLibrary"],
    "audio": ["UAudioComponent"],
    "widgetcomp": ["UWidgetComponent"],
    "mat": ["UMaterialInstanceDynamic", "UMaterialInterface"],
    "mid": ["UMaterialInstanceDynamic"],
    "fmath": ["FMath"],
    "math": ["FMath", "UKismetMathLibrary"],
    "kismet": ["UKismetSystemLibrary", "UKismetMathLibrary", "UGameplayStatics"],
    "gameplaystatics": ["UGameplayStatics"],
    "statics": ["UGameplayStatics"],
    "transform": ["FTransform"],
    "vec": ["FVector"],
    "vector": ["FVector"],
    "rot": ["FRotator"],
    "rotator": ["FRotator"],
    "quat": ["FQuat"],
    "str": ["FString"],
    "string": ["FString"],
    "arr": ["TArray"],
    "array": ["TArray"],
    "map": ["TMap"],
    "timer": ["FTimerManager"],
    "timermanager": ["FTimerManager"],
    "getworldtimermanager": ["FTimerManager"],
}

GENERIC_RECEIVERS = {
    "x", "y", "z", "obj", "object", "ptr", "p", "this", "self", "it", "item",
    "target", "src", "dst", "other", "inst", "instance", "ref", "val", "a", "b",
}


@dataclass
class ParsedQuery:
    raw: str
    is_code_expr: bool = False
    is_zh_query: bool = False
    zh_terms: List[str] = field(default_factory=list)
    zh_boost_symbols: List[str] = field(default_factory=list)
    receiver_raw: str = ""
    receiver_hints: List[str] = field(default_factory=list)
    operator: str = ""  # "->", ".", "::", or ""
    target: str = ""
    target_lower: str = ""
    words: List[str] = field(default_factory=list)
    arg_count: Optional[int] = None
    prefer_kind: str = ""  # "function", "class", etc.
    explanation: str = ""


class WordSegmenter:
    """Viterbi DP segmenter built from UE 5.8 identifier vocabulary."""

    def __init__(self, vocab: Dict[str, int]) -> None:
        self.vocab = vocab
        total = sum(vocab.values()) or 1
        self.log_total = math.log(total)
        self.max_len = min(20, max((len(w) for w in vocab), default=12))

    def _word_cost(self, word: str) -> float:
        freq = self.vocab.get(word, 0)
        if freq > 0:
            # Slightly prefer longer genuine words over splitting into tiny 2-char pieces
            return self.log_total - math.log(freq) - 0.35 * len(word)
        return self.log_total + 6.0 + 1.5 * len(word)

    def segment_chunk(self, chunk: str) -> List[str]:
        s = chunk.lower()
        n = len(s)
        if n <= 3 or s in self.vocab and self.vocab[s] >= 15:
            return [s]
        dp: List[Tuple[float, int]] = [(float("inf"), 0)] * (n + 1)
        dp[0] = (0.0, 0)
        for i in range(1, n + 1):
            start_min = max(0, i - self.max_len)
            best_cost = float("inf")
            best_j = i - 1
            for j in range(start_min, i):
                w = s[j:i]
                if len(w) == 1 and w not in ("a", "i", "x", "y", "z", "2", "3", "4"):
                    c = dp[j][0] + self.log_total + 10.0
                else:
                    c = dp[j][0] + self._word_cost(w)
                if c < best_cost:
                    best_cost = c
                    best_j = j
            dp[i] = (best_cost, best_j)
        words: List[str] = []
        idx = n
        while idx > 0:
            prev = dp[idx][1]
            words.append(s[prev:idx])
            idx = prev
        words.reverse()
        return words

    def tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        raw_parts = re.split(r"[^A-Za-z0-9]+", text)
        out: List[str] = []
        for p in raw_parts:
            if not p:
                continue
            camel_parts = _CAMEL_RE.findall(p) or [p]
            for cp in camel_parts:
                low = cp.lower()
                if len(low) >= 5 and self.vocab.get(low, 0) < 12:
                    out.extend(self.segment_chunk(low))
                else:
                    out.append(low)
        return [w for w in out if w]


def _count_args(args_str: str) -> int:
    s = args_str.strip()
    if not s:
        return 0
    depth = 0
    in_str: Optional[str] = None
    count = 1
    i = 0
    while i < len(s):
        ch = s[i]
        if in_str:
            if ch == "\\" and i + 1 < len(s):
                i += 2
                continue
            if ch == in_str:
                in_str = None
        else:
            if ch in ('"', "'"):
                in_str = ch
            elif ch in "([{<":
                depth += 1
            elif ch in ")]}>":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                count += 1
        i += 1
    return count


def _infer_receiver_hints(receiver_raw: str) -> List[str]:
    if not receiver_raw:
        return []
    # Keep the last identifier in chained expressions like GetPlayerController()->...
    cleaned = re.sub(r"\([^)]*\)", "", receiver_raw).strip()
    m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)$", cleaned)
    ident = m.group(1) if m else cleaned
    low = ident.lower()
    if low in GENERIC_RECEIVERS or len(low) <= 1:
        return []

    hints: List[str] = []
    # Exact hint table match
    if low in RECEIVER_HINTS:
        hints.extend(RECEIVER_HINTS[low])

    # Direct UE class name pattern (AActor, UActorComponent, FVector, USplineComponent, IInterface)
    if re.match(r"^[AUFSIE][A-Z][A-Za-z0-9_]+$", ident) and ident not in hints:
        hints.insert(0, ident)

    # Partial heuristic suffix/substring match on receiver identifier (e.g. MySplineComp -> USplineComponent)
    if not hints:
        for key, classes in RECEIVER_HINTS.items():
            if len(key) >= 4 and key in low:
                for cls in classes:
                    if cls not in hints:
                        hints.append(cls)
    return hints[:5]


def _parse_zh_query(raw_query: str, segmenter: WordSegmenter) -> ParsedQuery:
    """Parse Chinese or mixed Chinese+English UE queries (e.g. `设置时间令牌`, `设置角色的local旋转与location`)."""
    pq = ParsedQuery(raw=raw_query, is_zh_query=True)
    simp = raw_query.translate(TRAD_TO_SIMP).strip()

    # 1. Extract any mixed English identifiers/tokens (e.g. `local`, `location`, `SpringArm`, `Pitch`)
    en_tokens: List[str] = []
    for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", simp):
        for tok in segmenter.tokenize(m.group(0)):
            if tok not in en_tokens:
                en_tokens.append(tok)
            if tok == "local" and "relative" not in en_tokens:
                en_tokens.append("relative")

    # Remove English pieces & filler words for Chinese matching
    zh_only = re.sub(r"[A-Za-z0-9_\s\-.,;:!?()\[\]{}<>/\\]+", " ", simp)
    zh_clean = _ZH_FILLER_RE.sub(" ", zh_only)
    zh_terms: List[str] = []
    boost_syms: List[str] = []

    def add_tok(t: str) -> None:
        if t and t not in en_tokens:
            en_tokens.append(t)

    def add_boost(sym: str) -> None:
        if sym and sym not in boost_syms:
            boost_syms.append(sym)

    # 2. Match longest Chinese intent phrases first
    consumed = zh_clean
    for phrase, toks, syms in sorted(ZH_INTENT_MAP, key=lambda x: -len(x[0])):
        if phrase in consumed:
            zh_terms.append(phrase)
            for t in toks:
                add_tok(t)
            for s in syms:
                add_boost(s)
            consumed = consumed.replace(phrase, " ")

    # 3. Match remaining 2-char and 1-char Chinese words from ZH_WORD_MAP
    for zh_word, toks in sorted(ZH_WORD_MAP.items(), key=lambda x: -len(x[0])):
        if zh_word in consumed:
            zh_terms.append(zh_word)
            for t in toks:
                add_tok(t)
            consumed = consumed.replace(zh_word, " ")

    # Keep any remaining non-empty Chinese fragments for matching CURATED_ZH descriptions
    for frag in re.findall(r"[\u4e00-\u9fff]{2,}", consumed):
        if frag not in zh_terms:
            zh_terms.append(frag)

    # 4. Also find curated UE 5.8 symbols whose plain-Chinese manual matches our Chinese terms!
    if zh_terms:
        curated_hits: List[Tuple[int, str]] = []
        for key, desc in CURATED_ZH.items():
            hits = sum(1 for t in zh_terms if t in desc)
            # Also reward matching English tokens in symbol name
            key_low = key.lower()
            en_hits = sum(1 for et in en_tokens if len(et) >= 3 and et in key_low)
            total_h = hits * 2 + en_hits
            if hits > 0 and total_h >= 2:
                curated_hits.append((total_h, key))
        curated_hits.sort(key=lambda x: -x[0])
        for _, k in curated_hits[:14]:
            add_boost(k)

    # Set receiver hints if user mentioned a specific UE class/component in Chinese
    for tok in ("spring", "camera", "spline", "player", "character", "actor", "world", "timer"):
        if tok == "spring" and "arm" in en_tokens:
            if "USpringArmComponent" not in pq.receiver_hints:
                pq.receiver_hints.append("USpringArmComponent")
        elif tok == "timer" and "FTimerManager" not in pq.receiver_hints:
            pq.receiver_hints.extend(["FTimerManager", "FTimerHandle"])

    pq.zh_terms = zh_terms
    pq.zh_boost_symbols = boost_syms[:18]
    pq.words = en_tokens[:10]
    pq.target = " ".join(en_tokens) if en_tokens else simp
    pq.target_lower = pq.target.lower()

    parts = ["中文语义检索"]
    if pq.words:
        parts.append(f"映射英文关键词: {' / '.join(pq.words[:7])}")
    if pq.zh_boost_symbols:
        parts.append(f"核心关联: {', '.join(pq.zh_boost_symbols[:4])}")
    pq.explanation = " · ".join(parts)
    return pq


def parse_code_query(raw_query: str, segmenter: WordSegmenter) -> ParsedQuery:
    """Parse raw search input, Chinese natural query, or pasted C++ code line."""
    q = raw_query.strip()
    pq = ParsedQuery(raw=q)
    if not q:
        return pq

    # Chinese or mixed Chinese+English query (unless it's purely a C++ code line with `//` comment)
    code_no_cmt = re.sub(r"//.*$", "", q).strip()
    if _HAS_ZH_RE.search(code_no_cmt):
        return _parse_zh_query(q, segmenter)

    # Strip C++ comments and trailing semicolon
    work = re.sub(r"//.*$", "", q).strip()
    work = re.sub(r"/\*.*?\*/", "", work).strip()
    work = work.rstrip(";").strip()

    # Strip control-flow wrapper like `if (expr)` or `return expr`
    work = re.sub(r"^(?:return|co_return|await)\s+", "", work).strip()
    m_ctrl = re.match(r"^(?:if|while)\s*\((.+)\)$", work)
    if m_ctrl:
        work = m_ctrl.group(1).strip()

    # Strip assignment LHS like `const FVector Loc = X->GetActorLocation()`
    # Only strip '=' if not '==' / '!=' / '<=' / '>=' and before any '('
    eq_m = re.match(r"^[A-Za-z0-9_:<>*&\s]+?\s*=\s*(.+)$", work)
    if eq_m and not eq_m.group(1).startswith("="):
        work = eq_m.group(1).strip()

    # Strip leading negation/deref/cast like `!`, `*`, `&`
    work = work.lstrip("!*&~").strip()

    # Extract call expression: `[receiver] (-> | . | ::) ident [ (args) ]`
    call_re = re.compile(
        r"^(?:(?P<recv>.+?)\s*(?P<op>->|\.|::)\s*)?"
        r"(?P<ident>[~]?[A-Za-z_][A-Za-z0-9_]*)"
        r"(?:\s*\((?P<args>.*)\))?$"
    )
    m = call_re.match(work)
    if m and (m.group("op") or m.group("args") is not None or work != q):
        recv = (m.group("recv") or "").strip()
        op = m.group("op") or ""
        ident = m.group("ident")
        args = m.group("args")

        pq.is_code_expr = bool(op or args is not None)
        pq.receiver_raw = recv
        pq.operator = op
        pq.target = ident
        pq.target_lower = ident.lower()
        pq.receiver_hints = _infer_receiver_hints(recv)
        if args is not None:
            pq.arg_count = _count_args(args)
            pq.prefer_kind = "function"
        elif op in ("->", "."):
            pq.prefer_kind = "function"
        elif op == "::":
            pq.prefer_kind = "function"

        pq.words = segmenter.tokenize(ident)

        # Build human-readable match explanation
        parts = []
        if op and recv:
            if pq.receiver_hints:
                parts.append(f"对象 `{recv}` (推测类: {', '.join(pq.receiver_hints[:2])})")
            else:
                parts.append(f"任意对象 `{recv}`")
        parts.append(f"目标标识 `{ident}`")
        if pq.arg_count is not None:
            parts.append(f"参数个数 {pq.arg_count}")
        if pq.words:
            parts.append(f"分词: {' / '.join(pq.words)}")
        pq.explanation = " · ".join(parts)
        return pq

    # Fallback: regular keyword or multi-word natural search
    pq.target = work
    pq.target_lower = work.lower()
    pq.words = segmenter.tokenize(work)
    return pq
