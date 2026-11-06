"use strict";
const $ = (selector) => document.querySelector(selector);
const create = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
let sessionId = sessionStorage.getItem("border-session") || crypto.randomUUID();
sessionStorage.setItem("border-session", sessionId);
let data = { products: [], orders: [] }, dataTab = "products", busy = false;
const labels = { orchestrator: "任务规划完成", product: "商品助手完成查询", order: "订单助手完成处理", support: "客服助手找到参考资料", marketing: "营销助手完成草稿", summarize: "结果已汇总" };
const statuses = { paid: "待配送", in_transit: "运输中", delivered: "已签收" };

async function api(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(typeof detail.detail === "string" ? detail.detail : "请求失败，请稍后再试");
  }
  return response;
}
async function refresh() {
  try {
    data = await (await api("/api/dashboard")).json();
    $("#metric-products").textContent = data.products.length.toString().padStart(2, "0");
    $("#metric-orders").textContent = data.orders.filter(o => o.status === "paid").length.toString().padStart(2, "0");
    $("#metric-stock").textContent = data.products.filter(p => p.stock < 20).length.toString().padStart(2, "0");
    renderTable();
  } catch (error) { showStatus(error.message, true); }
}
function renderTable() {
  const products = dataTab === "products";
  $("#data-title").textContent = products ? "商品与库存" : "跨境订单";
  const headers = products ? ["商品", "售价", "库存", "市场"] : ["订单", "目的地", "金额", "状态"];
  const head = create("tr");
  headers.forEach(h => head.append(create("th", "", h)));
  $("#table-head").replaceChildren(head);
  $("#table-body").replaceChildren();
  const rows = products ? data.products : data.orders;
  rows.forEach(row => {
    const tr = create("tr"), first = create("td");
    if (products) {
      const cell = create("div", "product-cell"), name = create("div", "", row.name);
      name.append(create("small", "", row.sku));
      cell.append(create("span", "product-initial", row.name[0]), name); first.append(cell);
      tr.append(first, create("td", "", `$${row.price_usd.toFixed(2)}`));
      const stock = create("td"); stock.append(create("span", row.stock < 20 ? "stock-low" : "", `${row.stock} 件`));
      tr.append(stock, create("td", "", row.markets.join(" / ")));
    } else {
      first.textContent = row.order_id; first.append(create("small", "", row.sku));
      tr.append(first, create("td", "", row.country), create("td", "", `$${row.total_usd.toFixed(2)}`));
      const status = create("td"); status.append(create("span", "status-pill", statuses[row.status] || row.status)); tr.append(status);
    }
    $("#table-body").append(tr);
  });
  $("#data-count").textContent = `共 ${rows.length} 条演示${products ? "商品" : "订单"}`;
  document.querySelectorAll("[data-data]").forEach(b => b.classList.toggle("selected", b.dataset.data === dataTab));
}
function showStatus(text, error = false) {
  $("#task-status").textContent = text;
  $("#task-status").classList.toggle("error", error);
}
function renderProposals(proposals) {
  $("#proposals").replaceChildren();
  proposals.forEach(proposal => {
    const card = create("div", "proposal");
    const state = proposal.status === "approved" ? "已完成" : "待确认";
    card.append(create("strong", "", `${state} · ${proposal.kind === "restock" ? "补货" : "配送"}`));
    const payload = proposal.payload;
    card.append(create("p", "", proposal.kind === "restock" ? `${payload.sku} · ${payload.quantity} 件` : `订单 ${payload.order_id}`));
    if (proposal.status === "approved") {
      card.append(create("span", "", proposal.result.message));
    } else {
      const button = create("button", "", "确认执行演示操作");
      button.disabled = busy;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const result = await (await api(`/api/proposals/${proposal.id}/approve`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId })
          })).json();
          Object.assign(proposal, result);
          renderProposals(proposals);
          showStatus(result.result.message + "，页面数据已更新。");
          await refresh();
        } catch (error) { showStatus(error.message, true); button.disabled = false; }
      });
      card.append(button);
    }
    $("#proposals").append(card);
  });
}
async function loadProposals() {
  const proposals = await (await api(`/api/proposals/${sessionId}`)).json();
  renderProposals(proposals);
}
function handleEvent(event, payload, message) {
  if (event === "step") {
    const li = create("li", "", labels[payload.node] || payload.node);
    if (payload.node === "orchestrator") li.append(create("small", "", `协作顺序：${payload.data.pending.map(n => ({product:"商品",order:"订单",support:"客服",marketing:"营销"}[n])).join(" → ")}`));
    const result = payload.data?.results?.[payload.node];
    if (result?.supplier) li.append(create("small", "", `供应商 A2A 任务 ${result.supplier.task_id.slice(0, 8)} · MCP 库存查询`));
    if (result?.logistics) li.append(create("small", "", `物流 A2A 任务 ${result.logistics.task_id.slice(0, 8)} · MCP 运费查询`));
    $("#timeline").append(li);
  }
  if (event === "result") {
    $("#answer-wrap").hidden = false;
    $("#answer").textContent = payload.answer;
    $("#request-label").textContent = payload.request_id.slice(0, 8);
    showStatus("协作已完成。可在下方查看结果和待确认单。");
    $("#activity-empty").hidden = true;
    const history = create("article", "history-item"); history.append(create("strong", "", message), create("p", "", payload.answer.slice(0, 160)), create("small", "", `${new Date().toLocaleTimeString("zh-CN")} · ${payload.request_id.slice(0, 8)}`));
    $("#history").prepend(history);
  }
  if (event === "error") showStatus(`${payload.message}（任务 ${payload.request_id.slice(0, 8)}）`, true);
}
$("#task-form").addEventListener("submit", async event => {
  event.preventDefault();
  const message = $("#message").value.trim(); if (!message || busy) return;
  busy = true; $("#submit").disabled = true; $("#new-session").disabled = true;
  $("#timeline").replaceChildren(); $("#answer-wrap").hidden = true; $("#proposals").replaceChildren();
  showStatus("助手正在处理你的任务…");
  let gotResult = false;
  try {
    const response = await api("/api/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, session_id: sessionId }) });
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        const eventName = frame.split("\n").find(l => l.startsWith("event: "))?.slice(7);
        const body = frame.split("\n").filter(l => l.startsWith("data: ")).map(l => l.slice(6)).join("\n");
        if (eventName && body) { handleEvent(eventName, JSON.parse(body), message); if (eventName === "result") gotResult = true; }
      }
      if (done) break;
    }
    if (!gotResult && !$("#task-status").classList.contains("error")) throw new Error("连接中断，请重试。");
  } catch (error) { showStatus(error.message, true); }
  finally {
    busy = false; $("#submit").disabled = false; $("#new-session").disabled = false;
    await Promise.all([refresh(), loadProposals().catch(error => showStatus(error.message, true))]);
  }
});
document.querySelectorAll(".scenario").forEach(button => button.addEventListener("click", () => {
  if (busy) return; $("#message").value = button.dataset.prompt; $("#message").focus();
  if (innerWidth < 1150) $("#task-form").scrollIntoView({ behavior: "smooth", block: "center" });
}));
document.querySelectorAll("[data-data]").forEach(button => button.addEventListener("click", () => { dataTab = button.dataset.data; renderTable(); }));
document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => {
  const tab = button.dataset.tab;
  document.querySelectorAll("[data-tab]").forEach(b => b.classList.toggle("active", b === button));
  $("#page-label").textContent = button.textContent.trim().slice(1).trim();
  const titles = { overview: ["跨越边界，协作有序。", "从库存到交付，把复杂的跨境任务交给一个协作网络。"], products: ["商品有据，库存有数。", "查看演示商品目录，协调供应商查询与补货。"], orders: ["每一程交付，都清晰可见。", "跟进跨境订单，查询运费并确认演示配送。"], activity: ["让协作过程，留下清晰记录。", "这里展示本次会话中的任务与处理结果。刷新后可继续查看待确认单。"] };
  $("#page-title").textContent = titles[tab][0]; $("#page-description").textContent = titles[tab][1];
  $("#scenario-section").hidden = tab !== "overview";
  $(".data-panel").hidden = tab === "activity";
  $("#activity-panel").hidden = !["overview", "activity"].includes(tab);
  if (["products", "orders"].includes(tab)) { dataTab = tab; renderTable(); }
}));
$("#new-session").addEventListener("click", () => {
  sessionId = crypto.randomUUID(); sessionStorage.setItem("border-session", sessionId);
  $("#timeline").replaceChildren(); $("#history").replaceChildren(); $("#proposals").replaceChildren();
  $("#answer-wrap").hidden = true; $("#activity-empty").hidden = false; $("#message").value = "";
  showStatus("新会话已创建，商品和市场记忆已重置。");
});
$("#refresh").addEventListener("click", refresh);
(async () => {
  try {
    const health = await (await api("/api/health")).json();
    $("#connection").textContent = "● 本地服务已连接";
    if (health.model === "configured") $("#mode-label").textContent = "模型辅助模式 · 演示业务数据";
    await Promise.all([refresh(), loadProposals()]);
  } catch (error) { $("#connection").textContent = "连接失败"; showStatus(error.message, true); }
})();
