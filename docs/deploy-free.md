# 免费上线到 Render

这一方案只创建 **1 个 Free Web Service**，不创建数据库或付费磁盘。使用 SQLite、本地检索、进程内 A2A/MCP 协议服务，保留补货与配送等完整演示流程。没有密钥时运行规则演示；添加密钥后会调用外部模型，其费用由模型账号承担。

## 部署

1. 打开 [部署此项目](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2Fsir-svg%2Fcrossborder-commerce-agent-hub)。
2. 登录 Render。当前仓库是私有仓库，需要允许 Render 访问它；不用把仓库改为公开。如果没有找到仓库，先在 Render 中连接 GitHub，并允许其访问 `crossborder-commerce-agent-hub`。
3. Render 会读取仓库根目录的 `render.yaml`。确认只有 `border-commerce-demo` 一个服务，套餐为 **Free**；不选择任何付费升级。
4. 确认创建，等待构建和部署完成。部署成功后点击平台给出的 `https://…onrender.com` 网址。最终网址由平台分配，不要自行猜测。

也可以在 Render 中点 **New → Blueprint**，选择这个仓库和 `main` 分支，然后使用 `render.yaml`。

## 第一次打开

打开网址后会显示 BORDER 登录页面，在页面中输入：

- 用户名：`demo`。
- 密码：Render 服务的 **Environment** 页面中自动生成的 `DEMO_PASSWORD`。在平台上显示/复制即可，不写进 GitHub 或 README。

想换密码时，在 Render 的 Environment 中设置至少12位的 `DEMO_PASSWORD`，保存并重新部署。向面试官分享网址时，单独提供访问密码。它是共用演示密码，不是每人独立的账号系统。

登录后使用 HttpOnly 会话 Cookie，最长8小时有效，密码不会保存在 Cookie 或浏览器本地存储中。右上角“退出登录”会撤销当前会话；服务重启也会让会话失效，需要重新登录。原有 Render 密码设置不需要修改。

页面、接口文档、数据接口、确认操作和对外 A2A 入口都受会话保护；登录页面、它所需的样式和脚本、`/api/health` 可以公开访问。未登录访问页面会跳到登录页，接口则返回401，不会再触发浏览器原生登录弹窗。本机开发且未设置密码时保持免登录。

## 如果选择手动创建 Web Service

| 配置项             | 填写内容                                                                 |
| ------------------ | ------------------------------------------------------------------------ |
| 仓库               | `sir-svg/crossborder-commerce-agent-hub`                                 |
| 分支               | `main`                                                                   |
| Language / Runtime | **Python 3**，不要选择自动发现的 Docker                                  |
| 套餐               | **Free**                                                                 |
| Build Command      | `uv sync --frozen --no-dev`                                              |
| Start Command      | `.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health Check Path  | `/api/health`                                                            |

将 `render.yaml` 中列出的环境变量添加到服务中。手动创建时要自己设置 `DEMO_PASSWORD`；`generateValue` 只由 Blueprint 自动处理。需要真实模型时再添加 `LLM_API_KEY`，地址和模型名已预设，[查看模型配置](model-setup.md)。Python 版本由 `.python-version` 固定为3.13系列。这里使用原生 Python，不需要构建 Docker 镜像。

## 免费版的实际限制

- 闲置15分钟后服务可能休眠，再次访问需等待唤醒，通常约1分钟。
- 库存、配送单和会话记忆是临时数据；休眠、重启或重新部署后会重新初始化。页面会显示这条提醒。
- 访问密码保存在 Render 的环境配置中，应用数据重置不影响这个密码。
- 所有人共享一套演示商品与订单，不是多人独立店铺。浏览器会话仍有各自的记忆与确认单归属。
- Blueprint 配置默认关闭自动部署；手动创建的服务以控制台设置为准。已打开自动部署时，推送代码会触发更新，并可能重置演示数据。
- 免费额度、带宽和构建时间受 Render 账号的当期规则约束。若提示付费或 Free 不可用，先停止，不选择付费替代方案。
- 未配置密钥时不产生模型调用费用；添加 `LLM_API_KEY` 后按模型提供商规则计费。Redis、Milvus 和真实店铺均不在这个免费部署中运行。

## 部署后核对

1. `/api/health` 返回 `status: ok`。
2. 未登录访问首页应跳到 `/login`，接口返回401；在登录页输入正确密码后进入工作台，刷新后仍然有效，退出后接口重新返回401。
3. 提交“查询 SKU-1001 的库存，并补货50件”，确认后库存从18变68。
4. 提交“查看订单 CB-2026002，安排配送”，确认后出现 `DEMO-` 运单。

本地验证不能代替平台构建：拿到部署记录和实际网址后，仍需检查平台日志、页面、密码和 SSE 任务。

官方参考：[Blueprint 配置](https://render.com/docs/blueprint-spec)、[Python 版本](https://render.com/docs/python-version)、[私有仓库部署](https://render.com/docs/deploy-to-render)、[免费版限制](https://render.com/docs/free)。
