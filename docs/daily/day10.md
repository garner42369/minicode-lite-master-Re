# Day 10：真实 Provider——只翻译协议，不接管运行时

## 今日任务

把统一的 `ModelAdapter` 连接到一个真实支持 Tool Calling 的服务（DeepSeek V4），并保留 Mock
路径。内部历史始终保持 `ChatMessage`，Adapter 只在边界处把外部 JSON 翻译进来、翻译出去。

## 任务分析

此前 9 天建立的 Loop、工具、Prompt 都只和 `ModelAdapter` 协议打交道，从未真正发过网络请求。
Day 10 要把「模型提议」这一步接到真实服务，需要回答五个问题：

| 问题 | 答案 |
| --- | --- |
| 协议怎么互转 | 内部 ChatMessage 历史 ↔ Provider 的 messages/tools 格式 |
| 错误怎么处理 | 网络抖动/限流可重试，认证失败/参数错立即失败 |
| 成本怎么记 | 每次调用的 token/费用如实累加 |
| 模型怎么选 | 元数据 + Provider 检测 + Adapter 工厂 |
| 主模型挂了怎么办 | 有界 fallback，限制次数、不重放副作用 |

## 代码实现

### 1. 协议互转：to_openai_messages + parse_openai_response

```python
def to_openai_messages(messages):
    out = []
    i = 0
    while i < len(messages):
        role = messages[i]["role"]
        if role == "assistant_tool_call":
            # 内部每个工具调用是「一条 assistant_tool_call」，OpenAI 要求
            # 「一条 assistant 消息携带整个 tool_calls 数组」→ 合并连续的调用
            tool_calls = []
            reasoning = ""
            while i < len(messages) and messages[i]["role"] == "assistant_tool_call":
                call = messages[i]
                tool_calls.append({
                    "id": call["tool_call_id"],
                    "type": "function",
                    "function": {"name": call["tool_name"],
                                 "arguments": json.dumps(call["input"], ensure_ascii=False)},
                })
                if not reasoning and call.get("reasoning"):
                    reasoning = call["reasoning"]          # 同一步的思考链取第一条
                i += 1
            assistant = {"role": "assistant", "content": None, "tool_calls": tool_calls}
            if reasoning:
                assistant["reasoning_content"] = reasoning  # DeepSeek：不带会 400
            out.append(assistant)
        elif role == "tool_result":
            out.append({"role": "tool", "tool_call_id": messages[i]["tool_call_id"],
                        "content": messages[i].get("content", "")})
        else:  # system / user / assistant / assistant_progress
            out.append({"role": "assistant" if role == "assistant_progress" else role,
                        "content": messages[i].get("content", "")})
        i += 1
    return out


def parse_openai_response(payload):
    message = payload["choices"][0].get("message") or {}
    reasoning = message.get("reasoning_content") or ""
    raw_calls = message.get("tool_calls") or []
    if raw_calls:                                   # 有工具调用 → tool_calls 步
        calls = []
        for item in raw_calls:
            fn = item["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=item["id"], name=fn["name"], arguments=args))
        return AgentStep(type="tool_calls", calls=calls, reasoning=reasoning)
    content = message.get("content") or ""          # 无工具调用 → 文本步
    kind, text = _classify_text(content)            # 按 <final>/<progress> 判定 kind
    return AgentStep(type="assistant", kind=kind, content=text, reasoning=reasoning)
```

**关键点**：

- `to_openai_messages` 里最别扭的一处是「合并连续的 assistant_tool_call」——内部历史把每个
  工具调用拆成独立消息，OpenAI 要求一条 assistant 带整个 tool_calls 数组，所以要把连续的
  调用合并；
- `parse_openai_response` 里 `arguments` 是 JSON 字符串，要 `json.loads` 反序列化成 dict；
  文本步的 `kind` 按 `<final>/<progress>` 前缀判定。

### 2. 工厂：mock / 真实 两条路径汇合

```python
def build_adapter(config, *, tools=None, cost_tracker=None):
    if config.model == "mock":                 # 离线路径：Day 4 的 Mock
        from minicode.mock_model import MockModelAdapter
        return MockModelAdapter()
    if not config.base_url:
        raise ValueError("base_url is required for a real model")
    provider = detect_provider(config.model, config.base_url)   # openai / anthropic
    info = MODEL_CATALOG.get(config.model)                     # 价格、窗口
    return OpenAIAdapter(model=config.model, base_url=config.base_url, api_key=config.api_key,
                         tools=tools, cost_tracker=cost_tracker,
                         input_price_per_mtok=info.input_price_per_mtok if info else 0.0, ...)
```

**关键点**：工厂是「mock / 真实」两条路径的切换点——`model == "mock"` 走 Day 4 的离线 Mock，
真实模型走 `OpenAIAdapter`。上层（Loop）完全不感知这个切换。

## 讲解

### 每个模块为什么存在

