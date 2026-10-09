# Day 24：MCP——把外部能力翻译成内部 Tool，而不是开一条旁路

## 今日任务

实现懒启动的 stdio MCP 客户端，把外部的 Tool / Resource / Prompt 翻译成内部
`ToolDefinition`，复用参数校验、权限、截断、日志和 Session 记录。

## 背景：MCP 解决什么问题

minicode 是一个本地 Coding Agent，它需要调用外部能力——查数据库、订票、调用公司内部
API。如果为每个外部能力都写死一个工具，扩展一个新能力就要改一次内核。MCP（Model
Context Protocol）给出一套**标准协议**：任何实现 MCP 的服务端，minicode 都能自动发现
它的工具、资源和提示词并调用。

关键约束（课件反复强调）：MCP Tool Wrapper 仍然是 `ToolDefinition`，Loop 不直接调用
MCP Client。MCP 是「扩展外部能力」的通道，不是「绕过权限/截断/日志」的旁路。

## MCP 协议基础：JSON-RPC 2.0

MCP 的消息层是 JSON-RPC 2.0，有三种消息：

```jsonc
// 1. 请求 request：有 id，期待一个带同 id 的响应
{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

// 2. 响应 response：id 与请求一一对应
{"jsonrpc": "2.0", "id": 1, "result": {"tools": [...]}}
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "unknown method"}}

// 3. 通知 notification：无 id，不期待响应
{"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
```

**为什么 `id` 是核心**：stdio 是**全双工管道**，客户端可能连续发出多个请求（虽然我们
现在是串行请求），响应回来的顺序不保证和请求一致。`id` 是唯一能把「响应」路由回「对应
等待者」的钥匙。所以客户端的核心数据结构是 `request_id → Queue` 的映射。

## stdio 传输的两种帧格式

MCP 通过 stdio 传 JSON-RPC 消息，但「字节流如何切成一条条消息」有两种约定（用户拍板
「做双协议 auto-detect」）：

### newline-json（换行分隔）

每条消息占一行，末尾 `\n`：

```
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n
```

- 客户端按行读，每行 `json.loads`。
- **限制**：JSON 文本里不能有裸换行——多行文本必须转义成 `\n` 字符，否则一条消息会被
  拆成多行、解析失败。

### content-length（长度帧，MCP 官方现行规范）

先发一个长度头，空行，再发指定字节数的 body：

```
Content-Length: 48\r\n
\r\n
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
```

- 客户端先读头得到 `N`，再精确读 `N` 字节的 body。
- **好处**：body 可以含真换行（多行文本不用转义），这也是官方规范从 newline-json 迁移
  到 content-length 的原因。

### auto-detect 的实现

reader 线程读第一行，看是否以 `Content-Length:` 开头：是则按长度帧读，否则按行读。
我们的 `StdioMcpClient._consume_stdout` 里：

```python
if self.protocol is None:
    self.protocol = ("content-length"
                     if stripped.lower().startswith("content-length:")
                     else "newline-json")
```

## initialize 握手（完整状态机）

MCP 服务端启动后，客户端必须先做一次握手，之后才能发现/调用工具：

```
1. 客户端 spawn 服务端进程（stdin/stdout/stderr 管道）
2. 客户端发 initialize 请求（带 protocolVersion / capabilities / clientInfo）
3. 服务端回 initialize result（带 protocolVersion / capabilities / serverInfo）
4. 客户端发 notifications/initialized 通知（无响应，宣告握手完成）
5. 之后才能发 tools/list、tools/call 等
```

我们 `StdioMcpClient.start()` 的对应实现：

```python
def start(self):
    if self._started:
        return
    for protocol in self._protocol_candidates():
        try:
            self._spawn_process()
            self.protocol = protocol
            self.request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "minicode", "version": "0.1.0"},
            }, timeout_seconds=3.0)
            self.notify("notifications/initialized", {})
            self._started = True
            return
        except Exception as error:
            self.close()   # 失败则关掉重试下一个协议
    raise RuntimeError(...)
```

