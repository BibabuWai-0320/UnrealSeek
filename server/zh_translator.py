"""Plain-Chinese UE 5.8 API description generator for functions, properties, types, params, and returns."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .zh_curated import CURATED_ZH
from .zh_glossary import EN_TOKEN_ZH, PARAM_ZH_MAP, RETURN_TYPE_ZH

_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")

_VERB_PREFIX_ZH = {
    "get": "获取",
    "set": "设置",
    "is": "判断是否",
    "has": "判断是否包含/具备",
    "can": "判断是否可以",
    "should": "判断是否应该",
    "add": "添加/叠加",
    "remove": "移除",
    "clear": "清空/清除",
    "find": "查找",
    "query": "查询",
    "search": "搜索",
    "update": "更新",
    "refresh": "刷新",
    "recalculate": "重新计算",
    "recompute": "重新计算",
    "enable": "启用",
    "disable": "禁用",
    "toggle": "切换",
    "register": "注册",
    "unregister": "注销",
    "bind": "绑定",
    "unbind": "解绑",
    "spawn": "动态生成",
    "create": "创建",
    "make": "构造/生成",
    "build": "构建",
    "init": "初始化",
    "initialize": "初始化",
    "destroy": "销毁",
    "kill": "销毁/终止",
    "release": "释放",
    "free": "释放",
    "attach": "挂载",
    "detach": "分离解绑",
    "move": "移动",
    "teleport": "瞬移",
    "rotate": "旋转",
    "scale": "缩放",
    "apply": "应用",
    "reset": "重置",
    "restore": "恢复",
    "play": "播放",
    "pause": "暂停",
    "resume": "继续/恢复",
    "stop": "停止",
    "start": "开始/启动",
    "begin": "开始",
    "end": "结束",
    "finish": "完成",
    "compute": "计算",
    "calculate": "计算",
    "calc": "计算",
    "evaluate": "求值计算",
    "convert": "转换",
    "transform": "变换",
    "blend": "混合插值",
    "interp": "平滑插值",
    "lerp": "线性插值",
    "clamp": "限制范围",
    "check": "检查",
    "validate": "校验",
    "verify": "验证",
    "test": "检测",
    "trace": "射线检测",
    "sweep": "扫掠碰撞检测",
    "overlap": "重叠检测",
    "draw": "绘制",
    "render": "渲染",
    "show": "显示",
    "hide": "隐藏",
    "load": "加载",
    "save": "保存",
    "serialize": "序列化",
    "copy": "复制",
    "clone": "克隆",
    "sync": "同步",
    "replicate": "网络同步",
    "broadcast": "广播通知",
    "notify": "通知",
    "trigger": "触发",
    "dispatch": "派发",
    "handle": "处理",
    "process": "处理",
    "tick": "每帧更新",
    "on": "回调事件：当触发",
    "receive": "蓝图事件回调：当接收到",
    "post": "后置回调：",
    "pre": "前置回调：",
    "grab": "抓取",
    "lock": "锁定",
    "unlock": "解锁",
    "push": "压入/推送",
    "pop": "弹出",
    "append": "追加",
    "insert": "插入",
    "sort": "排序",
    "filter": "过滤",
    "flush": "刷新清空",
    "mark": "标记",
    "request": "请求",
    "cancel": "取消",
    "abort": "中止",
    "wait": "等待",
    "open": "打开",
    "close": "关闭",
    "read": "读取",
    "write": "写入",
    "parse": "解析",
    "format": "格式化",
    "log": "打印日志",
    "dump": "输出调试信息",
    "override": "覆盖设置",
    "inherit": "继承",
    "allow": "允许",
    "allows": "判断是否允许",
    "supports": "判断是否支持",
    "contains": "判断是否包含",
    "matches": "判断是否匹配",
    "equals": "判断是否相等",
}

_CONNECTOR_ZH = {
    "and": "与",
    "or": "或",
    "to": "到",
    "from": "从",
    "at": "在",
    "in": "在",
    "of": "的",
    "for": "用于",
    "by": "按",
    "with": "附带",
    "without": "不含",
    "into": "到",
    "as": "作为",
    "all": "全部",
    "any": "任意",
    "no": "无",
}


def _split_camel(name: str) -> List[str]:
    if not name:
        return []
    out: List[str] = []
    for part in re.split(r"[^A-Za-z0-9]+", name):
        if not part:
            continue
        toks = _CAMEL_RE.findall(part)
        out.extend(toks if toks else [part])
    return out


def _translate_tokens(tokens: List[str]) -> str:
    """Turn a sequence of English UE identifier tokens into readable Chinese phrase."""
    if not tokens:
        return ""
    pieces: List[str] = []
    i = 0
    while i < len(tokens):
        low = tokens[i].lower()
        nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
        # Common two-word UE idioms
        pair = f"{low} {nxt}" if nxt else ""
        if pair == "spring arm":
            pieces.append("弹簧臂")
            i += 2
            continue
        if pair == "line trace":
            pieces.append("射线检测")
            i += 2
            continue
        if pair == "view point":
            pieces.append("视角位置与朝向")
            i += 2
            continue
        if pair == "control rotation":
            pieces.append("控制器视角旋转")
            i += 2
            continue
        if pair == "camera lag":
            pieces.append("摄像机平滑延迟")
            i += 2
            continue
        if pair == "collision test":
            pieces.append("防穿墙碰撞检测")
            i += 2
            continue
        if pair == "world offset":
            pieces.append("世界空间偏移")
            i += 2
            continue
        if pair == "socket name":
            pieces.append("插槽名称")
            i += 2
            continue
        if pair == "spline point":
            pieces.append("样条线控制点")
            i += 2
            continue
        if pair == "along spline":
            pieces.append("沿样条线")
            i += 2
            continue
        if pair == "physics handle":
            pieces.append("物理抓取句柄")
            i += 2
            continue
        if pair == "timer handle":
            pieces.append("定时器句柄(时间令牌)")
            i += 2
            continue

        if low in EN_TOKEN_ZH:
            pieces.append(EN_TOKEN_ZH[low])
        elif low in _CONNECTOR_ZH:
            pieces.append(_CONNECTOR_ZH[low])
        elif low in _VERB_PREFIX_ZH:
            pieces.append(_VERB_PREFIX_ZH[low])
        else:
            pieces.append(tokens[i])
        i += 1

    # Clean redundant spaces between Chinese chars while keeping spaces around ASCII tokens
    joined = ""
    for p in pieces:
        if not joined:
            joined = p
        elif joined[-1].isascii() and joined[-1].isalnum() and p[0].isascii() and p[0].isalnum():
            joined += " " + p
        else:
            joined += p
    return joined


def _short_owner_zh(owner_name: str) -> str:
    if not owner_name:
        return ""
    if owner_name == "USpringArmComponent":
        return "弹簧臂"
    if owner_name == "USceneComponent":
        return "场景组件"
    if owner_name == "UActorComponent":
        return "组件"
    if owner_name == "AActor":
        return "Actor"
    if owner_name == "APawn":
        return "Pawn角色"
    if owner_name == "ACharacter":
        return "Character角色"
    if owner_name == "APlayerController":
        return "玩家控制器"
    if owner_name == "AController":
        return "控制器"
    if owner_name == "UCameraComponent":
        return "摄像机"
    if owner_name == "UWorld":
        return "当前世界"
    if owner_name == "UPhysicsHandleComponent":
        return "物理抓取句柄"
    if owner_name == "USplineComponent":
        return "样条线组件"
    if owner_name == "FTimerManager":
        return "定时器管理器"
    return owner_name


def describe_symbol_zh(
    name: str,
    kind: str,
    owner_name: str = "",
    return_type: str = "",
    params: List[Dict[str, Any]] | None = None,
    parent_name: str = "",
    is_blueprint: int = 0,
    is_deprecated: int = 0,
    doc_summary: str = "",
) -> str:
    """Generate a natural, plain-Chinese description for any UE 5.8 API symbol."""
    # 1. Curated exact match (`Owner::Name` then `Name`)
    if owner_name and f"{owner_name}::{name}" in CURATED_ZH:
        return CURATED_ZH[f"{owner_name}::{name}"]
    if name in CURATED_ZH:
        return CURATED_ZH[name]

    # 2. Constructors / Destructors
    if kind == "function" and owner_name:
        if name == owner_name:
            return f"{owner_name} 的构造函数，用于创建并初始化该类实例。"
        if name == "~" + owner_name:
            return f"{owner_name} 的析构函数，在对象销毁时自动清理释放资源。"
        if name.startswith("operator"):
            return f"C++ 运算符重载（{name}），用于支持该类型的快捷运算或比较。"

    # 3. Classes / Structs / Interfaces / Enums
    if kind in ("class", "struct", "interface", "enum"):
        raw_name = name
        prefix_hint = ""
        if len(name) >= 3 and name[0] in "AUFEIST" and name[1].isupper():
            raw_name = name[1:]
            prefix_hint = {
                "U": "UE 引擎对象类",
                "A": "场景 Actor 类",
                "F": "核心数据结构/工具类",
                "E": "枚举类型",
                "I": "接口类",
                "S": "Slate UI 控件类",
                "T": "模板容器/工具类",
            }.get(name[0], "")
        phrase = _translate_tokens(_split_camel(raw_name))
        if kind == "enum":
            return f"枚举类型（{name}）：定义与「{phrase}」相关的可选状态或模式选项。"
        parent_txt = f"，继承自 {parent_name}" if parent_name else ""
        kind_cn = {"class": "类", "struct": "结构体", "interface": "接口"}.get(kind, "类型")
        return f"{prefix_hint or kind_cn}（{name}）：负责「{phrase}」相关的数据与功能{parent_txt}。"

    # 4. Delegates
    if kind == "delegate":
        raw = name[1:] if len(name) >= 3 and name[0] == "F" and name[1].isupper() else name
        phrase = _translate_tokens(_split_camel(raw))
        return f"委托/事件回调签名（{name}）：用于绑定并监听「{phrase}」触发事件。"

    # 5. Properties / Member Variables
    if kind == "property":
        ret_low = (return_type or "").lower()
        is_bool = (
            (len(name) >= 2 and name[0] == "b" and name[1].isupper())
            or "bool" in ret_low
        )
        if is_bool and len(name) >= 2 and name[0] == "b" and name[1].isupper():
            phrase = _translate_tokens(_split_camel(name[1:]))
            return f"开关属性：控制是否{phrase}（设为 true 开启，false 关闭）。"
        phrase = _translate_tokens(_split_camel(name))
        owner_zh = f"{_short_owner_zh(owner_name)}的" if owner_name else ""
        type_hint = f"（类型：{return_type}）" if return_type else ""
        return f"属性配置：表示或控制{owner_zh}「{phrase}」{type_hint}。"

    # 6. Functions / Methods
    work_name = name
    tags: List[str] = []
    if work_name.startswith("K2_"):
        work_name = work_name[3:]
        tags.append("蓝图节点版")
    elif work_name.startswith("OnRep_"):
        work_name = work_name[6:]
        phrase = _translate_tokens(_split_camel(work_name))
        return f"网络同步回调函数：当属性「{phrase}」从服务端同步到客户端发生变化时自动触发。"
    elif work_name.endswith("_Implementation"):
        work_name = work_name[: -len("_Implementation")]
        tags.append("C++ 默认实现")
    elif work_name.endswith("_Validate"):
        work_name = work_name[: -len("_Validate")]
        tags.append("RPC 参数校验")

    tokens = _split_camel(work_name)
    if not tokens:
        return f"执行 {name} 操作。"

    first_low = tokens[0].lower()
    verb_zh = _VERB_PREFIX_ZH.get(first_low, "")
    rest_tokens = tokens[1:] if verb_zh else tokens
    obj_zh = _translate_tokens(rest_tokens)

    owner_zh = _short_owner_zh(owner_name)
    prefix = f"（{' / '.join(tags)}）" if tags else ""

    if verb_zh and obj_zh:
        if first_low in ("on", "receive", "post", "pre"):
            base = f"{prefix}{verb_zh}「{obj_zh}」时执行对应逻辑。"
        elif first_low in ("is", "has", "can", "should", "allows", "supports", "contains", "matches", "equals"):
            base = f"{prefix}{verb_zh}「{obj_zh}」（返回布尔值 true/false）。"
        else:
            of_owner = f"{owner_zh}的" if owner_zh and owner_zh not in obj_zh else ""
            base = f"{prefix}{verb_zh}{of_owner}「{obj_zh}」。"
    else:
        full_zh = _translate_tokens(tokens)
        base = f"{prefix}在 {owner_zh or '当前对象'} 上执行「{full_zh}」操作。"

    # Add helpful parameter / output hints for C++ clarity
    extras: List[str] = []
    if params:
        out_params = [
            p.get("name", "")
            for p in params
            if p.get("name", "").lower().startswith("out") or ("&" in p.get("type", "") and "const" not in p.get("type", ""))
        ]
        if out_params:
            extras.append(f"结果写入输出参数 {', '.join(x for x in out_params[:3] if x)}")
        pnames_low = {p.get("name", "").lower() for p in params}
        if "bsweep" in pnames_low:
            extras.append("可选开启扫掠碰撞检测")
        if "teleport" in pnames_low or "bteleport" in pnames_low:
            extras.append("支持设置物理瞬移模式")
    if is_blueprint and "蓝图节点版" not in tags:
        extras.append("可在蓝图中直接调用")
    if is_deprecated:
        extras.append("注意：该接口已在 UE 5.8 标记废弃")

    if extras:
        base = base.rstrip("。") + "（" + "；".join(extras) + "）。"
    return base


def describe_param_zh(param: Dict[str, Any]) -> str:
    """Generate a plain-Chinese description for a single C++ function parameter."""
    p_name = str(param.get("name", "") or "").strip()
    p_type = str(param.get("type", "") or "").strip()
    low = p_name.lower()

    if low in PARAM_ZH_MAP:
        return PARAM_ZH_MAP[low]

    # Strip common UE param prefixes `In` / `Out` / `b`
    is_out = low.startswith("out") or ("&" in p_type and "const" not in p_type)
    work = p_name
    if len(work) >= 3 and work.startswith("In") and work[2].isupper():
        work = work[2:]
    elif len(work) >= 4 and work.startswith("Out") and work[3].isupper():
        work = work[3:]
    elif len(work) >= 2 and work.startswith("b") and work[1].isupper():
        phrase = _translate_tokens(_split_camel(work[1:]))
        return f"布尔开关：是否{phrase}。"

    if work:
        phrase = _translate_tokens(_split_camel(work))
        if is_out:
            return f"输出参数：用于接收返回的「{phrase}」（类型 {p_type}）。"
        return f"输入参数：指定「{phrase}」（类型 {p_type}）。"
    return f"参数类型 {p_type}。"


def describe_return_zh(return_type: str, kind: str = "function") -> str:
    """Generate a plain-Chinese explanation for a function return value."""
    rt = (return_type or "").strip()
    if not rt:
        return "构造/析构函数，无显式返回值。"
    clean = rt
    for pfx in ("const ", "class ", "struct ", "enum "):
        if clean.startswith(pfx):
            clean = clean[len(pfx) :].strip()
    base = clean.split("<", 1)[0].rstrip("*& ").strip()
    low = base.lower()

    if low in RETURN_TYPE_ZH:
        return RETURN_TYPE_ZH[low]
    if clean.endswith("*"):
        return f"指向 {base} 对象的指针（使用前建议判空检查是否为 nullptr）。"
    if clean.startswith("TArray"):
        return f"动态数组（{rt}），包含一组符合条件的结果列表。"
    if clean.startswith("TMap"):
        return f"键值对映射表（{rt}）。"
    if clean.startswith("TSet"):
        return f"去重集合（{rt}）。"
    if clean.startswith("TSubclassOf"):
        return f"类类型引用（{rt}），指向该基类或其子类的 UClass。"
    if clean.startswith("TObjectPtr") or clean.startswith("TWeakObjectPtr"):
        return f"UE 对象智能指针（{rt}）。"
    return f"返回类型为 {rt} 的结果值或对象引用。"
