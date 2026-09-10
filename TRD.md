# TRD — Mac mini GitHub Repository Watcher & Controlled Deployment System

## 1. 背景

当前有两台开发设备：

* **MacBook**：主要开发设备
* **Mac mini**：长期在线设备，用于部署和运行长期任务 / 服务
* **GitHub Public Repository**：代码的唯一 Source of Truth

目标是建立一套轻量、安全、低维护成本的个人开发与部署工作流。

开发工作全部在 MacBook 完成。

代码提交至 GitHub 后，Mac mini 自动检查 GitHub `main` 分支是否产生新版本。

发现更新时，Mac mini 不允许直接部署。

系统必须：

1. 检测新的 `main` commit
2. 汇总本次更新
3. 通知用户
4. 等待用户明确批准
5. 部署用户批准的确切 commit SHA
6. 重启对应服务
7. 验证服务运行状态
8. 保存部署记录

---

# 2. 核心设计原则

## 2.1 GitHub 是唯一代码真源

MacBook：

```text
develop
↓
test
↓
commit
↓
push
↓
GitHub
```

Mac mini 不承担代码开发。

Mac mini 上的 repository 应视为：

```text
read-only deployment working copy
```

不得直接修改生产代码。

---

## 2.2 只有 main 分支允许部署

Public Repository 中：

```text
PR
feature branch
fork branch
```

均不得触发 Mac mini 部署。

Mac mini 只读取：

```text
origin/main
```

进入 `main` 的代码视为已经经过 repository owner 的批准。

---

## 2.3 检查和部署必须分离

禁止：

```text
检测更新
↓
自动 git pull
↓
自动 restart
```

必须：

```text
检测更新
↓
通知用户
↓
等待明确批准
↓
部署
```

未经用户批准，不得修改当前运行版本。

---

## 2.4 Deployment 必须绑定 Commit SHA

假设：

```text
当前线上：
A

检测到：
B
```

系统通知：

```text
New version detected: B
```

用户批准：

```text
Deploy B
```

则部署时必须：

```text
git checkout B
```

或：

```text
git reset --hard B
```

禁止重新读取：

```text
latest origin/main
```

并直接部署最新版本。

原因：

如果审批期间出现：

```text
B
↓
C
```

用户批准的是 B，系统必须部署 B。

---

# 3. Overall Architecture

```text
┌────────────────────────────┐
│          MacBook           │
│                            │
│ Coding                     │
│ Local Test                 │
│ Commit                     │
│ Push                       │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│     GitHub Public Repo     │
│                            │
│ main                       │
│ Source of Truth            │
└─────────────┬──────────────┘
              │
              │ Read Only
              ▼
┌────────────────────────────┐
│          Mac mini          │
│                            │
│ Codex Automation           │
│        ↓                   │
│ Repository Watcher         │
│        ↓                   │
│ Approval Gate              │
│        ↓                   │
│ Deployment Manager         │
│        ↓                   │
│ Service Manager            │
│        ↓                   │
│ Health Check               │
└────────────────────────────┘
```

---

# 4. Components

系统拆分成五部分。

## 4.1 Repository Watcher

负责：

```text
GitHub main
↓
读取 remote HEAD
↓
与本地 deployed SHA 比较
```

推荐实现：

```bash
git fetch origin main
git rev-parse origin/main
```

返回：

```text
REMOTE_SHA
```

读取本地：

```text
LAST_DEPLOYED_SHA
```

如果：

```text
REMOTE_SHA == LAST_DEPLOYED_SHA
```

结束任务。

如果：

```text
REMOTE_SHA != LAST_DEPLOYED_SHA
```

进入 Change Analysis。

---

# 5. Local State

不要用当前 Git working tree 的 HEAD 作为生产状态的唯一依据。

建立：

```text
.state/
```

目录。

建议结构：

```text
.state/
├── deployed_sha
├── last_notified_sha
├── previous_sha
├── deployment_status.json
└── deployment.lock
```

---

## deployed_sha

记录：

```text
当前成功运行的 commit SHA
```

例如：

```text
29f6d86...
```

---

## last_notified_sha

记录：

```text
最近已经提醒用户的 commit
```

防止每天重复通知同一个版本。

---

## previous_sha

记录上一个成功版本。

用于 rollback。

---

# 6. Scheduled Check

使用 Codex Automation 创建 recurring task。

默认：

```text
每天检查一次
```

建议时间：

```text
09:00 Asia/Singapore
```

具体时间应可配置。

Automation 每次运行：

```text
1. cd repository
2. git fetch origin main
3. 获取 remote SHA
4. 获取 deployed SHA
5. 比较
6. 没更新 → 结束
7. 有更新 → 分析 changes
8. 通知用户
```

