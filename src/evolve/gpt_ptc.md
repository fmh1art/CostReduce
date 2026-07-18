# Programmatic Tool Calling 简介

> 更新日期：2026-07-14

## 1. 什么是 PTC

Programmatic Tool Calling（PTC，程序化工具调用）是 OpenAI Responses API 中的一种工具编排方式。模型可以生成一段轻量 JavaScript，用它来调用其他工具、处理工具返回值，并输出精简后的结果。

普通工具调用通常是：

```text
模型 → 调用工具 → 读取结果 → 再次推理 → 调用下一个工具
```

PTC 则可以是：

```text
模型生成 JavaScript
        ↓
程序并行或串行调用多个工具
        ↓
程序过滤、去重、排序、聚合工具结果
        ↓
只将精简结果返回给模型
```

它适合需要多个相关工具调用、控制流程比较确定，或者工具会返回大量中间数据的任务。

## 2. PTC 如何运行

PTC 可分为三个阶段：

1. **生成程序**：LLM 根据任务、工具定义和输出结构生成 JavaScript。
2. **执行程序**：OpenAI 在隔离的 V8 运行时中执行这段 JavaScript。程序可以使用并行调用、循环、条件判断和普通数据处理操作。
3. **生成最终回答**：程序通过 `text(...)` 或 `image(...)` 输出结果，结果再交给 LLM，用于继续推理或生成最终回答。

运行时支持 JavaScript 和顶层 `await`，但不提供 Node.js、npm、通用文件系统、子进程、任意网络访问或跨程序持久状态。程序只能访问请求中明确授权的工具。

## 3. 程序执行过程中是否涉及 LLM

**程序的生成涉及 LLM，但程序的普通执行过程不自动调用 LLM。**

也就是说：

- JavaScript 是 LLM 根据任务生成的；
- JavaScript 在 V8 中运行时，循环、条件判断、排序和聚合都是确定性代码操作；
- 每次 `filter`、`map`、`sort` 或 `reduce` 不会偷偷触发一次模型推理；
- 程序完成后，精简结果才会重新进入 LLM 上下文；
- 如果程序调用的某个工具本身由 LLM 或 embedding 模型实现，那么该工具内部仍然会产生相应模型调用和费用。

因此，PTC 并不是“让一个小模型在后台处理工具结果”，而是“让普通 JavaScript 处理适合代码化的中间步骤”。

## 4. 不使用 LLM，程序如何筛选和聚合结果

程序依靠工具返回的结构化字段和标准 JavaScript 操作进行处理，例如：

- `Promise.all`：并行调用多个工具；
- `filter`：按状态、分数、时间、类型等条件筛选；
- `map`：提取模型最终需要的字段；
- `Map` / `Set`：去重；
- `sort`：按分数、时间或优先级排序；
- `reduce`：计数、求和、分组或统计；
- `if` / `for`：实现条件分支和有界循环。

示例：并行搜索多个关键词，过滤低分结果，按文件与行号去重，只返回前 20 条：

```js
const queries = ["CacheManager", "invalidateCache", "cache_ttl"];

const batches = await Promise.all(
  queries.map(query => tools.search_code({ query, max_results: 100 }))
);

const unique = new Map();
for (const item of batches.flat()) {
  if ((item.relevance ?? 0) < 0.6) continue;

  const key = `${item.path}:${item.line}`;
  const previous = unique.get(key);
  if (!previous || item.relevance > previous.relevance) {
    unique.set(key, item);
  }
}

const matches = [...unique.values()]
  .sort((a, b) => b.relevance - a.relevance)
  .slice(0, 20)
  .map(({ path, line, snippet, relevance }) => ({
    path,
    line,
    snippet,
    relevance
  }));

text(JSON.stringify({ matches }));
```

这里的 `relevance` 必须由搜索工具返回，或者由代码通过明确规则计算。V8 运行时本身并不知道一段代码是否“语义相关”。

如果筛选条件需要真正的语义理解，例如“这段代码是否可能造成权限绕过”，通常有三种做法：

