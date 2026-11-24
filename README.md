# Unreal Engine 5.8 C++ API Reference (Local Knowledge Base)

本地完整的 **Unreal Engine 5.8 C++ API 文档网站与智能检索知识库**，直接解析本机 UE 5.8 引擎与全部插件源码头文件（45,000+ `.h` 文件、1,030,000+ API 符号），还原经典 UE 4.7 双栏接口文档风格，并支持代码行模糊检索与中文自然语言语义搜索。

## 核心特性
- **经典 UE 4.7 双栏接口文档样式**：顶部深色导航栏 + 左侧可拖拽缩放索引树 + 右侧高信息密度文档区（支持浅色经典 / 暗黑主题切换）。
- **严格对齐 UE 5.8 官方头文件与 `*.Build.cs`（UBT）规则**：
  - 自动扫描全引擎 2,230+ 个 `*.Build.cs`，准确还原每个类的模块名、公开 `#include` 路径及非核心模块的 `PublicDependencyModuleNames.Add(...)` 依赖提示。
  - 自动过滤 `#if !CPP` 假头文件（如 `NoExportTypes.h`），正确解析 UE 5.8 LWC 数学类型（`FVector`、`FRotator`、`FQuat`、`FTransform`）及 `.h.inl` 核心容器（`FString`、`TMap`、`TSet`）。
  - 默认 `仅Public` 模式，避免误引用引擎内部 `Private/Internal` 头文件。
- **左侧双模式索引树 + 搜索自动定位联动**：
  - **模块目录索引**：`Runtime / Plugins / Editor / Developer / Programs -> Module -> Class (: ParentClass) -> Function / Property`（含可展开的“继承自父类”成员节点）。
  - **类继承树**：按真实 C++ 继承体系（`UObjectBase -> UObjectBaseUtility -> UObject -> AActor -> ...`）逐级浏览。
- **组件全量可用成员总览**：打开任意类/组件（如 `USpringArmComponent`），右侧统一展示**本类 + 全部父类继承**的可用函数与属性，并支持中英文即时筛选。
- **代码行模糊检索 & 中文/中英混合语义检索**：
  - 支持直接粘贴代码行，如 `X->GetLocationandRotation`、`PC->GetPlayerViewPoint(Loc, Rot);`、`GetActorLocatoin`。
  - 支持简繁体中文与中英混合搜索，如 `设置时间令牌`、`设置角色的local旋转与location`、`弹簧臂俯仰角跟随鼠标`、`射线检测`。
- **通俗中文接口说明**：全站函数、属性、参数与返回值均提供通俗中文解释，并保留可折叠的 UE 5.8 英文原注释与源码上下文预览。

## 快速开始

### 1. 安装依赖
需要 Python 3.10+（内置 `sqlite3` 支持 FTS5）：
```bash
pip install -r requirements.txt
```

### 2. 从本机 UE 5.8 引擎源码构建本地 SQLite 索引库
> 注：生成的 `data/ue58_api.db` 约 900 MB（受 GitHub 单文件 100 MB 限制及引擎源码许可约定，不随仓库分发，在本机约 1 分钟即可全量构建）。

```bash
python -m indexer.build_db --engine "D:\Epic Games\UE_5.8\Engine" --workers 20
```
（将 `--engine` 替换为你本机 UE 5.8 的 `Engine` 目录路径。）

### 3. 启动本地文档网站
Windows 直接双击 `start.bat`，或命令行运行：
```bash
python -m server.app --open-browser
```
浏览器将自动打开 `http://127.0.0.1:5858`。

## 目录结构
```text
ue58-api-docs/
├── indexer/
│   ├── build_cs.py        # UBT *.Build.cs 模块与公开 #include 路径解析器
│   ├── header_meta.py     # Doxygen 注释解析与驼峰拆词
│   ├── cpp_decl.py        # 函数/参数/属性/枚举声明解析
│   ├── cpp_parser.py      # 单遍 C++ 头文件词法解析器（支持跳过 #if !CPP）
│   ├── core_aliases.py    # UE 5.8 LWC 数学别名与 .h.inl 容器展开
│   └── build_db.py        # 多进程全量建库（SQLite + FTS5 分词/Trigram 索引）
├── server/
│   ├── zh_curated.py      # 高频核心 UE 5.8 API 精选通俗中文手册
│   ├── zh_glossary.py     # 中英双向 UE 专业术语与意图映射词库
│   ├── zh_translator.py   # 通俗中文函数/属性/参数/返回值说明生成引擎
│   ├── code_query.py      # 代码行解析、对象类推断与中文语义查询解析
│   ├── fuzzy_search.py    # 多路 SQLite FTS5 + 模糊/拼写/代码/中文检索排序引擎
│   ├── db_queries.py      # 目录树、类继承树、自动定位、父类继承成员与详情查询
│   └── app.py             # Flask Web 服务与 API 接口
├── web/
│   ├── index.html         # 经典 UE 4.7 双栏页面结构
│   ├── styles.css         # 经典浅色 / 暗黑主题样式
│   ├── detail.js          # 右侧详情页、头文件与 Build.cs 依赖、可用成员总览渲染
│   ├── tree.js            # 左侧模块/继承双模式索引树与搜索自动定位联动
│   └── search.js          # 顶部智能模糊与中文检索交互
├── start.bat              # Windows 一键启动脚本
├── requirements.txt
└── .gitignore
```
