"use strict";

// Independent business forms use typed endpoints. They never submit chat prompts.
(() => {
  const root = $("#module-workspace");
  const definitions = {
    products: [
      "商品中心",
      "核对本地与供应商库存，按数量或目标库存补货。",
      "product",
    ],
    orders: [
      "订单中心",
      "查看订单详情、物流渠道和运费，创建配送确认单。",
      "order",
    ],
    support: [
      "客服中心",
      "按主题检索售后政策、配送、税费和商品评论。",
      "support",
    ],
    marketing: [
      "营销中心",
      "选择商品和市场，直接生成多语言推广草稿。",
      "marketing",
    ],
  };
  const languageNames = { zh: "中文", en: "英文", de: "德文", ja: "日文" };
  const views = new Map();

  function element(tag, className = "", text) {
    return create(tag, className, text);
  }

  function field(label, control) {
    const group = element("label", "module-field");
    group.append(element("span", "", label), control);
    return group;
  }

  function select(name, choices = []) {
    const node = element("select");
    node.name = name;
    setChoices(node, choices);
    return node;
  }

  function setChoices(node, choices) {
    const selected = node.value;
    node.replaceChildren(
      ...choices.map(([value, label]) => {
        const option = element("option", "", label);
        option.value = value;
        return option;
      }),
    );
    if (choices.some(([value]) => value === selected)) node.value = selected;
  }

  function button(text, action, secondary = false) {
    const node = element(
      "button",
      secondary ? "module-secondary" : "module-primary",
      text,
    );
    node.type = "submit";
    node.value = action;
    return node;
  }

  function productChoices(includeAll = false) {
    return [
      ...(includeAll ? [["", "通用政策 / 不指定商品"]] : []),
      ...data.products.map((p) => [p.sku, p.name + " · " + p.sku]),
    ];
  }

  function orderChoices() {
    return data.orders.map((o) => [
      o.order_id,
      o.order_id +
        " · " +
        marketNames[o.country] +
        " · " +
        (statuses[o.status] || o.status),
    ]);
  }

  function status(view, message, error = false) {
    view.status.textContent = message;
    view.status.classList.toggle("error", error);
  }

  function optionsForMarket(view) {
    const p = data.products.find(
      (item) => item.sku === view.form.elements.sku.value,
    );
    const marketSelect = view.form.elements.country;
    setChoices(
      marketSelect,
      (p?.markets || []).map((code) => [code, marketNames[code] || code]),
    );
  }

  function build(tab) {
    const [name, description, endpoint] = definitions[tab];
    const section = element("section", "module-page");
    section.dataset.module = tab;
    const head = element("div", "module-heading");
    head.append(element("h1", "", name), element("p", "", description));
    const grid = element("div", "module-grid");
    const formBox = element("section", "module-card");
    const form = element("form", "module-form");
    const result = element("div", "module-result");
    const resultBox = element("section", "module-card");
    const resultHeading = element("h2", "module-card-heading", "业务结果");
    resultBox.append(resultHeading, result);
    const view = {
      tab,
      endpoint,
      section,
      form,
      result,
      status: element("p", "module-status"),
      proposalBox: null,
    };
    view.status.setAttribute("role", "status");
    const sku =
      tab !== "orders"
        ? select("sku", productChoices(tab === "support"))
        : null;
    const notes = element("textarea");
    notes.rows = 4;
    notes.name = tab === "support" ? "question" : "notes";
    notes.maxLength = tab === "support" ? 1000 : 600;
    if (tab === "products") {
      const quantity = element("input");
      quantity.name = "quantity";
      quantity.type = "number";
      quantity.min = "1";
      quantity.max = "10000";
      quantity.step = "1";
      quantity.value = "50";
      form.append(
        field("商品", sku),
        field(
          "补货方式",
          select("method", [
            ["quantity", "新增库存数量"],
            ["target_stock", "补充到目标库存"],
          ]),
        ),
        field("数量（件）", quantity),
      );
      form.append(
        element(
          "p",
          "module-hint",
          "库存查询不改变商品；补货需在待确认单中二次确认。",
        ),
      );
      const buttons = element("div", "module-actions");
      buttons.append(
        button("查询库存", "inventory"),
        button("创建补货单", "restock", true),
      );
      form.append(buttons);
    } else if (tab === "orders") {
      form.append(field("订单", select("order_id", orderChoices())));
      form.append(
        element("p", "module-hint", "按订单目的地和商品数量获取模拟物流报价。"),
      );
      const buttons = element("div", "module-actions");
      buttons.append(
        button("查询订单与物流", "quote"),
        button("创建配送单", "shipment", true),
      );
      form.append(buttons);
    } else if (tab === "support") {
      const topic = select("topic", [
        ["policy", "退货与退款"],
        ["shipping", "配送与时效"],
        ["customs", "税费与清关"],
        ["reviews", "商品评论分析"],
        ["general", "一般咨询"],
      ]);
      notes.placeholder = "例如：客户申请退货，需要满足什么条件？（可选）";
      form.append(
        field("查询主题", topic),
        field("商品", sku),
        field("补充问题（可选）", notes),
        button("查询资料与回答", "search"),
      );
    } else {
      const country = select("country");
      notes.placeholder = "例如：通勤场景，简洁、口语化，适合商品广告。";
      form.append(
        field("推广商品", sku),
        field("目标市场", country),
        field("文案要求（可选）", notes),
        element(
          "p",
          "module-hint",
          "自动提供中文、英文，以及目标市场对应的德文或日文草稿。",
        ),
        button("生成营销草稿", "generate"),
      );
      sku.addEventListener("change", () => optionsForMarket(view));
      optionsForMarket(view);
    }
    form.noValidate = true;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(view, event.submitter?.value || "inventory");
    });
    formBox.append(
      element("h2", "module-card-heading", "业务操作"),
      form,
      view.status,
    );
    grid.append(formBox, resultBox);
    section.append(head, grid);
    if (["products", "orders"].includes(tab)) {
      const proposals = element("section", "module-confirm-section");
      view.proposalBox = element("div", "module-proposals");
      proposals.append(
        element("h2", "module-card-heading", "本会话确认单"),
        view.proposalBox,
      );
      section.append(proposals);
    }
    empty(view);
    return view;
  }

  function empty(view) {
    view.result.replaceChildren(
      element(
        "div",
        "module-empty",
        "选择业务对象并提交操作，结果会显示在这里。",
      ),
    );
    status(view, "");
  }

  function requestBody(view, action) {
    const values = Object.fromEntries(new FormData(view.form));
    const body = { session_id: sessionId };
    if (view.tab === "products") {
      if (!values.sku) throw new Error("请先选择商品。");
      Object.assign(body, { sku: values.sku, action });
      if (action === "restock") {
        const quantity = Number(values.quantity);
        if (!Number.isInteger(quantity) || quantity < 1 || quantity > 10000) {
          throw new Error("请选择 1 至 10000 之间的整数数量。");
        }
        body[values.method] = quantity;
      }
    } else if (view.tab === "orders") {
      if (!values.order_id) throw new Error("请先选择订单。");
      Object.assign(body, { order_id: values.order_id, action });
    } else if (view.tab === "support") {
      if (values.topic === "reviews" && !values.sku)
        throw new Error("评论分析需要选择具体商品。");
      Object.assign(body, {
        sku: values.sku || null,
        topic: values.topic,
        question: values.question,
      });
    } else {
      if (!values.sku || !values.country)
        throw new Error("请选择商品和目标市场。");
      Object.assign(body, {
        sku: values.sku,
        country: values.country,
        notes: values.notes,
      });
    }
    return body;
  }

  async function run(view, action) {
    if (busy) {
      status(view, "当前会话正在处理其他任务，请稍后再试。", true);
      return;
    }
    let body;
    try {
      body = requestBody(view, action);
    } catch (error) {
      status(view, error.message, true);
      return;
    }
    busy = true;
    $("#submit").disabled = $("#new-session").disabled = true;
    syncBusy();
    view.result.replaceChildren(element("div", "module-empty", "正在处理…"));
    status(view, "任务执行中，请稍候。");
    try {
      const result = await (
        await api("/api/modules/" + view.endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      ).json();
      showResult(view, result);
      status(
        view,
        result.proposals.length
          ? "待确认单已生成，请核对数量后确认。"
          : "操作完成。",
      );
      await Promise.all([refresh(), loadHistory()]);
    } catch (error) {
      view.result.replaceChildren(
        element("div", "module-empty", "请求未完成，请检查提示后重试。"),
      );
      status(view, error.message, true);
    } finally {
      busy = false;
      $("#submit").disabled = $("#new-session").disabled = false;
      syncBusy();
      await loadProposals().catch((error) => status(view, error.message, true));
    }
  }

  function stats(title, pairs) {
    const block = element("section", "module-detail");
    const values = element("dl", "module-values");
    for (const [label, value] of pairs) {
      const item = element("div");
      item.append(element("dt", "", label), element("dd", "", String(value)));
      values.append(item);
    }
    block.append(element("h3", "", title), values);
    return block;
  }

  function showResult(view, response) {
    const fact = response.facts;
    const result = view.result;
    result.replaceChildren();
    if (fact.product) {
      const p = fact.product;
      result.append(
        stats(p.name + " · " + p.sku, [
          ["商品售价", "USD " + p.price_usd.toFixed(2)],
          ["查询时本地库存", p.stock + " 件"],
          [
            "销售市场",
            p.markets.map((code) => marketNames[code] || code).join(" / "),
          ],
        ]),
        element("p", "module-text", p.description),
      );
    }
    if (fact.supplier) {
      result.append(
        stats("供应商返回", [
          ["可用库存", fact.supplier.available + " 件"],
          ["预计补货交期", fact.supplier.lead_days + " 天"],
          ["仓库", fact.supplier.warehouse || "—"],
        ]),
      );
    }
    if (fact.order) {
      const o = fact.order;
      result.append(
        stats(o.order_id, [
          ["查询时状态", statuses[o.status] || o.status],
          ["目的地", marketNames[o.country] || o.country],
          ["商品 / 数量", o.sku + " / " + o.quantity + " 件"],
          ["订单金额", "USD " + o.total_usd.toFixed(2)],
          ["运单", o.tracking || "尚未生成"],
        ]),
      );
    }
    if (fact.logistics) {
      const quote = fact.logistics;
      result.append(
        stats("物流演示报价（不含税费）", [
          ["预计运费", "USD " + quote.cost_usd.toFixed(2)],
          ["预计时效", quote.eta_days.join("–") + " 天"],
          ["渠道", quote.channel],
        ]),
      );
    }
    for (const key of ["message", "analysis", "notice"]) {
      if (fact[key])
        result.append(
          element(
            "p",
            key === "notice" ? "module-warning" : "module-text",
            fact[key],
          ),
        );
    }
    for (const [language, draft] of Object.entries(fact.drafts || {})) {
      const card = element("section", "module-detail module-draft");
      const title = element("div", "module-draft-title");
      const copy = element("button", "text-button", "复制");
      copy.type = "button";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(draft);
          status(view, "文案已复制。");
        } catch {
          status(view, "复制未完成，可手动选中文字。", true);
        }
      });
      title.append(
        element("h3", "", languageNames[language] || language.toUpperCase()),
        copy,
      );
      card.append(title, element("p", "module-text", draft));
      result.append(card);
    }
    if (fact.sources?.length) {
      const sources = element("section", "module-detail");
      sources.append(element("h3", "", "参考资料"));
      for (const source of fact.sources) {
        const details = element("details", "module-source");
        details.append(
          element("summary", "", source.title + " · " + source.source),
          element("p", "module-text", source.text),
        );
        sources.append(details);
      }
      result.append(sources);
    }
    for (const warning of response.warnings)
      result.append(element("p", "module-warning", warning));
    result.append(
      element(
        "p",
        "module-meta",
        (["products", "orders"].includes(view.tab)
          ? "业务工具直接查询 · 不消耗模型额度"
          : usageLabel(response.model_usage)) +
          " · " +
          response.request_id.slice(0, 8),
      ),
    );
  }

  let latestProposals = [];
  function proposals(items) {
    latestProposals = items;
    for (const view of views.values()) {
      if (!view.proposalBox) continue;
      const kind = view.tab === "products" ? "restock" : "shipment";
      const relevant = items.filter((item) => item.kind === kind);
      renderProposalCards(relevant, view.proposalBox, (message, error) =>
        status(view, message, error),
      );
      if (!relevant.length)
        view.proposalBox.append(element("p", "module-hint", "暂无确认单。"));
    }
  }

  function syncBusy() {
    for (const view of views.values()) {
      for (const control of view.form.querySelectorAll("button[type=submit]"))
        control.disabled = busy;
      if (view.proposalBox) {
        for (const control of view.proposalBox.querySelectorAll("button"))
          control.disabled = busy;
      }
    }
    for (const control of $("#proposals").querySelectorAll("button"))
      control.disabled = busy;
  }

  window.borderModules = {
    open(tab) {
      if (!definitions[tab]) return;
      if (!views.has(tab)) views.set(tab, build(tab));
      const view = views.get(tab);
      root.replaceChildren(view.section);
      proposals(latestProposals);
      syncBusy();
    },
    refresh() {
      for (const view of views.values()) {
        if (view.tab === "orders")
          setChoices(view.form.elements.order_id, orderChoices());
        else
          setChoices(
            view.form.elements.sku,
            productChoices(view.tab === "support"),
          );
        if (view.tab === "marketing") optionsForMarket(view);
      }
    },
    select(value) {
      const view = views.get(activeTab);
      if (!view) return;
      const control =
        view.form.elements[view.tab === "orders" ? "order_id" : "sku"];
      control.value = value;
      control.focus();
      view.form.scrollIntoView({ block: "center" });
    },
    proposals,
    syncBusy,
    reset() {
      latestProposals = [];
      for (const view of views.values()) empty(view);
      proposals([]);
    },
  };
})();