1. 先用 PTC 做机械过滤，再把较小的候选集交给 LLM 判断；
2. 直接使用普通工具调用，让 LLM 逐步阅读和判断结果；
3. 提供一个 `semantic_search`、`classify` 或 `rerank` 工具，由 embedding、reranker 或另一个 LLM 完成语义判断。此时语义能力来自该工具，而不是 JavaScript 运行时。

实践中最常见的是第一种：**PTC 负责缩小数据，LLM 负责理解数据。**

## 5. 基本配置

需要在 Responses API 请求中加入 `programmatic_tool_calling`，并为允许从程序调用的工具设置 `allowed_callers`：

```json
[
  {
    "type": "function",
    "name": "search_code",
    "parameters": {
      "type": "object",
      "properties": {
        "query": { "type": "string" }
      },
      "required": ["query"],
      "additionalProperties": false
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "matches": { "type": "array" }
      },
      "required": ["matches"],
      "additionalProperties": false
    },
    "allowed_callers": ["direct", "programmatic"]
  },
  {
    "type": "programmatic_tool_calling"
  }
]
```

`allowed_callers` 的含义：

| 配置 | 行为 |
|---|---|
| 省略或 `["direct"]` | 只能由模型直接调用 |
| `["programmatic"]` | 只能由生成的程序调用 |
| `["direct", "programmatic"]` | 两种方式都可以 |

PTC 支持的工具类型包括 function/custom、MCP、apply patch、local/hosted shell 和 code interpreter。具体支持情况应以所用模型页面为准。

## 6. 自有工具的执行与续跑

对于应用自己实现的函数，模型生成的 JavaScript 由 OpenAI 执行，但函数本身仍由应用执行：

1. 程序运行到自有函数时暂停；
2. Responses API 返回 `function_call`；
3. 应用校验参数和权限，然后执行函数；
4. 应用返回 `function_call_output`，并原样保留 `call_id` 和 `caller`；
5. 服务恢复对应程序；
6. 收到最终 `message` 后结束。

因此，自有工具可能仍产生多次 HTTP continuation，但程序内部的确定性步骤不需要 LLM 每一步都重新判断。

## 7. 适用与不适用场景

适合 PTC：

- 批量调用同一种工具；
- 并行搜索、读取或验证；
- 对结构化结果做 join、过滤、去重和聚合；
- 处理大量日志或查询结果，只保留关键信息；
- 调用依赖关系明确，后续参数可以机械推导。

不适合 PTC：

- 只有一次简单工具调用；
- 每一步都需要新的语义判断；
- 搜索方向需要根据当前内容灵活调整；
- 涉及付款、删除、部署等高影响操作；
- 最终结果必须保留完整引用或原生工具输出。

对于 SWE 任务，PTC 很适合并行代码搜索、批量读取、测试、lint、类型检查和日志聚合；根因判断、方案选择、代码修改和最终验收通常仍适合由 LLM 直接控制。

## 8. 成本影响

PTC 可能降低成本，主要因为：

- 大量中间工具输出不必全部进入模型上下文；
- 多个调用可以并行；
- 确定性处理由 JavaScript 完成；
- 模型重新读取上下文和重新决策的次数减少。

PTC 不会改变模型 token 单价，也不会自动免除工具调用费。如果程序调用了相同次数的付费工具，工具费用通常仍然存在。单次调用、输出很小或需要大量语义判断的任务，使用 PTC 可能没有收益，甚至会增加生成程序和失败恢复的开销。

评估时应同时比较正确率、输入/输出 token、模型轮次、工具调用次数、端到端延迟以及每个成功任务的总成本。

## 9. 安全注意事项

- 工具返回值应尽量结构化，并通过 `output_schema` 明确字段；
- 应用必须对每次工具调用重新校验参数和权限；
- 涉及副作用的函数应设计为幂等，并保留审批机制；
- 循环和重试必须设置上限；
- 压缩结果时应保留文件位置、日志引用或其他可回读证据；
- 不应让 PTC 的摘要替代必要的最终验证。

## 10. 官方资料

- [Programmatic Tool Calling](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling)
- [Function Calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Conversation State](https://developers.openai.com/api/docs/guides/conversation-state)
- [OpenAI API Models](https://developers.openai.com/api/docs/models)
