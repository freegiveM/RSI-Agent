# RSI-Agent

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
tests/             # 自动化测试
docs/              # 设计说明和使用文档
```

## Quickstart

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
```

当前测试不需要模型 API Key 或 GitHub 公网 Webhook。模型和外部事件接入时，可以复用同一组任务、证据和状态接口。