**协议协商**：`_protocol_candidates()` 返回 config 指定的协议（若有），否则
`["content-length", "newline-json"]`。每个协议试一次——spawn + initialize，失败就 close
换下一个。这保证「服务端用哪个协议都能连上」。

## 客户端完整生命周期

### 1. 懒启动（为什么进程不预先 spawn）

配置发现后**不立即启动所有进程**。`StdioMcpClient.__init__` 只存配置，`start()` 在第一次
`list_tools`/`call_tool` 时才被 `_ensure_started()` 触发。原因：

- 启动快：不需要等所有 MCP 服务端 spawn + initialize；
- 省资源：配置了但没用的服务端永远不启动；
- 韧性：一个服务端失败不阻塞其他服务端（装配时 try，失败降级）。

```python
def _ensure_started(self):
    if self._started and not self._is_process_alive():
        self.close()          # 进程死了就重置，下次 start 重连
    if not self._started:
        self.start()
```

### 2. spawn（子进程 + 三管道）

`_spawn_process()` 用 `subprocess.Popen` 启动服务端，stdin/stdout/stderr 都接管道。
Windows 上两个细节：

- `creationflags=CREATE_NO_WINDOW`：避免子进程弹控制台窗口；
- `_prepare_spawn()`：Windows 上 `npx`/`npm` 等是 `.cmd` 批处理包装器，`shell=False` 时
  `CreateProcess` 既不能直接运行 `.cmd` 也不按 PATHEXT 搜索。所以用 `shutil.which` 解析
  真实路径，批处理包装器经 `cmd.exe`（`shell=True`）启动。

### 3. 请求（request ID → Queue 关联）

```python
def request(self, method, params, timeout_seconds=5.0):
    message_id = self.next_id
    self.next_id += 1
    response_queue = Queue(maxsize=1)
    with self._lock:
        self._pending[message_id] = response_queue
    self.send({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
    try:
        message = response_queue.get(timeout=timeout_seconds)
    except Empty:
        # 超时：清掉 pending，报错（不永久挂起）
        ...
    if message.get("error"):
        raise RuntimeError(...)
    return message.get("result")
```

每个请求分配一个自增 id，`_pending[id] = Queue`。发出去后 `Queue.get(timeout)` 阻塞等
响应。三种失败都走「明确抛错」而非「挂死」：超时（Empty）、协议错误（error）、进程退出
（reader 线程投递错误）。

### 4. 响应（reader 线程路由）

```python
def _handle_message(self, message):
    message_id = message.get("id")
    if not isinstance(message_id, int):
        return                     # notification 无 id，忽略
    with self._lock:
        queue = self._pending.pop(message_id, None)
        if queue is not None:
            queue.put(message)     # 按 id 唤醒对应的等待者
```

### 5. 断线（进程退出释放等待者）

这是最容易被忽略、后果最严重的一环。reader 线程读到 EOF（服务端进程退出）时，如果
不清理 `_pending`，所有 `Queue.get()` 会**永久阻塞**，整个 agent 挂死。所以 reader 线程
的 `finally` 里：

```python
finally:
    if self.process is not None:
        error_msg = {"error": {"code": -1, "message": f"MCP server process exited"}}
        with self._lock:
            for q in list(self._pending.values()):
                q.put(error_msg)   # 每个等待者都收到「进程退出」错误
            self._pending.clear()
```

### 6. 关闭（dispose）

`close()` 先给所有 pending 投递「closed」错误，再 `terminate()` + 超时 `kill()` 进程，
最后复位状态（protocol/线程/cache 全清）。`create_mcp_backed_tools` 返回的 `dispose`
闭包遍历所有 client 调 `close()`，挂到 `ToolRegistry.add_disposer`，保证 `Registry.dispose`
时清理所有 MCP 进程。

