"""minicode 的上下文压缩预算：工具结果预算与读取去重（Day 17）。

长任务里，工具可能返回超大输出（日志、diff），整段塞进模型上下文会撑爆预算。
本模块做「确定性」的预算管理（不调模型）：

- ToolResultBudgetManager：大结果持久化 artifact，模型只收 head/tail 摘要；
- ReadDedupManager：相同 path+hash 的重复读取返回 stub（第 3 步加）。

课件易错点：不同工具用不同截断策略；通用策略至少保留 head、tail、错误行和规模信息。
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class ToolResultBudgetManager:
    """工具结果预算：大结果持久化 artifact，模型只收可追踪摘要。

    为什么保留完整 artifact：模型收到的摘要不是「丢失」完整内容，而是把完整
    内容落到本地文件，模型需要时可再读、用户也可查看——摘要明确标注了
    artifact 路径和原始大小，不会让人误以为「只有这点内容」。
    """

    def __init__(self, artifact_dir: Path, limit: int = 2_000) -> None:
        self.artifact_dir = artifact_dir
        self.limit = limit

    def process(self, output: str) -> tuple[str, dict]:
        """处理一段工具输出。返回 (预算后的文本, 元信息)。

        未超限：原样返回，元信息空字典。
        超限：完整内容写 artifact，返回 head/tail 摘要，元信息含 truncated /
        original_size / artifact 路径。
        """
        if len(output) <= self.limit:
            return output, {}

        content_hash = hashlib.sha256(output.encode()).hexdigest()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact = self.artifact_dir / f"{content_hash}.txt"
        artifact.write_text(output, encoding="utf-8")

        half = self.limit // 2
        summary = (
            output[:half]
            + f"\n...[truncated: {len(output)} chars, full output at {artifact}]...\n"
            + output[-half:]
        )
        meta = {
            "truncated": True,
            "original_size": len(output),
            "artifact": str(artifact),
        }
        return summary, meta


class ReadDedupManager:
    """读取去重：相同 path+hash 的重复读取返回 stub。

    文件变化（hash 不同）时自动失效——返回新内容，绝不因旧 dedup 拦截新内容。
    这保证「同一内容重复读不占上下文」，但「文件改后再读」能拿到最新内容。
    """

    def __init__(self) -> None:
        self._reads: dict[str, str] = {}  # path -> 上次的 content hash

    def process(self, path: str, content: str) -> tuple[str, bool]:
        """处理一次读取。返回 (文本, 是否去重 stub)。

        相同 path + 相同 content hash → 返回 stub（去重，bool=True）。
        否则：记录新 hash，返回原内容（文件变化自动失效，bool=False）。
        """
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        previous = self._reads.get(path)
        if previous == content_hash:
            stub = f"[unchanged duplicate read: {path}, hash={content_hash[:8]}]"
            return stub, True
        self._reads[path] = content_hash
        return content, False


def _safe_cut(messages: list[dict], cut: int) -> int:
    """把切分点调整到安全边界：不拆散 assistant_tool_call ↔ tool_result 对。

    一次工具调用 = assistant_tool_call(请求) + tool_result(结果) 两条相邻消息，
    且请求一定在结果前面。只有 cut 落在 tool_result 上才会拆对——它的配对
    assistant_tool_call 在 cut-1，会被压进 removed、结果却留在 recent，只剩一半。
    此时往前移一格，让整对都留在 recent。

    cut 落在 assistant_tool_call 上是安全的：它的 tool_result 在 cut+1，两者都进
    recent，不拆对。所以这里只退 tool_result，不退 assistant_tool_call——否则
    cut 会退过头，recent 比 keep_recent 多留一整对，压缩不充分。
    """
    while cut > 0 and messages[cut].get("role") == "tool_result":
        cut -= 1
    return cut


def _summarize_content(content: str, limit: int = 200) -> str:
    """截取长内容的 head/tail，中间标注规模，避免摘要被长输出撑爆。"""
    if len(content) <= limit:
        return content
    half = limit // 2
    return content[:half] + f"\n...[truncated: {len(content)} chars]...\n" + content[-half:]


def compact_messages(messages: list[dict], keep_recent: int) -> list[dict]:
    """把旧历史压缩成一条结构化摘要，保留最近 keep_recent 条。

    只压缩「对话历史」（非 system 消息）；system 消息原样保留在最前面。
    这是「记忆注入」和「压缩摘要」两条线分开的关键：记忆作为 system 消息
    注入（见 turn_kernel.upsert_stable_task_message），压缩时 system 消息不
    参与、不被摘要，记忆因此永不丢失（详见 docs/memory-context-mixing.md）。

    摘要从被压缩的消息里确定性提取（不调 LLM）：
    - topics：user 消息内容（前 100 字）；
    - tools：assistant_tool_call 的工具名；
    - files：assistant_tool_call 的 input.path（已读/已改文件）；
    - errors：tool_result 里 is_error 的错误内容（前 120 字）；
    - unlinked：无链接 tool_result 的一次性输出 head/tail（run_command/search）。
    source_range 标记被压缩的范围（相对 non-system）。

    关键保证：工具对不被拆散；原始 messages 不被修改（返回新列表）。
    不把被压缩的工具结果正文收进摘要——文件内容靠重读（文件系统是权威），
    只进「状态」（错误、路径）不进「数据」，避免把 Day 17 截断的大结果又收回来。
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= keep_recent:
        return list(messages)

    cut = _safe_cut(non_system, len(non_system) - keep_recent)
    removed, recent = non_system[:cut], non_system[cut:]

    # 从被压缩的 user 消息提取讨论主题（前 100 字）
    topics = [
        m.get("content", "")[:100].replace("\n", " ")
        for m in removed
        if m.get("role") == "user" and isinstance(m.get("content"), str) and m.get("content")
    ]
    # 从被压缩的 assistant_tool_call 提取工具名和涉及文件
    tools = sorted({
        str(m.get("tool_name"))
        for m in removed
        if m.get("role") == "assistant_tool_call" and m.get("tool_name")
    })
    files = sorted({
        str(m.get("input", {}).get("path"))
        for m in removed
        if m.get("role") == "assistant_tool_call" and m.get("input", {}).get("path")
    })
    # 从被压缩的 tool_result 提取失败错误（前 120 字）
    errors = [
        m.get("content", "")[:120]
        for m in removed
        if m.get("role") == "tool_result" and m.get("is_error")
    ]
    # 有链接的工具调用（input 有 path）→ 结果靠重读恢复，摘要只放路径（files 已提）
    linked_calls = {
        m.get("tool_call_id")
        for m in removed
        if m.get("role") == "assistant_tool_call" and (m.get("input") or {}).get("path")
    }
    # 无链接的 tool_result（run_command/search 一次性输出，非 error）→ head/tail 进摘要
    # WHY 不调 LLM 判断「重不重要」，只看「有没有链接」：有链接靠重读，无链接不
    # 留内容就永久丢失，所以无链接的一律留 head/tail。
    unlinked = [
        _summarize_content(m.get("content", ""))
        for m in removed
        if m.get("role") == "tool_result"
        and m.get("tool_call_id") not in linked_calls
        and m.get("content") and not m.get("is_error")
    ]

    summary_content = "COMPACTED (older context summarized)\n"
    if topics:
        summary_content += "Topics discussed:\n" + "\n".join(f"- {t}" for t in topics[:8]) + "\n"
    if tools:
        summary_content += "Tools used: " + ", ".join(tools) + "\n"
    if files:
        summary_content += "Files touched: " + ", ".join(files) + "\n"
    if errors:
        summary_content += "Errors encountered:\n" + "\n".join(f"- {e}" for e in errors[:3]) + "\n"
    if unlinked:
        summary_content += "Tool results (no linked file):\n" + "\n".join(f"- {r}" for r in unlinked[:5])

    summary = {
        "role": "system",
        "content": summary_content,
        "source_range": [0, max(0, cut - 1)],
    }
    return [*system_msgs, summary, *recent]


def reactive_truncate(messages: list[dict], keep_ratio: float = 0.4) -> list[dict]:
    """响应式激进截断：Provider 报 overflow 后，只保留 system 消息 + 最近一部分。

    比 compact_messages 更激进——不留结构化摘要，直接砍掉最旧的对话历史，只保留
    最近 keep_ratio 比例，中间插入一条标记说明「发生了激进截断」。用于 context
    overflow 后的兜底恢复，宁可丢历史也不让请求继续超限。
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    keep_count = max(3, int(len(non_system) * keep_ratio))
    if keep_count >= len(non_system):
        return list(messages)

    marker = {
        "role": "system",
        "content": (
            "[Context aggressively truncated for recovery]\n"
            "Earlier conversation was removed to fit context limits."
        ),
    }
    return [*system_msgs, marker, *non_system[-keep_count:]]
