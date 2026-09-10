# Mac mini 安装与日常操作

## 1. 在 MacBook 上准备 GitHub 仓库

把本工具目录提交到你自己的 GitHub 仓库。业务项目可以是另一个公开仓库；配置里的 repository.url 指向**业务仓库**，而不是默认指向本工具。

上传前检查 git diff --cached，确认没有本机配置、密钥或运行数据。工具已经提供 .gitignore；Git 不会自动停止跟踪之前误提交的文件。

## 2. Mac mini 的一次性准备

需要 Git、Python 3.9+，以及业务项目需要的 uv / Node / npm。先在终端确认：

```bash
git --version
python3 --version
command -v python3
```

建议把工具放在稳定目录，不放在 Desktop、Documents 或 Downloads 等可能受 macOS 隐私权限影响的目录。下面的 OWNER/auto-deploy 是占位符，替换成你的工具仓库：

```bash
mkdir -p ~/tools
git clone https://github.com/OWNER/auto-deploy.git ~/tools/auto-deploy
cd ~/tools/auto-deploy
./scripts/setup.sh
.venv/bin/python -m unittest discover -s tests -v
mkdir -p ~/apps/my-project/config
cp deployment.example.yaml ~/apps/my-project/config/deployment.yaml
chmod 700 ~/apps/my-project/config
chmod 600 ~/apps/my-project/config/deployment.yaml
```

编辑 `~/apps/my-project/config/deployment.yaml`：

- root：例如 `~/apps/my-project`，不要和工具目录重叠。
- repository.url：业务项目的公开 GitHub HTTPS 地址；branch 固定 main。
- service.name：本机唯一 label，例如 com.helen.my-project。
- service.command：前台启动的 argv 数组，不是 shell 字符串，不能带 `&`。
- deployment.build_command：例如 `npm run build`，无构建则保持空字符串。
- health_check：按下面示例配置。
- schedule：默认每天 09:00 Asia/Singapore，确认你希望使用的时区和时间。
- path：Mac mini 本机可执行文件目录。Apple Silicon 常用 `/opt/homebrew/bin`，Intel 常用 `/usr/local/bin`；不要复制 MacBook 的虚拟环境。

Python/uv 项目可使用 `["python", "main.py"]`；工具为成功检出的 SHA 创建独立 venv，启动服务和构建时自动把该 venv 放在 PATH 前面。没有 Python 依赖文件的纯标准库项目可用 `["python3", "main.py"]`。Node 项目可用 `["node", "dist/server.js"]`。

`.env` 只在 Mac mini 上创建，权限必须为 600：

```bash
touch ~/apps/my-project/config/.env
chmod 600 ~/apps/my-project/config/.env
```

内容是单行 `KEY=value`，可以用成对单/双引号包裹值。不支持 `export`、变量替换、命令替换、多行值和行末注释；本工具不会 source 这个文件。服务将得到其中的环境变量，不会自动读取业务 repo 内的 .env。

## 3. 初始化（不部署）

```bash
export DEPLOY_CONFIG="$HOME/apps/my-project/config/deployment.yaml"
./scripts/bootstrap.sh
./scripts/check_update.sh
```

初始化创建空生产 repo 和独立 Git 缓存，只抓取 main，不启动业务代码。`check` 输出完整 target_sha。你可以反复执行检查，直到确认要部署哪个版本。

## 4. 安装系统级 launchd 服务

先生成文件供查看。此步骤不需要 sudo，也不改变系统服务：

```bash
.venv/bin/python scripts/install_service.py \
  --config "$DEPLOY_CONFIG" \
  --python "$PWD/.venv/bin/python" \
  --user "$(id -un)" \
  --output-dir /tmp/autodeploy-service-preview
```

确认 plist 的用户名、Python 路径和工具路径。然后执行一次安装：

```bash
sudo "$PWD/.venv/bin/python" scripts/install_service.py \
  --config "$DEPLOY_CONFIG" \
  --python "$PWD/.venv/bin/python" \
  --user "$(id -un)"
```

安装创建 `/Library/LaunchDaemons/<label>.plist` 及 `/etc/sudoers.d/autodeploy-<label中的点改为短横线>`，不会立即启动业务代码。授权仅包含这个 label 的 bootout、固定 plist 的 bootstrap、kickstart；业务进程以你的普通用户运行。以后 deploy 无需弹出管理员密码。

