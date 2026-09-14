# auto-deploy · Mac 服务受控部署

给运行在 Mac mini 或其他长期在线 Mac 上的个人服务使用的轻量部署工具。

**检查 GitHub main → 汇总变更 → 人工批准一个完整 SHA → 安装/构建 → 重启 → 健康检查 → 成功记录或自动回滚。**

适合个人机器人、小型 API 和由 launchd 管理的常驻任务。开发可以在另一台 Mac 或其他电脑完成；实际服务管理在 macOS 上运行。

## 支持范围

| 项目 | 当前支持 |
|---|---|
| 部署机器 | macOS，使用系统级 launchd；不直接支持 Linux/Windows |
| 代码来源 | 配置指定的公开 GitHub 仓库，仅 main |
| 项目数量 | 每份配置管理一个业务项目；没有统一多项目调度器 |
| 安装依赖 | Python：uv.lock / requirements.txt / pyproject.toml；Node：package-lock.json |
| 构建 | 本机配置 build_command；没有构建需求时留空 |
| 健康检查 | 进程、HTTP、自定义命令 |
| 更新通知与自然语言审批 | 使用 Codex 本地定时任务；CLI 可独立手动使用 |
| 自动恢复 | 部署失败后恢复旧版本；中断或回滚失败需人工处理 |

工具仓库与业务仓库是两个概念：先安装本工具，再配置要部署的业务项目。工具不会自动扫描账号下的仓库，也不提供自身的自动更新。私有业务仓库认证不在当前支持范围内。

## 快速开始

部署工具需要 Python 3.9+、Git 和 macOS 自带工具。业务项目另外需要相应的 Python、uv 或 Node/npm。

在目标 Mac 上克隆并安装本工具（仓库仍为私有时需要 GitHub 访问权限）：

```bash
mkdir -p ~/tools
git clone https://github.com/helen-bird/auto-deploy.git ~/tools/auto-deploy
cd ~/tools/auto-deploy
./scripts/setup.sh
./scripts/autodeploy.sh --help
```

setup.sh 在工具目录的独立 .venv 中安装固定版本 PyYAML。不要从其他机器复制虚拟环境。

然后按照 **[安装指南](docs/mac-mini-setup.md)** 完成：

1. 复制 [配置模板](deployment.example.yaml)，填写业务仓库地址、应用目录、服务名称、启动命令及健康检查。
2. 在应用目录的 config/.env 中配置本机密钥，不提交到 Git。
3. 初始化目录并检查更新；这一步不会部署或运行业务代码。
4. 查看并安装 launchd 配置，明确批准首次部署的完整 SHA。
5. 如需通知和自然语言审批，再配置 Codex 定时任务。

不使用 Codex 时也可以手动运行 check，查看输出后调用 deploy。检查脚本不会自己发通知或等待输入；配置中的 schedule 也不会单独安装一个系统定时器。完整定时检查、摘要和通知由 Codex 任务提供。

`deployment.yaml` 支持常规 YAML 和 JSON，采用安全解析器。调度模板默认每天 09:00 Asia/Singapore，可在本机配置；修改配置后还需更新已经创建的定时任务。

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

兼容 shell 入口包括 `bootstrap.sh`、`check_update.sh`、`analyze_update.sh`、`deploy.sh <SHA>`、`rollback.sh` 和 `health_check.sh` 入口；这些入口读取 `DEPLOY_CONFIG`，未设置时读取当前目录的 `deployment.yaml`。

`deploy <SHA>` 命令本身代表操作者的明确批准，必须由你或接到你明确指令的 Codex 调用。脚本要求这个完整 SHA 已经被 check 检测到，不会把 SHA 换成最新 main。自然语言授权由 Codex 任务处理，CLI 不会自行推断人的批准。

`check` 返回 JSON：unchanged / already_notified / changed / needs_attention。不会退出等待输入。变更摘要交给 Codex 生成。通知展示后由 `notified <SHA>` 记账；单纯执行 check 不会吞掉还没送达的通知。

## 目录与状态

```text
~/tools/auto-deploy/           # 部署工具，与业务工作树分离
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

## 安全边界

详细攻击面、已修复问题及剩余风险见 [安全审查](docs/security-review.md)。

本工具用于部署自己信任的代码，不是代码执行沙箱，也不提供网络隔离。安装脚本、依赖构建和服务均具有部署用户的文件及网络权限；安装阶段不传递 `.env` 不代表无法读取该用户有权访问的文件。建议使用专用普通用户，不在该用户下保存其他业务的敏感资料。公开仓库不等于可信仓库，批准前仍应审查代码及依赖变化。

单条安装、构建或 Git 命令的 stdout/stderr 合计最多 8 MiB，超过即终止该命令进程组并报告失败；部署阶段按原有规则尝试回滚。超限及超时的部分输出不写入日志，避免截断秘密造成泄漏。较大的单条日志会以省略标记替代。watcher.log、deployment.log 与 service.log 均按约 5 MB 轮转，保留当前文件和 3 份备份。服务单行输出超过 1 MiB 时停止该子进程并记载原因，launchd 可能按现有策略重启它。

HTTP 健康检查只接受回环地址，禁用代理和重定向，响应最多 64 KiB，要求返回 JSON 对象且 status 为 ok。进程存活或健康响应不能证明业务代码没有恶意行为，也不能替代应用自身的鉴权和漏洞修复。

仓库中的提交信息及文件内容均是不可信数据，不能代替部署批准。本地配置、状态目录、工具源码及部署用户账号属于信任边界；拥有该用户权限的攻击者可直接调用 CLI 或篡改这些文件。不要把该工具作为多租户、运行不可信代码或对外接收部署指令的安全网关。

## 验证状态与开发

维护者已反馈在 Mac mini 完成实机验收、运行无异常。该反馈与自动化测试记录分开保存：没有把未提供的机器配置、日志或逐项结果推定为已核验。其他使用者仍应按 [验收清单](docs/acceptance.md) 验证自己的项目。

2026-09-10 的开发验证记录为 26 项测试通过，覆盖真实临时 Git 仓库、本机 HTTP 健康检查、子进程、失败回滚与中断恢复。自动测试中的 launchd 管理使用测试适配器，不会注册系统服务。

```bash
./scripts/setup.sh
.venv/bin/python -m unittest discover -s tests -v
```

测试会临时监听本机回环地址；受限沙箱可能需要允许本地监听。部署默认不运行项目测试：需要部署前测试时，可在项目自己的构建步骤中配置。

[TRD](TRD.md) 是原始设计记录，保留历史示例；实际安装以本 README、配置模板和安装指南为准。

## 贡献与许可证

欢迎提交问题和改进。提交问题时请说明 macOS、Python 版本、失败阶段及脱敏错误信息；不要附带真实 .env、访问令牌或完整生产日志。修改审批、来源验证、回滚或状态管理时，请同时提供相应回归测试。

本项目采用 [MIT License](LICENSE)，版权署名为 helen-bird。外部依赖仍遵循各自许可证；PyYAML 由安装脚本下载，不打包进本仓库。