重要：

Scheduled Check **只有读取权限和分析权限**。

它不应该自行触发 Deployment。

---

# 7. Change Analysis

发现：

```text
LAST_DEPLOYED_SHA
        ↓
REMOTE_SHA
```

后执行：

```bash
git log LAST_DEPLOYED_SHA..REMOTE_SHA
```

以及：

```bash
git diff --stat LAST_DEPLOYED_SHA..REMOTE_SHA
```

必要时：

```bash
git diff LAST_DEPLOYED_SHA..REMOTE_SHA
```

Codex 生成简洁摘要。

通知示例：

```text
New deployment available

Repository:
xxx

Current:
29f6d86

New:
d528f71

Commits:
3

Changes:
- 修复 Reddit comment parsing
- 新增 retry mechanism
- 修改 OpenAI API timeout
- 更新 requirements.txt

Files changed:
8

Risk:
Medium

Important:
requirements.txt changed
service restart required

Deploy d528f71?
```

---

# 8. Approval Gate

系统默认：

```text
APPROVAL_REQUIRED=true
```

部署只能通过明确用户指令触发。

接受：

```text
Deploy
Deploy d528f71
批准部署
部署这个版本
```

Codex 必须把自然语言解析成：

```text
approved_sha=d528f71
```

然后再次进行验证。

---

# 9. Pre-Deployment Validation

实际部署前必须检查：

### 9.1 SHA 存在

```bash
git cat-file -e "$SHA^{commit}"
```

---

### 9.2 SHA 属于 origin/main

验证：

```bash
git merge-base --is-ancestor "$SHA" origin/main
```

如果失败：

```text
ABORT
```

---

### 9.3 Working Tree

执行：

```bash
git status --porcelain
```

预期：

```text
empty
```

如果发现本地修改：

```text
ABORT
```

不要：

```text
git stash
```

不要覆盖未知文件。

需要通知用户。

---

### 9.4 Deployment Lock

必须防止两个 deployment 同时发生。

例如：

```text
.state/deployment.lock
```

Deployment 开始：

```text
acquire lock
```

完成或失败：

```text
release lock
```

---

# 10. Deployment Process

部署某个：

```text
APPROVED_SHA
```

完整流程：

```text
① Acquire deployment lock

② Save current deployed SHA

③ git fetch

④ verify APPROVED_SHA

⑤ checkout / reset 到 APPROVED_SHA

⑥ install dependencies

⑦ build

⑧ restart service

⑨ health check

⑩ success → 更新 deployed_sha

⑪ failure → rollback

⑫ release lock
```

---

# 11. Git Update Strategy

推荐：

```bash
git fetch origin

git reset --hard "$APPROVED_SHA"
```

Repository 不作为开发目录使用，因此不需要：

```bash
git pull
```

`git fetch + reset` 可以明确控制具体部署版本。

---

# 12. Dependency Installation

根据项目自动识别。

例如 Python：

优先支持：

```text
uv.lock
pyproject.toml
requirements.txt
```

推荐：

```bash
uv sync --frozen
```

如果使用 requirements：

```bash
pip install -r requirements.txt
```

Node：

```bash
npm ci
```

必须优先使用 lock file。

---

# 13. Service Management

Mac mini 的长期进程不得直接依赖：

```bash
python main.py &
```

推荐使用：

```text
launchd
```

管理。

示例 service：

```text
com.helen.project-name
```

部署完成：

```bash
launchctl kickstart -k \
gui/$(id -u)/com.helen.project-name
```

目标：

* Mac mini reboot 后自动运行
* process crash 后自动恢复
* deployment 后可以统一 restart

---

# 14. Health Check

Restart 后必须验证服务实际成功运行。

支持三类 Health Check。

## Process

确认 PID 存在。

---

## HTTP

如果项目提供 API：

```text
GET http://localhost:<PORT>/health
```

预期：

```json
{
  "status": "ok"
}
```

---

## Custom Command

允许项目配置：

```text
HEALTH_CHECK_COMMAND
```

例如：

```bash
python scripts/health_check.py
```

---

# 15. Health Check Retry

建议：

```text
最大等待：60 秒
检查间隔：5 秒
```

逻辑：

```text
restart
↓
5s
↓
health check
↓
失败
↓
retry
```

直到：

```text
SUCCESS
```

或者：

```text
TIMEOUT
```

---

# 16. Successful Deployment

Health Check 成功后：

```text
previous_sha = old deployed_sha

deployed_sha = APPROVED_SHA
```

保存：

