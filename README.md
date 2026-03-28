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
