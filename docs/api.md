# 接口说明

启动后访问 `/docs` 可试用普通接口。流式任务使用 POST，前端通过 fetch 读取 SSE。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 查看实际运行模式与配置的组件 |
| GET | `/api/dashboard` | 商品目录和订单列表 |
| POST | `/api/chat/stream` | 创建一轮协作，返回 SSE |
| GET | `/api/proposals/{session_id}` | 当前会话的最近30个确认单 |
| POST | `/api/proposals/{id}/approve` | 确认一张演示单据 |
| GET | `/agents/{role}/.well-known/agent-card.json` | supplier / logistics 的能力卡 |
| POST | `/agents/{role}/` | A2A 0.3 JSON-RPC，支持官方 SDK 的任务处理 |

## 发起任务

```json
{"session_id":"demo-session-001","message":"查询 SKU-1001 的库存，并补货50件"}
```

`message` 为1～2000字符且不能为空白。`session_id` 为8～80位字母、数字、下划线或短横线。默认演示支持美国 US、德国 DE、英国 GB、日本 JP；没有指定市场时先复用会话记忆，否则使用 US。订单场景以订单中的目的地为准。

```text
event: start
data: {"request_id":"..."}

event: step
data: {"node":"orchestrator","data":{"pending":["product"],"entities":{...}}}

event: step
data: {"node":"product","data":{"results":{...}}}

event: result
data: {"answer":"...","facts":{...},"proposals":[...],"request_id":"..."}

event: done
data: {"request_id":"..."}
```

执行失败返回 `error` 事件，不会把内部堆栈或服务密钥返回给浏览器。HTTP 200 仅表示流已经建立，调用方仍须检查是否收到 `result`。总时限120秒，A2A 单次 HTTP 请求15秒，模型单次请求30秒；流关闭后清除当前会话的运行标记。同一进程同一会话并发任务返回409。

## 确认单

提交到 `/api/proposals/{id}/approve`：

```json
{"session_id":"demo-session-001"}
```

补货确认检查供应商剩余库存，把数量从供应商演示库存转入本地。配送确认要求订单为 `paid` 且本地库存足够，扣除本地库存，生成模拟运单。单据检查、数据变化和结果记录在同一 SQLite `BEGIN IMMEDIATE` 事务中完成。重复确认返回原结果。

错误：404 确认单不存在或不属于当前会话；409 当前会话正在运行、供应商库存不足、商品库存不足或订单状态不允许。确认只操作本地合成数据，不调用真实平台写入。

## A2A 与 MCP

供应商 A2A 接受 JSON 文本 `{"sku":"SKU-1001","operation":"inventory"}`，补货草案可附加 `"operation":"restock","quantity":50`。物流接受 `{"sku":"SKU-1002","country":"DE","quantity":1}`。请求放在 A2A `message/send` 的 TextPart 中，结果放在 Task Artifact 的 DataPart 中。

外部 URL 仅从配置读取，不从用户消息读取；客户端验证 Agent Card 的技能 ID，并向已配置端点发送请求，不跳转到 Card 中任意 URL。未配置身份认证，仅供本机演示。