## 线程模型：为什么 stderr 要独立消费

两个后台线程：

| 线程 | 职责 |
| --- | --- |
| `_consume_stdout` | 读 stdout，解析帧，按 id 路由响应 |
| `_consume_stderr` | 读 stderr，存最近 8 行（诊断用） |

**为什么 stderr 不能忽略**：OS 管道的缓冲区是有限的（通常 64KB）。如果服务端往 stderr
写日志超过缓冲且没人读，服务端的 `write(stderr)` 会**阻塞**，进而卡住整个服务端（包括
stdout 的响应）。所以 stderr 必须独立线程持续消费，哪怕只是把内容丢弃/暂存。

## 工具包装：`mcp__server__tool`

`create_mcp_backed_tools` 遍历 enabled 的 server，对每个：

1. `list_tools()` 拿到 descriptor 列表（每个含 name/description/inputSchema）；
2. 包装成 `ToolDefinition`，名字 `mcp__{server}__{tool}`（前缀隔离——两个 server 都有
   `echo` 工具时不会撞名）；
3. `inputSchema` 透传（服务端自己校验参数，客户端不重复造 schema）；
4. `validator` 只做 dict 校验（透传，因为真正的校验在服务端）；
5. `run` 闭包捕获 client + 原工具名，调 `client.call_tool(原名, input)`。

```python
def _run(input_data, _context, *, _client=client, _name=descriptor_name):
    return _client.call_tool(_name, input_data)

ToolDefinition(
    validator=_validator,
    run=_run,
    metadata=ToolMetadata(
        name=wrapped_name,          # mcp__server__tool
        description=...,
        input_schema=input_schema,
        # 不声明 READ_ONLY：MCP 工具可能有副作用，走默认权限判断
    ),
)
```

**踩坑**：我们的 `ToolDefinition` 是 `(validator, run, metadata)` 结构（D8 把描述归
metadata），不是参考项目的 `(name=, description=, input_schema=, ...)`。照参考项目写会
直接 `TypeError`。

## Resource 与 Prompt 网关工具

Tool 是「动作」，Resource 是「只读数据」，Prompt 是「内容模板」。后两者不直接包装成
mcp__ 工具，而是通过网关工具暴露：

| 网关工具 | 作用 |
| --- | --- |
| `list_mcp_resources` | 列出所有 server 的资源 URI |
| `read_mcp_resource` | 按 server + uri 读资源内容 |
| `list_mcp_prompts` | 列出所有 server 的 prompt |
| `get_mcp_prompt` | 按 server + name（+参数）取渲染后的 prompt |

这些网关工具声明 `READ_ONLY`。**MCP Prompt 绝不拼进系统指令**——它是带来源的外部内容，
可能含冲突或注入文本，只能作为普通工具结果回填。

## 安全模型（裁剪版）

两层（用户拍板「裁剪版」，不做参考项目的硬编码命令白名单）：

1. **项目级默认不信任**：`load_mcp_servers` 读 `<cwd>/.mcp.json`，每个 server 的
   `enabled` 默认 False，只有显式 `"enabled": true` 才启动。`.mcp.json` 在项目仓库里，
   可能来自 clone 别人的仓库（不可信），默认 disabled 意味着「显式启用」才承担启动外部
   进程的风险。
2. **参数 shell 元字符检查**：`_validate_mcp_args` 过滤 `|&;`$(){}<>` 等元字符。因为
   Windows 上 `.cmd` 批处理包装器要用 `shell=True` 启动，参数若含元字符会被 shell 解释，
   构成注入。参数已经过校验，`shell=True` 才安全。

## 装配与顺序（`runtime_factory.create`）

```python
tools = create_full_tool_registry()

# MCP 工具必须在 build_adapter 之前注册
mcp_result = create_mcp_backed_tools(cwd=cwd, mcp_servers=load_mcp_servers(cwd))
for tool in mcp_result["tools"]:
    tools.register(tool)