请勿用 sudo 运行 bootstrap/check/deploy，也不要手工 bootstrap 服务来跳过首次批准。安装器拒绝覆盖已有安装；修改 Python 路径或工具位置时需要先查看现有 plist，再由管理员明确更新。

## 5. 明确批准首次部署

从刚才 check 的报告复制完整 SHA，替换下面占位符：

```bash
./scripts/deploy.sh <完整SHA>
./scripts/autodeploy.sh --config "$DEPLOY_CONFIG" status
```

部署顺序是锁 → 验证 → 停服务 → checkout 指定 SHA → 安装 → 构建 → 启动 → 健康检查。首次启动成功后写入 deployed_sha。失败时不会冒充成功；首次部署没有可回滚版本。

## 6. 在 Mac mini 上启用 Codex 定时任务

在 Mac mini 的桌面应用中打开一个以本工具为上下文的本地任务，确保它能访问 `~/apps/my-project` 及 GitHub 网络。运行：

```bash
./scripts/autodeploy.sh --config "$DEPLOY_CONFIG" automation-prompt
```

把生成的文字粘贴到该 Mac mini 任务，让应用创建**回到同一任务的定时检查**。提示词已包含绝对工具/配置路径、时间、时区、通知去重、严格审批 SHA 和故障处理。检查任务所属机器与下一次触发时间，手动运行一次核对输出。此项目不会在 MacBook 上注册一个假装在 Mac mini 执行的任务。

配置文件 schedule 是创建任务时的输入；修改它不会自动修改已存在的桌面任务。修改后重新生成提示词，要求更新原任务，避免重复创建。

本地文件任务需要机器开机且桌面应用运行；这与 launchd 业务服务的开机恢复是两件事。[OpenAI 官方定时任务文档](https://learn.chatgpt.com/docs/automations?surface=app)

日常只需在 MacBook push main，然后在任务里回复对应更新通知：“部署这个版本”或“Deploy <SHA>”。定时触发本身绝不部署。若应用权限阻止执行，按实际提示授予所需访问；不能通过修改工具跳过批准。

## 健康检查示例

HTTP 服务建议使用接口检查，返回 HTTP 200 且 JSON 中 status 为 ok：

```json
"health_check": {
  "type": "http",
  "url": "http://127.0.0.1:8080/health",
  "timeout_seconds": 60,
  "interval_seconds": 5
}
```

无 HTTP 的任务可以提供业务级检查脚本：

```json
"health_check": {
  "type": "command",
  "command": "python scripts/health_check.py",
  "timeout_seconds": 60,
  "interval_seconds": 5
}
```

命令在业务 repo 中执行，退出码 0 代表健康。也支持模板中的 process：验证 launchd 包装器及业务子进程存在，但进程存在不等于业务可用，能提供接口或业务检查时应使用它们。

## 失败与恢复

- **本地修改**：查看 repo 的 git status，人工确认文件归属后处理。工具不会 stash 或覆盖。
- **锁占用**：等待正在运行的操作结束。OS 在进程退出时释放锁，不要删除锁文件。
- **部署失败、回滚 healthy**：旧版本已恢复，修复代码后重新推送并审批。
- **critical**：查看脱敏日志，不把 deployed_sha 当作当前健康证明。修复本机故障，明确运行 recover 恢复最近成功版本。
- **进程中断/掉电留下 deploying**：先检查日志，再明确运行 recover；首次部署没有旧版本时，重新批准原报告的完整 SHA 并运行 `recover <SHA>`。不会自动部署新的 main。
- **main 被 force-push**：已批准 SHA 不再是 main 的祖先会被拒绝。保留 main 历史，恢复正确分支后重新检查，不绕过来源验证。
- **Python/Node 构建产生文件**：业务仓库应通过 .gitignore 声明构建目录。若构建修改已跟踪文件，部署会失败，并可能需要人工修复后才能回滚。
- **开机恢复**：系统 LaunchDaemon 不要求用户登录；磁盘必须已解锁，目录及运行时必须可访问。Codex 定时检查另外要求桌面应用运行，按 Mac mini 的实际登录和电源设置验收。

## 移除服务

先停止服务，再由管理员删除上面明确列出的该服务 plist 与 sudoers 文件。保留应用目录中的配置、状态和日志，除非你另行决定删除。不要删其他服务的文件。
