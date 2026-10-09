# Day 3：配置、日志与入口

## 今日任务

把「启动失败」变成「可诊断的信息」。Agent 的大量功能故障，本质是配置优先级混乱、
缺失凭据或编码问题。今天落地三件事：配置（合并出有效运行配置）、日志（轮转 + 关键
路径事件 + 脱敏）、入口（main 与 headless 两个最小入口）。

## 任务分析

### 为什么 Day 3 要处理「配置」这个看起来不性感的话题

一个 Agent Runtime 真正跑起来后，第一类报错几乎都不是「逻辑写错了」，而是「配置没
读对」——模型名拼错、凭据没设、base_url 少了个斜杠、编码不对。这些问题如果每次都由
使用者在报错堆栈里自己猜，体验极差。Day 3 的目标就是：**让配置错误在启动阶段就被
结构化地暴露出来，而不是等到半路崩成一团乱麻**。

### 为什么把「配置」做成一个收敛的合并动作

如果每个模块都自己去 `os.environ.get(...)`，会出现三个问题：来源散落（优先级不透明）、
无法测试（散落的读取没法注入假环境）、无法审计（追不回「用了哪份配置」）。所以正确做法
是收敛：把「默认值 ← 用户级 settings ← 项目级 settings ← .env/环境变量 ← CLI 参数」这一串
来源，用一个合并函数合成一份「有效运行配置」。之后所有模块只读这一份，不再各自碰环境。

## 代码解释（框架长什么样）

### 1. 配置：一份不可变的 RuntimeConfig + 纯函数

配置的核心是一份 `RuntimeConfig`，加上合并、校验、脱敏、加载四个纯函数：

```python
@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    model: str = "mock"          # 模型名；"mock" 表示离线 Mock
    base_url: str = ""           # Provider API 地址
    api_key: str = ""            # 凭据；展示/记录必须脱敏
    tool_profile: str = "core"   # 工具画像：core / full


def merge_config(base, **overrides):
    # 只接受已知字段；None/空串视为「未提供」，不覆盖 base
    clean = {k: v for k, v in overrides.items() if k in RuntimeConfig.__dataclass_fields__ and v not in (None, "")}
    return replace(base, **clean)   # 纯函数：返回新配置，不动 base


def validate_config(config) -> list[str]:
    errors = []
    if config.model != "mock" and not config.api_key:   # 真实模型必须带凭据
        errors.append("api_key is required for a real model")
    if config.base_url and urlparse(config.base_url).scheme not in {"http", "https"}:
        errors.append("base_url must use http or https")
    return errors


def safe_snapshot(config) -> dict:
    masked = "not set" if not config.api_key else f"***{config.api_key[-4:]}"   # 只露末四位
    return {"model": config.model, "base_url": config.base_url, "api_key": masked, ...}


def load_runtime_config(env=None):
    load_dotenv()                              # 先把 .env 合并进 os.environ
    env = env if env is not None else os.environ
    return RuntimeConfig(model=env.get("MINI_CODE_MODEL", "mock"), ...)
```

这一段就是「配置子系统」的全貌：**一份不可变配置 + 四个纯函数**（合并、校验、脱敏、
加载）。看懂这段，就看懂了「配置从哪来、怎么合成、怎么校验、怎么安全展示」。

### 2. 日志：一个根 logger + 分级 handler + 关键路径 helper

```python
def setup_logging(level="INFO", log_to_file=True, log_to_console=True):
    root = logging.getLogger("minicode")
    root.handlers.clear()                      # 避免重复调用时日志重复
    # 文件 handler：RotatingFileHandler，10MB × 5 轮转，记录所有级别（DEBUG 兜底）
    # 控制台 handler：只显示 INFO 及以上（关键节点，不刷屏）

def get_logger(name):                          # 子 logger：minicode.tools 等
    return logging.getLogger(f"minicode.{name}")

def log_tool_execution(...): ...   # Day 5 用
def log_api_call(...): ...         # Day 10 用
def log_permission_check(...): ... # Day 11 用
def log_session_event(...): ...    # Day 15 用
```