```json
{
  "status": "success",
  "sha": "d528f71",
  "previous_sha": "29f6d86",
  "deployed_at": "...",
  "duration_seconds": 18
}
```

然后通知：

```text
Deployment successful

29f6d86
↓
d528f71

Service:
running

Health:
healthy

Duration:
18s
```

---

# 17. Deployment Failure

任何阶段失败：

```text
install
build
restart
health check
```

都必须：

```text
deployment = FAILED
```

保存完整错误日志。

---

# 18. Rollback

如果：

```text
代码已经切换
```

并且：

```text
build / restart / health check
```

失败：

自动 rollback：

```bash
git reset --hard "$PREVIOUS_SHA"
```

重新：

```text
install dependencies
restart
health check
```

如果 rollback 成功：

通知：

```text
Deployment failed.

Target:
d528f71

Failure:
Health check timeout

Rolled back:
29f6d86

Current service:
healthy
```

如果 rollback 也失败：

通知必须明确标记：

```text
CRITICAL DEPLOYMENT FAILURE
```

不得修改：

```text
deployed_sha
```

---

# 19. Logging

目录：

```text
logs/
├── watcher.log
├── deployment.log
└── service/
```

Deployment log 至少保存：

```text
timestamp
repository
old_sha
target_sha
deployment stage
command
exit code
duration
health status
rollback status
```

---

# 20. Secrets

以下内容禁止提交 GitHub：

```text
.env
API keys
tokens
database credentials
private certificates
SSH private keys
production data
```

Production secrets 只保存在 Mac mini。

建议：

```text
~/apps/<project>/config/.env
```

或：

```text
macOS Keychain
```

Repository：

```text
.gitignore
```

必须覆盖相应文件。

---

# 21. Directory Structure

建议：

```text
~/apps/<project>/
│
├── repo/
│
├── config/
│   └── .env
│
├── state/
│   ├── deployed_sha
│   ├── previous_sha
│   ├── last_notified_sha
│   └── deployment_status.json
│
├── logs/
│   ├── watcher.log
│   └── deployment.log
│
└── scripts/
    ├── check_update.sh
    ├── analyze_update.sh
    ├── deploy.sh
    ├── rollback.sh
    └── health_check.sh
```

---

# 22. Configuration

建立：

```text
deployment.yaml
```

示例：

```yaml
repository:
  url: https://github.com/OWNER/REPO.git
  branch: main

schedule:
  timezone: Asia/Singapore
  check_interval: daily

deployment:
  approval_required: true

service:
  manager: launchd
  name: com.helen.project-name

health_check:
  type: command
  command: "./scripts/health_check.sh"
  timeout_seconds: 60
  interval_seconds: 5

rollback:
  enabled: true
```

---

# 23. Security Requirements

因为 Repository 为 Public Repository：

Mac mini 不应执行：

```text
pull_request
pull_request_target
fork
feature branch
```

来源代码。

Deployment source 永远只能是：

```text
origin/main
```

并且：

```text
user explicitly approved commit SHA
```

---

# 24. Public Repo Threat Model

需要考虑：

```text
External Contributor
↓
Fork
↓
PR
```

该 PR：

```text
不得直接进入 Mac mini
```

完整安全路径：

```text
External PR
↓
GitHub
↓
Repository Owner Review
↓
Merge Main
↓
Mac mini detects update
↓
Owner deploy approval
↓
Mac mini deploy
```

因此形成两层人工 Gate：

```text
Gate 1:
是否 merge

Gate 2:
是否 deploy
```

---

# 25. MVP

第一阶段只需要实现：

### Script 1

```text
check_update.sh
```

功能：

```text
读取 origin/main SHA
对比 deployed_sha
返回是否有新版本
```

---

### Script 2

```text
deploy.sh <SHA>
```

功能：

```text
验证 SHA
切换版本
安装依赖
restart
health check
记录 deployed_sha
```

---

### Script 3

```text
rollback.sh
```

功能：

```text
恢复 previous_sha
restart
health check
```

---

### Codex Automation

每天：

```text
运行 check_update
↓
如果 unchanged
→ 不通知

如果 changed
→ 分析 commit
→ 通知用户
→ 等待批准
```

---

# 26. Codex Automation Prompt

建议建立长期 Codex automation：

