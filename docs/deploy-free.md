# 免费上线到 Render

这一方案只创建 **1 个 Free Web Service**，不创建数据库、磁盘或付费模型服务。默认使用 SQLite、本地检索、进程内 A2A/MCP 协议服务，保留补货与配送等完整演示流程。

## 部署

1. 打开 [部署此项目](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2Fsir-svg%2Fcrossborder-commerce-agent-hub)。
2. 登录 Render。当前仓库是私有仓库，需要允许 Render 访问它；不用把仓库改为公开。如果没有找到仓库，先在 Render 中连接 GitHub，并允许其访问 `crossborder-commerce-agent-hub`。
3. Render 会读取仓库根目录的 `render.yaml`。确认只有 `border-commerce-demo` 一个服务，套餐为 **Free**；不选择任何付费升级。
4. 确认创建，等待构建和部署完成。部署成功后点击平台给出的 `https://…onrender.com` 网址。最终网址由平台分配，不要自行猜测。

也可以在 Render 中点 **New → Blueprint**，选择这个仓库和 `main` 分支，然后使用 `render.yaml`。

## 第一次打开

浏览器会弹出用户名和密码输入框：

- 用户名：`demo`。
- 密码：Render 服务的 **Environment** 页面中自动生成的 `DEMO_PASSWORD`。在平台上显示/复制即可，不写进 GitHub 或 README。

想换密码时，在 Render 的 Environment 中设置至少12位的 `DEMO_PASSWORD`，保存并重新部署。向面试官分享网址时，单独提供访问密码。它是共用演示密码，不是每人独立的账号系统。

页面、接口文档、数据接口、确认操作和对外 A2A 入口都受同一密码保护；`/api/health` 单独放行，供平台判断进程是否启动。只有本机开发且未设置密码时才保持免登录。

## 如果选择手动创建 Web Service

| 配置项 | 填写内容 |
|---|---|
| 仓库 | `sir-svg/crossborder-commerce-agent-hub` |
| 分支 | `main` |
| Language / Runtime | **Python 3**，不要选择自动发现的 Docker |
| 套餐 | **Free** |
| Build Command | `uv sync --frozen --no-dev` |
| Start Command | `.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health Check Path | `/api/health` |

将 `render.yaml` 中列出的环境变量添加到服务中。手动创建时要自己设置 `DEMO_PASSWORD`；`generateValue` 只由 Blueprint 自动处理。Python 版本由 `.python-version` 固定为3.13系列。这里使用原生 Python，不需要构建 Docker 镜像。

## 免费版的实际限制

- 闲置15分钟后服务可能休眠，再次访问需等待唤醒，通常约1分钟。
- 库存、配送单和会话记忆是临时数据；休眠、重启或重新部署后会重新初始化。页面会显示这条提醒。
- 访问密码保存在 Render 的环境配置中，应用数据重置不影响这个密码。
- 所有人共享一套演示商品与订单，不是多人独立店铺。浏览器会话仍有各自的记忆与确认单归属。
- 关闭自动部署，避免日常推送意外重置演示状态。需要更新时，在 Render 手动执行部署。
- 免费额度、带宽和构建时间受 Render 账号的当期规则约束。若提示付费或 Free 不可用，先停止，不选择付费替代方案。
- 默认没有配置任何模型密钥，因此不会产生模型调用费用。Redis、Milvus 和真实店铺均不在这个免费部署中运行。

## 部署后核对

1. `/api/health` 返回 `status: ok`。
2. 不提供密码访问首页、商品接口或确认接口应返回401；输入正确密码后能打开页面。
3. 提交“查询 SKU-1001 的库存，并补货50件”，确认后库存从18变68。
4. 提交“查看订单 CB-2026002，安排配送”，确认后出现 `DEMO-` 运单。

本地验证不能代替平台构建：拿到部署记录和实际网址后，仍需检查平台日志、页面、密码和 SSE 任务。

官方参考：[Blueprint 配置](https://render.com/docs/blueprint-spec)、[Python 版本](https://render.com/docs/python-version)、[私有仓库部署](https://render.com/docs/deploy-to-render)、[免费版限制](https://render.com/docs/free)。
