# Session Browser

一个独立的本地 Session 浏览工具，面向 Antigravity 本地数据目录。

仓库里只有两个 Python 文件：

- `store.py`
  负责读取本地 session 数据，并对外提供四类能力：
  - session 列表
  - session 的文件列表
  - session 文件内容
  - session 的历史对话记录，按角色拆分

- `ui.py`
  负责启动本地 Web UI，并调用 `store.py` 暴露的能力。

## 数据来源

默认读取以下本地目录：

- `~/.gemini/antigravity/conversations`
- `~/.gemini/antigravity/brain`

历史对话优先使用本地缓存；必要时会尝试从 Antigravity 的本地运行时接口补齐。

## 实现原理

整体只有两层：

### 1. `store.py`

`store.py` 是唯一的数据入口，内部的 `AntigravitySessionStore` class 负责对外提供四类能力：

1. `session` 列表
2. `session` 的文件列表
3. 文件内容
4. 按角色拆分的历史对话记录

它的工作方式是：

- 扫描 `~/.gemini/antigravity/conversations/*.pb` 生成 session 列表
- 扫描 `~/.gemini/antigravity/brain/<session_id>/` 生成文件列表
- 直接读取具体文件内容
- 读取或恢复这个 session 的历史消息

历史消息恢复链路如下：

1. 先读本地缓存 `brain/<id>/.live-cache/messages.json`
2. 如果没有缓存，就尝试读取旧的 step 缓存
3. 如果当前 Antigravity 的本地 language server 正在运行，就调用本地 HTTP API：
   - `GetCascadeTrajectorySteps`
4. 将返回的 step 数据转换成按角色的消息：
   - `CORTEX_STEP_TYPE_USER_INPUT` -> `user`
   - `CORTEX_STEP_TYPE_NOTIFY_USER` -> `assistant`
5. 成功后把结果写回 `.live-cache`，后续直接复用

### 2. `ui.py`

`ui.py` 只是一个很薄的 Web UI 壳：

- 启动一个本地 HTTP 服务
- 暴露固定的 JSON API
- 用浏览器页面消费这些 API

当前 API 只有：

- `/api/sessions`
- `/api/sessions/<id>/files`
- `/api/sessions/<id>/files/<name>`
- `/api/sessions/<id>/files/<name>/raw`
- `/api/sessions/<id>/messages`

UI 结构是三栏：

- 左边：session 列表
- 中间：文件列表
- 右边：默认显示历史对话；点文件后显示文件内容

图片文件不走文本接口，而是走 `/raw`，所以可以被浏览器正常渲染，并支持放大查看。

## 为什么不直接解密 `.pb`

这个工具没有走“直接硬解 `.pb`”的路线，原因是：

- `conversations/*.pb` 本体是高熵二进制容器
- `protoc --decode_raw` 对目标文件直接失败
- 直接字符串抽取基本拿不到稳定正文

相比之下，Antigravity 自己已经有一套本地 runtime API 能返回结构化的 step 数据。  
因此这里选择的是更稳定的方案：

- 优先使用 Antigravity 自己的 runtime 数据
- 再将结果缓存成自己的消息结构
- 让 UI 读取缓存，而不是每次都依赖 live 进程

这也是为什么这个工具在多数情况下比直接逆向 `.pb` 更可靠。

## 运行

在仓库目录执行：

```bash
python3 ui.py --host 127.0.0.1 --port 8770 --open
```

然后在浏览器访问：

```text
http://127.0.0.1:8770/
```

## 对外功能

这个工具只提供以下功能：

1. session 列表
2. session 的文件列表
3. session 文件内容
4. session 的历史对话记录，按角色分组

## 开发说明

- 保持仓库精简，默认只保留 `store.py` 和 `ui.py` 两个核心文件
- 如需扩展，请优先在这两个文件内部重构，而不是继续增加分散脚本
