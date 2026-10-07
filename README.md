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
  queue.py        # 内存队列与 Redis Stream 适配器
  run_worker.py   # Redis Stream Worker 入口
  github_comments.py # GitHub 评论格式化
  agentscope_runner.py # AgentScope 执行边界
  api.py          # FastAPI Webhook 接入
  github_api.py   # GitHub PR 只读客户端
tests/             # 自动化测试
docs/              # AgentScope 集成说明
```

## 快速开始

### 1. 前置条件

- Python 3.11+
- Docker Desktop
- GitHub Token（接入 GitHub 时需要）
- DeepSeek API Key（运行真实 Agent 时需要）

### 2. 安装

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
```

复制配置模板并填写本地凭据：

```powershell
Copy-Item .env.example .env
```

`.env` 已被 Git 忽略，不要提交真实 Token、密码或 Webhook Secret。

默认配置使用本机 Docker Redis：

```dotenv
DEPLOYMENT_MODE=local
REDIS_URL=redis://127.0.0.1:6379/0
```

### 3. 启动本地依赖和服务

```powershell
docker compose up -d redis
python -m rsi_agent.run_api --port 8787
```

另开终端启动 Worker：

```powershell
python -m rsi_agent.run_worker
```

服务启动后打开 `http://127.0.0.1:8787/` 进入审查控制台。页面会列出最近的 PR 任务；选择任务后可以查看审查结论、Finding、风险面、证据引用、涉及文件和任务过程。页面中的“未发现可报告风险”表示检测器已完成但没有输出 Finding，不等于代码绝对安全。

Webhook 到达后，API 负责校验签名并创建任务，Worker 负责执行审查。任务完成后刷新控制台即可查看结果；若任务失败，报告会显示失败分类和恢复所需的信息。

或使用一键脚本：

```powershell
.\scripts\start-local.ps1 -StartWorker
```

### 4. 运行测试

```powershell
python -m pytest -q
```

当前测试不需要模型 API Key 或 GitHub 公网 Webhook。

真实 GitHub 联调需要设置 `GITHUB_TOKEN` 和 `GITHUB_WEBHOOK_SECRET`，然后运行：

```powershell
python -m rsi_agent.run_api --port 8787
```

也可以使用一键脚本。脚本会检查配置、安装服务依赖并验证 Redis 连接：

```powershell
.\scripts\start-local.ps1
```

如果暂时不使用 Redis：

```powershell
.\scripts\start-local.ps1 -SkipRedisCheck
```

真实异步 Worker 使用 Redis Stream：

```powershell
python -m rsi_agent.run_worker
```

GitHub Webhook 需要至少选择 `Pull requests`；启用评论反馈时还要选择 `Issue comments`。Webhook 入口只负责校验并入队，Worker 才执行审查。

安装真实 AgentScope Runner：

```powershell
python -m pip install -e ".[agent]"
```

配置 `DEEPSEEK_API_KEY` 后，`run_worker` 会使用 AgentScope 2.0.9 创建 Security、Correctness 和 Verifier Agent。`DEEPSEEK_MODEL` 必须填写 DeepSeek 控制台实际提供的模型 ID。

策略候选只会在离线评测通过后由用户显式激活。仓库级记忆和 Skill 使用 `.rsi/memory` 与 `.rsi/skills` 目录；历史反馈和评测结果保存在 SQLite。项目不要求向量数据库，历史记录使用 FTS5/BM25，仓库文件使用可审计的文本检索。

### 5. 配置 GitHub Webhook

Webhook 至少选择 `Pull requests`；启用评论反馈时再选择 `Issue comments`。本地联调使用 Cloudflare Tunnel 暴露 `/webhooks/github`。

### 6. 提交反馈

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

### 远程 Redis（可选）

```dotenv
DEPLOYMENT_MODE=remote
REDIS_URL=redis://:password@redis.example.com:6379/0
```

远程 Redis 需要自行配置认证、TLS 或私网访问。SSH 隧道只是个人开发环境的可选方式。