tools.add_disposer(mcp_result["dispose"])

adapter = build_adapter(config, tools=tools.list(), ...)  # 这里才冻结工具清单
```

**为什么顺序不能反**：真实模型的 `tools.list()` 在构造 adapter 时冻结成 API 工具清单。
如果 MCP 工具在 `build_adapter` 之后才 register，模型根本不知道有这些工具，MCP 就成了
摆设。所以必须「先 register MCP 工具，再 build_adapter」。

## 关键设计决策

1. **为什么 MCP Tool Wrapper 仍是 ToolDefinition？** 外部能力若开旁路（Loop 直接调 MCP
   Client），就绕过权限、截断、日志、Session——这些是「所有工具」的公共契约。翻译成
   ToolDefinition 后，MCP 工具和本地工具无差别，自动获得这些保障。
2. **为什么 request ID → Queue 而非 Future？** 运行时零依赖，标准库没有现成 Future。
   `queue.Queue` + `get(timeout)` 足以实现「按 id 路由 + 超时 + 退出释放」，不引依赖。
3. **为什么进程退出必须释放等待者？** 否则 pending 的 `Queue.get()` 永久阻塞，agent 挂死。
   这是「断线」场景的保命逻辑，reader 线程 finally 里必须清空 pending。
4. **为什么懒启动？** 不预启动没用的进程：启动快、省资源、失败不阻塞。装配时 list_tools
   触发首次 spawn，失败降级 error 状态（工具不注册），不抛异常拖垮其他 server。
5. **为什么项目级默认不信任？** `.mcp.json` 可能来自不可信仓库。默认 disabled 把「启动
   外部进程」变成显式选择，而不是 clone 仓库就自动执行陌生命令。

## 易错点

- **MCP Prompt 不拼进系统指令**：外部内容可能含注入文本，只能经网关工具按需获取。
- **stderr 不消费会卡死服务端**：管道缓冲满了，服务端写 stderr 阻塞，连带 stdout 也卡。
- **ToolDefinition 结构不同**：我们是 `(validator, run, metadata)`，别照参考项目
  `(name=, ...)` 写。
- **Windows `.cmd` 启动**：`shell=False` 跑不了 `npx.cmd`，要用 `shutil.which` + `shell=True`，
  且参数必须先过元字符校验。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 假服务完整端到端，无网络依赖 | ✅ fake_mcp_server.py + test_mcp.py |
| 工具名称冲突被前缀隔离 | ✅ `mcp__server__tool`（有测试） |
| 服务超时/退出释放等待者，不永久挂起 | ✅ timeout + exit-after 测试 |
| 超大/畸形消息被拒绝且有诊断 | ✅ 消息大小上限 + 坏 JSON 跳过 |
| 关闭 Registry 后无遗留 MCP 进程 | ✅ add_disposer + close（有测试） |
| 项目级 .mcp.json 默认不信任 | ✅ load_mcp_servers 默认 enabled=False（有测试） |
| 既有测试无回归 | ✅ 157 passed（原 145 + 新增 12） |

## 插曲：fastMCP 交叉验证（未采用）

用户起初用 fastMCP 起了两个 HTTP 服务（`host`+`port`）和一个 stdio server 想交叉验证。
但：① HTTP 服务是 HTTP/SSE 传输，与本日 stdio 客户端不匹配；② stdio server 用 mcp 1.x
旧写法 `from mcp.server import FastMCP`，本机是 mcp 2.x（`FastMCP` 改名 `MCPServer`），
`ImportError` 跑不起来。最终用户决定放下 fastMCP 验证，回到课件要求的自写 fake server
（零依赖、可复现）。这个插曲也提示：若要连真实网络 MCP 服务，需另实现 HTTP/SSE 传输，
超出本日 stdio 范围。