```text
You are the deployment manager for this Mac mini.

Repository:
<REPOSITORY_PATH>

Your responsibilities:

1. Check origin/main for updates.
2. Compare origin/main HEAD against the SHA stored in:
   state/deployed_sha
3. If they are identical, take no action.
4. If there is a new commit:
   - inspect commits and diff between the deployed SHA and remote SHA;
   - summarize the changes;
   - identify dependency/configuration/service changes;
   - estimate deployment risk;
   - notify me of the exact target commit SHA.
5. NEVER deploy a new version automatically.
6. Wait for my explicit approval.
7. Approval always applies to one exact commit SHA.
8. After approval, call:
   scripts/deploy.sh <approved_sha>
9. Report deployment result, health-check result and rollback status.
10. Never execute code originating from pull requests, forks or branches other than origin/main.
11. Never expose secrets from the Mac mini.
12. If anything is ambiguous or unsafe, abort deployment and report the reason.
```

---

# 27. Notification Format

检测到更新时：

```text
Repository Update Available

Current production:
29f6d86

Available:
d528f71

Commits:
3

Summary:
- xxx
- xxx
- xxx

Dependency changes:
Yes / No

Configuration changes:
Yes / No

Risk:
Low / Medium / High

Suggested action:
Deploy / Review manually

Awaiting approval:
d528f71
```

---

# 28. Deployment Confirmation Format

用户批准：

```text
Deploy d528f71
```

Codex 回复并执行：

```text
Deploying approved version:
d528f71
```

完成：

```text
Deployment successful

Version:
29f6d86 → d528f71

Build:
passed

Service restart:
passed

Health check:
passed
```

---

# 29. Non-Goals

MVP 暂时不要实现：

```text
Kubernetes
Jenkins
ArgoCD
Docker Registry
complex CI/CD server
public webhook
automatic PR deployment
automatic main deployment
multi-node deployment
```

保持系统轻量。

---

# 30. Future Enhancements

未来可以增加：

## A. Faster monitoring

从：

```text
daily
```

改成：

```text
hourly
```

---

## B. Multiple repositories

配置：

```yaml
projects:
  - project-a
  - project-b
  - project-c
```

由一个 Deployment Manager 管理多个项目。

---

## C. Deployment dashboard

显示：

```text
Project
Running Version
Latest Version
Last Deployment
Status
Health
```

---

## D. Automatic deployment

未来对：

```text
low-risk commits
```

可以选择：

```text
auto deploy
```

但 MVP 保持：

```text
manual approval
```

---

## E. Test before deployment

增加：

```text
checkout SHA
↓
test
↓
build
↓
deploy
```

只有 test 通过才部署。

---

# 31. Acceptance Criteria

MVP 完成标准：

### AC1

MacBook push 新 commit 到 `main` 后：

Mac mini 下一次定时检查可以识别新 commit。

### AC2

没有更新：

不得产生 deployment。

### AC3

有更新：

必须通知用户。

### AC4

未经用户批准：

不得修改 production working tree。

### AC5

批准 SHA A：

必须部署 SHA A。

不得自动替换成更新的 SHA B。

### AC6

部署成功：

必须更新：

```text
state/deployed_sha
```

### AC7

服务 restart 后必须执行 health check。

### AC8

health check 失败：

必须 rollback 到 previous SHA。

### AC9

Public PR 不能触发 Mac mini 执行任何 PR code。

### AC10

Mac mini reboot 后：

production service 可以自动恢复运行。

### AC11

所有关键 deployment 操作都有日志。

### AC12

API key / secrets 不得写入 Git repository 或 deployment logs。

---

# 32. Implementation Priority

Codex 请按照以下顺序开发：

```text
Phase 1

repository structure
↓
state management
↓
check_update.sh


Phase 2

deploy.sh
↓
service restart
↓
health check


Phase 3

rollback
↓
deployment logging


Phase 4

Codex recurring automation
↓
notification
↓
manual approval workflow


Phase 5

end-to-end test
```

---

# 33. End-to-End Test

最终必须验证完整流程：

```text
MacBook
↓
修改代码
↓
push main
↓
Mac mini scheduled check
↓
发现新 SHA
↓
Codex 通知用户
↓
用户批准 SHA
↓
deploy
↓
restart
↓
health check
↓
success
```

然后测试失败路径：

```text
push broken version
↓
approve
↓
deploy
↓
health check failure
↓
rollback
↓
old version restored
↓
service healthy
```

两条路径均通过后，MVP 才视为完成。

---

# 34. Final Desired User Experience

日常情况下，用户只需要在 MacBook：

```bash
git add .
git commit -m "..."
git push
```

之后不需要操作 Mac mini。

出现可部署版本时：

```text
Codex:
New version d528f71 available.
Deploy?
```

用户：

```text
Deploy
```

之后所有：

```text
pull/fetch
dependency installation
build
restart
health check
logging
rollback
```

均由 Mac mini 自动完成。

这就是该系统最终需要实现的体验。