日志子系统 = 「一个根 logger + 分级 handler（控制台 INFO / 文件 DEBUG）+ 四个关键路径
helper」。四个 helper 是给后续 Day 占好的位置，今天只定义、不调用。

### 3. 入口：main 和 headless 两个薄壳

```python
# main.py：交互入口
def main(argv=None):
    args = parser.parse_args(argv)
    setup_logging(level=args.log_level)
    if args.validate_config:
        config = load_runtime_config()
        snapshot = safe_snapshot(config)        # 脱敏后才打印
        errors = validate_config(config)
        # 打印诊断 → 有错返回 1，没错返回 0

# headless.py：非交互入口
def main(argv=None):
    if args.validate_config:
        return 0 if not validate_config(load_runtime_config()) else 1   # 只返回退出码
```

两个入口今天都只是「读参数 → 加载配置 → 校验 → 返回退出码」的薄壳，真正让 Agent 干活
的 Loop 要到 Day 7 才接进来。

## 核心设计（每个设计的 WHY）

### 1. 为什么 RuntimeConfig 必须是不可变的

配置一旦生成，就应该「一次生成、到处只读」。如果可变，任何模块在运行中途改一个字段
（比如顺手把 `model` 改成 `mock`），你很难排查「谁改的、什么时候改的」。`frozen` 从类型
层面掐掉「偷偷改配置」的可能——想改只能显式基于旧配置生成新配置。

### 2. 为什么 merge_config 要做成纯函数

纯函数可预测、可测试：同一组入参永远同一结果、不产生副作用。配置合并是启动路径的一环，
如果它偷偷改了入参、或有隐式状态，测试就难写（得先清理再断言）。纯函数让「合并」行为
完全确定：传入 base 和 overrides，返回新配置，base 原封不动。

### 3. 为什么 overrides 要「跳过 None 和空串」

调用方可能传进一堆「没填」的值（CLI 参数没传默认是 `None` 或空串）。如果这些空值也覆盖
进去，就会把「上一层已配好的值」冲掉。所以规则是：**只有非空的覆盖值才生效**，`None` 和
空串视为「这个来源没提供」。这也是「优先级」能成立的前提——每个来源只覆盖它真正填的。

### 4. 为什么校验要区分 mock 和真实模型

`validate_config` 只对「非 mock」要求 `api_key`。因为 Day 4 的 Mock 完全离线、不需要密钥，
如果一刀切都要求密钥，「用 Mock 演示/测试」这件最该轻松的事反而被卡住。校验的粒度要跟
「真实运行需要什么」对齐，而不是「所有模型一个样」——这为 Day 10 接入真实 Provider 留好
了「mock / 真实」的分界。

### 5. 为什么脱敏只留末四位

`safe_snapshot` 显示 `***abcd`。脱敏不是「完全不显示」，而是「显示到能识别、但不足以滥用」
——留末四位让用户配了多个 key 时能认出「当前生效的是哪个」，但完整密钥绝不进日志、快照。
核心原则：**任何可能被轮转、排查、分享的输出，都不能包含完整密钥**。

### 6. 为什么日志要「控制台 INFO、文件 DEBUG」两级

控制台给「正在操作的人」实时看，只显示关键节点（INFO），否则海量 DEBUG 会淹没真正的错误；
文件给「事后排查」看，记录所有级别（DEBUG）兜底。一句话：**实时看关键，事后看全量**。

### 7. 为什么日志 helper 要一次性定义好

四个 helper（工具/API/权限/会话）Day 3 就占好位置，对应 Day 5/10/11/15 的关键路径。意义是
**日志的格式、级别、脱敏规则只写一次，后续直接调用**，而不是每到一个新模块临时拼，格式
不统一、脱敏容易漏。

### 8. 为什么入口要拆成 main 和 headless

main 交互式给人用（读键盘、显示 TUI、打印诊断），headless 非交互给机器用（读参数、只返回
退出码）。混在一起，headless 会打印一堆没人看的交互输出（污染 CI 日志）。拆开让边界干净：
**交互入口管体验，非交互入口管契约（退出码）**。

### 9. 一个真实的坑：types.py 与标准库重名

