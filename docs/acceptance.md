# 验收记录与目标机器检查

开发基线：[TRD](../TRD.md)。本地自动化测试使用临时真实 Git 仓库，运行实际 shell 安装/健康检查步骤；服务管理测试适配器不修改本机 launchd。真实 system launchd 的启动、崩溃重启及系统重启必须在 Mac mini 完成。

| TRD | 本地验证 | Mac mini 待验收 |
|---|---|---|
| AC1 main 更新可检测 | Git 新提交检测 | MacBook push → GitHub → 定时发现 |
| AC2 无更新不部署 | unchanged 不执行部署 | 一次真实定时无更新运行 |
| AC3 有更新通知 | 检测报告及通知去重记账 | 桌面任务通知实际送达 |
| AC4 未批准不改生产 | 独立缓存、无审批拒绝 | 检查后线上 SHA 与服务不变 |
| AC5 精确 SHA | 批准 B 后 main 出现 C，仍部署 B | 同样场景真实部署 |
| AC6 成功记录 | journal 和 SHA 更新 | 对照实际健康版本 |
| AC7 重启后健康检查 | 成功及失败路径 | HTTP 或业务级检查 |
| AC8 失败回滚 | 健康/安装失败，旧版本恢复；回滚失败 critical | 真实坏版本 → 旧服务恢复 |
| AC9 不执行 PR 代码 | feature SHA 拒绝、main 强制改写拒绝 | PR 不合并不触发代码执行 |
| AC10 重启后服务恢复 | plist RunAtLoad/KeepAlive、普通用户、包装器状态门禁 | 系统重启及子进程崩溃恢复 |
| AC11 关键操作日志 | 命令、阶段、状态记录 | 实际故障可定位 |
| AC12 secrets 不入 Git/日志 | .gitignore、环境隔离、日志脱敏 | 使用测试凭证检查应用实际输出；勿用真实秘密做泄漏测试 |

## 目标机器的两条端到端路径

使用一个可丢弃的 GitHub 测试业务仓库。可复制 examples/demo_service.py 到该仓库，配置：

- service.command：`["python3", "demo_service.py"]`
- deployment.install_command：`""`（标准库示例没有外部依赖）
- health_check：HTTP `http://127.0.0.1:8765/health`
- service.name 与 root 使用单独测试名称，避免影响业务项目。

### 成功路径

1. 在 MacBook 提交初始正常版本 A 并 push main。
2. 在 Mac mini 按安装指南初始化、安装服务并明确批准 A。
3. 在 MacBook 修改 VERSION 为 B，提交并 push main。
4. 等待真实定时检查，确认收到目标 B 的完整 SHA，生产仍为 A。
5. 可选：此时再 push C；回复 B 的通知批准 B。
6. 确认部署的是 B，服务返回 version B，健康检查成功，日志和 journal 均指向 B。
7. 再检查，确认 C 被检测到并等待独立批准。

### 失败路径

1. 在已成功运行的版本基础上，将示例 HEALTHY 改成 False 并 push main。
2. 等待通知并批准坏版本完整 SHA。
3. 确认健康检查在配置超时后失败，自动恢复部署开始前的成功版本。
4. 请求 /health，确认返回旧 version 和 status ok。
5. 检查状态：status failed、rollback_status healthy、deployed_sha 保持旧成功 SHA。

### 服务生命周期与保密

1. 终止测试业务子进程，确认 launchd 自动重启并恢复健康。
2. 重启 Mac mini，在磁盘解锁后确认业务服务恢复；另外确认桌面应用开启后定时检查可运行。
3. 在测试 .env 中写入不具权限的随机测试值，让测试业务输出它，确认日志为 [REDACTED]。检查准备提交的文件不含本机 config/state/logs。
4. 中断一次测试部署，确认状态 deploying 被保留、下次部署拒绝、包装器不启动中断版本；人工 recover 后检查服务。

两条端到端路径及上述生命周期检查完成后，记录日期、机器、GitHub 仓库、SHA、日志位置和结果。当前尚未执行这些 Mac mini 验收，不能标记整个 MVP 完成。

## MacBook 开发验证记录（2026-09-10）

- `scripts/setup.sh`：成功创建独立工具环境，安装 PyYAML 6.0.3。
- `.venv/bin/python -m unittest discover -s tests -v`：26 项全部通过（23.939 秒）。包含真实本机 HTTP 服务、子进程包装器、完整 SHA 绑定、安装/构建/启动/健康失败回滚、并发锁、中断恢复、YAML 安全加载和脱敏。
- 全部 Python 文件 AST 与 shell 入口语法检查通过。
- 从 YAML 模板生成的 plist 通过 `plutil -lint`；限定服务的 sudoers 规则通过 `visudo -cf`。
- 未在 MacBook 安装常驻服务，未创建定时任务，未上传 GitHub。Mac mini 上的项目配置、定时任务注册及真实端到端验收仍待完成。
