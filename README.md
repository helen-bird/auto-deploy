# Mac mini 受控部署工具

在 MacBook 开发，通过 GitHub 同步，在 Mac mini 上检查和部署业务项目。

**发现更新 → 通知 → 用户批准完整 SHA → 安装/构建 → 重启 → 健康检查 → 成功记录或自动回滚。**

当前实现单个公开 GitHub 仓库、仅 `main`、强制人工批准、launchd 长期服务。部署工具和业务项目各有一个独立 checkout。不会给 MacBook 安装常驻服务，也不会替你上传 GitHub。

## 快速开始

MacBook 上可以直接运行开发验证：

```bash
./scripts/setup.sh
.venv/bin/python -m unittest discover -s tests -v
./scripts/autodeploy.sh --help
```

部署工具需要 Python 3.9+、Git 和 macOS 自带工具；setup.sh 会在工具目录的独立 .venv 中安装固定版本 PyYAML，用于安全读取 YAML 配置。业务项目根据需要使用 uv、Python 或 Node/npm。

Mac mini 安装流程见 **[安装指南](docs/mac-mini-setup.md)**。复制 [配置模板](deployment.example.yaml)，填业务仓库地址、应用目录、启动命令和健康检查，初始化后安装 launchd 服务。最后在 Mac mini 的 Codex 本地任务中启用定时检查。

`deployment.yaml` 支持常规 YAML 和 JSON，使用安全解析器，不执行 YAML 中的对象构造指令。调度默认保持 TRD 的每天 09:00 Asia/Singapore，可配置。

## 命令

完整 CLI 支持显式配置路径：

```bash
./scripts/autodeploy.sh --config /absolute/path/deployment.yaml init
./scripts/autodeploy.sh --config /absolute/path/deployment.yaml check
./scripts/autodeploy.sh --config /absolute/path/deployment.yaml deploy <完整SHA>
./scripts/autodeploy.sh --config /absolute/path/deployment.yaml status
./scripts/autodeploy.sh --config /absolute/path/deployment.yaml rollback
./scripts/autodeploy.sh --config /absolute/path/deployment.yaml recover
```

也保留 TRD 中的 `bootstrap.sh`、`check_update.sh`、`analyze_update.sh`、`deploy.sh <SHA>`、`rollback.sh` 和 `health_check.sh` 入口；这些入口读取 `DEPLOY_CONFIG`，未设置时读取当前目录的 `deployment.yaml`。

`deploy <SHA>` 命令本身代表操作者的明确批准，必须由你或接到你明确指令的 Codex 调用。脚本要求这个完整 SHA 已经被 check 检测到，不会把 SHA 换成最新 main。自然语言授权由 Codex 任务处理，CLI 不会自行推断人的批准。

`check` 返回 JSON：unchanged / already_notified / changed / needs_attention。不会退出等待输入。变更摘要交给 Codex 生成。通知展示后由 `notified <SHA>` 记账；单纯执行 check 不会吞掉还没送达的通知。

## 目录与状态

```text
~/tools/auto-deploy/           # 本工具，只在 MacBook 开发
~/apps/my-project/
  config/deployment.yaml      # Mac mini 本机配置
  config/.env                 # mode 600，只保存本机
  source.git/                 # 检查用 bare Git 缓存，只 fetch main
  repo/                       # 生产工作树，只有明确部署才 checkout
  venvs/<SHA>/                # Python 依赖按版本隔离
  state/
    deployment_status.json   # 原子写入的权威状态
    deployed_sha             # 最近成功版本的兼容镜像
    previous_sha
    last_notified_sha
    pending.json             # 检测 SHA 与当时部署基线
    deployment.lock          # OS 文件锁，不要删除
  logs/
    watcher.log
    deployment.log
    service.log              # 服务日志，5 MB × 最多 4 份
```

健康检查通过后才更新成功版本。部署/回滚结果、命令、退出码、输出、时间和阶段都保存在日志中。JSON journal 是权威状态，SHA 文本文件由它派生；中途掉电可能让文本镜像暂时滞后，不能据此猜测成功。

失败自动回到**本次部署开始前的成功版本**，重新安装依赖、构建、启动并验证。手动 rollback 回到 previous_sha。首次部署没有旧版本，失败时停止服务并明确报告无法回滚。回滚也失败时保留最近成功 SHA，但状态标为 critical，不能解释为该版本目前仍健康。

## 约束

- 发现本地已跟踪修改或未知文件就中止；不 stash、不 git clean、不强制覆盖。checkout 也保护冲突的 ignored 文件。构建产物应在业务项目 `.gitignore` 中声明，或放在仓库外。
- Python 优先 `uv.lock` → `uv sync --frozen`；其次 requirements.txt → 独立 venv + pip；只有 pyproject.toml 时在独立 venv 中 pip install .。Node 用 package-lock.json → npm ci；只有 package.json 时明确报错，可在本机配置 install_command。requirements.txt 应固定版本；完整复现推荐锁文件。
- 混合 Python/Node 项目需要显式 install_command；自动识别选择一个依赖体系，不猜测多语言构建顺序。build_command 留空代表不需要构建；有构建步骤的项目必须填写。
- system LaunchDaemon 以指定普通用户运行，通过限定到该服务的 sudoers 规则完成管理。服务和部署工具都不是以 root 运行。安装器不启动业务代码。
- 安装/构建默认不接收 `.env`；服务与自定义健康检查才接收。本工具不继承终端里的其他环境变量。配置中的命令只应引用变量，不能内嵌密钥。
- 日志对 `.env` 中的值及其 JSON 转义形式脱敏，也过滤常见凭证字段。应用仍必须遵守“不输出秘密”的约定：任意程序对秘密进行编码、拆分、加密或从其他来源读取后输出，无法由通用过滤器保证识别。公开仓库本身不能包含秘密。
- 回滚恢复代码、依赖与构建，不逆转数据库迁移或外部副作用。此类项目必须提供可回滚的部署步骤，否则不能认为满足该项目的回滚验收。
- 程序被强制终止或机器掉电后不会自行把中断操作当成功；后续部署被阻止，需明确 recover。常规成功版本重启可由 launchd 恢复。

## 验证状态

本地测试覆盖真实 Git 仓库与实际 shell 命令，launchd 管理在自动测试中使用测试适配器，不会注册本机服务。详见 [验收清单](docs/acceptance.md)。Mac mini 的 GitHub 通知、真实 launchd、开机恢复、成功/失败端到端验证仍必须在目标机器上完成；完成前不宣称整套 MVP 已验收。

[TRD](TRD.md) 保留原始要求；没有把上述待验收项目从范围中删除。