`types` 是 Python 标准库模块名。从 `minicode/` 目录内直接 `python main.py` 运行时，
`sys.path[0]` 变成 `minicode/`，标准库内部的 `from types import ...` 会错误导入我们的
`minicode/types.py`，触发循环导入。教训：**包内模块名避开标准库名**，或永远从项目根以
包方式运行（`python -m minicode.main`）。

### 10. 渐进式：Day 3 只做最小，不阻塞主链路

Day 3 只做「不可变配置 + 纯函数合并/校验/脱敏 + 环境变量加载」，provider 检测、fallback、
MCP scope 留到 Day 10+。原则：**别为「完美配置」阻塞主链路**——Agent 核心是「模型 ↔ 工具」
闭环，配置是支撑性的，先做「够用且正确」的。

## 这三个模块在 Agent Runtime 里的位置

### 配置：整个 runtime 的「入口参数」

`RuntimeConfig` 是整个 Agent 运行时的**单一入口参数**。一次运行从哪来？就是「环境变量/.env
合成一份 RuntimeConfig，然后这份配置决定模型是什么（`model`/`base_url`/`api_key`）、工具加载
什么画像（`tool_profile`）」。

这份配置会被 Day 10 的 `model_registry.build_adapter(config)` 消费——根据 `model` 判断走 Mock
还是真实 Provider。到 Day 21 的 runtime factory，`RuntimeConfig` 更是「装配整个 runtime」的
起点：`config → adapter + tools + permissions + loop + kernel`。

### 日志：整个系统的「可观测性骨架」

`logging_config.py` 建立的是全系统共享的日志体系：一个 `minicode` 根 logger + 各子 logger。
后续每个 Day 的关键路径日志点都挂在它上面：Day 5 工具执行 → `log_tool_execution`，Day 10
API 调用 → `log_api_call`（记 token/成本、绝不记密钥），Day 11 权限 → `log_permission_check`，
Day 15 会话 → `log_session_event`。可观测性是横切所有模块的，Day 3 先立骨架，后面往上挂肉。

### 入口：runtime 的「外壳」，真正的工作在 Loop

`main.py`/`headless.py` 今天只是「外壳」——读参数、校验配置、打印诊断。真正让 Agent 工作的
Loop 要到 Day 7 才出现。这个「外壳」定义了「一次运行」的边界：启动 → 读配置 → 校验 →（未来）
装配 Loop 跑任务 → 退出码。Day 21 的 factory 会把「装配」从入口里抽出来，让入口更薄。

## 数据流

一次「启动 → 校验」的数据流：

```
.env / 环境变量
    │  load_runtime_config()
    ▼
RuntimeConfig（不可变的一份）
    ├── validate_config() ──► 错误列表 ──► main 打印诊断 / headless 返回退出码
    └── safe_snapshot() ──► 脱敏快照 ──► 展示（密钥只露末四位）
```

到 Day 10 延伸成 `RuntimeConfig → build_adapter → 真实 Adapter`；到 Day 21 延伸成
`RuntimeConfig → 装配出完整 runtime`。Day 3 是这条链的最前端——把「原始环境变量」变成
「一份可靠配置」。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 空环境 / Mock / 错误 URL / 缺失密钥分别有确定输出 | ✅ `test_config.py` 覆盖 |
| 日志中不出现完整密钥 | ✅ `safe_snapshot` 脱敏 + 日志点不记 key |
| 两个入口可运行（`--help` / `--validate-config`） | ✅ `python -m minicode.main --validate-config` 退出码 0 |
| 全部测试通过 | ✅ `21 passed` |

## 小结

Day 3 确立了「配置是 runtime 的入口参数、日志是可观测性骨架、入口是外壳」三个定位，以及
两个原则：**配置收敛成一份不可变的有效配置**、**日志和展示必须脱敏**。这两个原则贯穿后面
所有 Day——Day 10 接真实 Provider 读凭据、Day 11 权限记日志、Day 15 会话落盘，都依赖「配置
可诊断、日志不泄密」这个地基。理解了这三个「支撑层」的定位，后面看 Day 10 的 adapter、
Day 21 的 factory 时，就能明白「它们在往这个骨架上装什么」。