| 模块 | 职责 | 谁消费 |
| --- | --- | --- |
| `openai_adapter.py` | OpenAI 兼容格式互转 + 适配器 | Loop（通过 ModelAdapter 协议） |
| `api_retry.py` | 错误分类、退避、带重试的 urllib 传输 | Adapter |
| `cost_tracker.py` | token/费用事实记录 | Adapter、Day 27 控制论 |
| `model_registry.py` | 模型元数据、检测、Adapter 工厂 | Day 21 运行时装配 |
| `model_switcher.py` | 有界 fallback | 上层装配 |

### 数据流

```
Loop 调 model.next(history)   ← 只认 ModelAdapter 协议
        │
        ▼
OpenAIAdapter.next()
        ├── to_openai_messages(history)   ← 内部 ChatMessage → OpenAI 格式
        ├── request_with_retry(url, ...)  ← urllib 发送，429/5xx 退避重试
        ├── parse_openai_response(...)    ← OpenAI 响应 → AgentStep（含 reasoning）
        └── cost_tracker.record(...)      ← 记 token/费用
        │
        ▼
AgentStep → 回给 Loop
```

### 关键设计决策

1. **为什么「Provider 原始 JSON 只存在于 Adapter」（本次红线）？** 内部历史保持 `ChatMessage`，
   Adapter 在请求前转成 Provider 格式、响应后转回 `AgentStep`，Loop 和工具协议不感知 OpenAI/
   Anthropic 格式。换 Provider 只改 Adapter，不动 Loop/工具/UI——若 Provider 字段渗进核心协议，
   换一个 Provider 就要满处改。协议隔离是「运行时能换 Provider」的前提。

2. **为什么思考链（reasoning_content）必须回传？** DeepSeek V4 默认开启 thinking 模式，响应
   里有 `reasoning_content`，且**带 tool_calls 的 assistant 消息回传时必须带上它，否则返回
   400**。这是真实测试才暴露的硬约束。处理方式是思考链只落进 `AgentStep.reasoning` 这个诊断
   字段，不改变核心语义。

3. **为什么重试要「分类」？** 网络错误、429、5xx 可重试（抖动、限流、暂不可用）；401/400/422
   不可重试（认证失败、参数错，重试只会浪费甚至锁号）。退避要「指数 + jitter + 尊重
   Retry-After」——jitter 打破惊群，Retry-After 尊重服务端限流意图。

4. **为什么 fallback 只在「可用性错误」上切换？** 认证失败是配置错误、换模型无意义；而工具
   一旦执行过，历史里已有 `tool_result`，此时换模型重放会产生「同一动作执行两次」的副作用。
   fallback 的正确时机是「某次 next() 失败后、尚未产生副作用」。

## Adapter 在 Agent Runtime 里的位置

Adapter 是「**模型**」这一环的**真实实现**，补上 Day 4 留下的「Mock 的另一半」：

- **对 Loop 而言**：Day 4 的 `MockModelAdapter` 和 Day 10 的 `OpenAIAdapter` 实现的是**同一个
  `ModelAdapter` 协议**，Loop 完全不感知换没换；
- **切换点集中在 `build_adapter` 工厂**：`model == "mock"` 走离线 Mock，`model == 真实模型`
  走真实 Adapter——开发用 Mock（快、稳、不花钱），上线切真实（一行配置的事）；
- **成本记录给 Day 27 用**：`cost_tracker` 记的 token/费用，是 Day 27 控制论「按成本做决策」
  的数据来源；
- **协议隔离给未来换 Provider 用**：因为只有 Adapter 感知 Provider 格式，将来加 Anthropic、
  换别的服务，都只动 Adapter。

## 真实模型的实测发现

- 配置 `model=deepseek-v4-pro`、`base_url=https://api.deepseek.com` 请求成功；
- thinking 模式默认开启：响应含 `reasoning_content`，usage 里有 `reasoning_tokens`；
- 端到端闭环跑通：`list_files` → `read_file`（分页 3 次）→ 最终回答，`stop_reason=done`。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 默认测试不访问网络 | ✅ 真实 demo 为手动脚本，不进 pytest 收集 |
| 无凭据时明确 SKIP/未配置 | ✅ `build_adapter` 无 base_url 抛清晰错误 |
| Tool Call ID 往返不变 | ✅ 真实闭环 `read_file` 连续 3 次调用成功回填 |
| 日志无密钥 | ✅ 配置打印用 `***{末四位}` 脱敏 |
| 真实模型用只读工具完成解释 | ✅ `list_files` + `read_file` 解释项目 |
| 既有测试无回归 | ✅ `72 passed` |

## 小结

Day 10 是整个项目从「离线脚手架」跨向「真实世界」的分水岭。前 9 天建立的 Loop、工具、Prompt
都只和 `ModelAdapter` 协议打交道，今天第一次接到真实的 DeepSeek 上，且守住了「协议隔离」这条
红线——Provider 的原始 JSON 只存在于 Adapter 里。这一天也确立了几个后面反复依赖的能力：成本
记录（Day 27 控制论要用）、错误重试（所有 Provider 调用都要）、模型工厂（Day 21 运行时装配
要用）。而真实测试暴露的 reasoning_content 回传 400，是最能说明「真实测试价值」的例子——
fixture 永远模拟不出这个坑，只有真调一次才能发现。
