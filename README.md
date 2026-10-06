# RSI-Agent

[English](README.en.md) | 中文

面向研发 Pull Request 的证据驱动风险审查 Agent。项目以 AgentScope 作为 Agent 执行底座，在其上实现 PR 快照、风险面路由、专家分析、独立验证、证据门禁和可恢复的后端任务闭环。

## 核心思想

```text
PR Webhook
  -> 幂等任务与提交快照
  -> 静态预分析与初筛
  -> Security / Correctness 专家 Agent
  -> 独立 Verifier 复核证据
  -> 风险告警与 PR 反馈
  -> 离线策略候选 Replay / Validation / Holdout / Shadow
```

系统不把细粒度漏洞类型作为第一层路由，而是使用有限风险面（输入与敏感 Sink、权限边界、解析序列化、状态并发、依赖配置、业务回归）。未匹配场景进入通用检查或人工复核，不默认视为安全。

AgentScope 负责通用 Agent 执行、工具调用和基础观测；项目自身负责 PR 快照幂等、风险路由、证据契约、反馈和策略版本。策略更新在离线评测后再由用户显式激活，避免未经验证的策略直接影响审查结果。

## 项目结构

```text
rsi_agent/
  models.py      # PR 快照、任务、风险特征和结果模型
  store.py       # SQLite 事件、任务和状态迁移
  routing.py     # 风险面提取与专家路由
  service.py     # 审查任务编排边界
  agents.py       # AgentRunner、工具权限和证据契约
  worker.py       # 带重试与超时边界的任务执行器
  memory.py       # SQLite FTS5 历史记忆和仓库 Skill 加载
  feedback.py     # 反馈事件与策略候选持久化
  policy.py       # Validation/Holdout 策略门禁
  replay.py       # 可注入检测器的离线 Replay/Shadow
  http_api.py     # 本地反馈 HTTP 接口
  api.py          # FastAPI Webhook 接入
  github_api.py   # GitHub PR 只读客户端
tests/             # 自动化测试
docs/              # AgentScope 集成说明
```

## 快速开始

### 1. 安装

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
```

### 2. 运行测试

```powershell
python -m pytest -q
```

当前测试不需要模型 API Key 或 GitHub 公网 Webhook。

真实 GitHub 联调需要设置 `GITHUB_TOKEN` 和 `GITHUB_WEBHOOK_SECRET`，然后运行：

```powershell
python -m rsi_agent.run_api --port 8787
```

策略候选只会在离线评测通过后由用户显式激活。仓库级记忆和 Skill 使用 `.rsi/memory` 与 `.rsi/skills` 目录；历史反馈和评测结果保存在 SQLite。项目不要求向量数据库，历史记录使用 FTS5/BM25，仓库文件使用可审计的文本检索。

### 3. 启动本地反馈服务

```powershell
python -m rsi_agent.run_feedback_server --port 8787
```

### 4. 提交反馈

向 `POST /feedback` 提交 JSON。`kind` 支持 `accepted`、`false_positive`、`missed_risk` 和 `insufficient_evidence`。

```powershell
$payload = @{
  event_id = "feedback-1"
  repo_id = "org/repo"
  pr_number = 1
  head_sha = "<commit-sha>"
  kind = "accepted"
  note = "reviewed by maintainer"
  reporter = "local"
} | ConvertTo-Json

Invoke-RestMethod -Uri http://127.0.0.1:8787/feedback `
  -Method Post -ContentType "application/json" -Body $payload
```

重复的 `event_id` 会被幂等忽略。`missed_risk` 反馈必须包含说明文本，才能进入重新审查链路。
