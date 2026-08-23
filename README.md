# Hermes-Exilium

[Hermes Agent](https://github.com/NousResearch/hermes-agent) 的个人薄 fork（thin fork）：在不长期偏离上游的前提下，修复官方仓库中久未修复的问题。

## 分支模型

| 分支 | 角色 |
|---|---|
| `main` | 上游 `NousResearch/hermes-agent` 的**纯镜像**，永不携带本 fork 的提交 |
| `custom` | **部署分支**：本 fork 的修复，每次更新时 rebase 到 `main` 上 |

`git log main..custom` 始终是一个干净、可审阅的修复序列（一修复一提交，每个修复附带回归测试）。

## 相对原项目的修改

### 更新机制

- **可配置的更新分支**（`44c578e3b`）：新增 `updates.branch` 配置项，可将默认更新目标从 `main` 改为任意分支（本 fork 使用 `custom`）。
- **薄 fork 更新工作流**（`9372a3e97`）：检测到上游前进时，`hermes update` 自动执行：同步 `main` 镜像到 `upstream/main` 并推送 → 将 `custom` rebase 到新 `main` → 运行本 fork 的回归测试（fork 新增的 `tests/` 文件）→ 测试通过才 force-push 两分支。rebase 冲突会干净中止（`custom` 不动），测试失败则阻断推送。
- **镜像推送 lease 修复**（`d40710e8c`）：修复 single-branch clone 下 `main` 镜像推送因 `--force-with-lease` 基准错误被永久拒绝的问题。

### 启动提示

- **三档更新提示**（`77a15cc63`）：新增 `updates.notify` 配置——
  - `release`（默认）：仅当官方发布新的日历版本 tag（`vYYYY.M.D`）时提示；
  - `commit`：官方 `main` 有新提交即提示；
  - `off`：不提示（YAML 中需加引号写作 `"off"`，裸词会被解析为布尔值）。

### Bug 修复

- **配置静默覆盖**（`f6aeb54bc`）：修复 tools/reconfigure 流程中"子流程写入的 vision / langfuse 配置被外层旧配置对象覆盖丢失"的问题。
- **ANSI 颜色渲染**（`71e70aa52`）：更新提示改经 prompt_toolkit 渲染器输出（修复被 `patch_stdout` 吞掉转义字符产生的 `?[33m` 乱码）；stderr 警告（配置问题、`.env` 弃用项、xAI 模型退役）改为条件上色——仅在 stderr 本身是 TTY 时输出颜色，重定向/日志不再泄漏裸转义序列。

### 安装

- **安装脚本指向本 fork**（`8bb867ea2`）：`install.sh` / `install.ps1` / `install.cmd` / bootstrap-installer 默认从本 fork 的 `custom` 分支安装，并在安装后自动写入 `updates.branch: custom`。

## 从官方安装迁移到本 fork

适用于已按官方方式（git 安装）安装的 Hermes。前提：工作树干净、无未推送提交。

```bash
cd <你的 hermes-agent checkout>   # 默认 ~/.hermes/hermes-agent

# 1. origin 指向本 fork，官方仓库改为 upstream
git remote set-url origin https://github.com/UMP40/Hermes-Exilium.git
git remote add upstream https://github.com/NousResearch/hermes-agent.git

# 2. 官方安装是 single-branch main clone，需改为跟踪 custom
git remote set-branches origin custom
git fetch origin custom

# 3. 切到部署分支
git checkout -B custom origin/custom

# 4. 依赖如有变化则同步
pip install -e '.[all]'

# 5. 更新目标固定为 custom
hermes config set updates.branch custom

# 6. 如有常驻 gateway，重启以加载新代码
hermes gateway restart
```

验证：`hermes update --check` 应显示 `→ Fetching from upstream...`（薄 fork 模式生效）。

## 本 fork 发布新更改后如何更新

统一入口是 `hermes update`，但行为取决于发布形态：

**形态 A — fork 已 rebase 到最新上游后发布**（上游有新提交，维护者跑过 `hermes update` 全流程）：

下游直接运行 `hermes update`。它会 fetch upstream、发现落后、执行镜像 + rebase + 测试 + 推送，本机代码随之更新。

**形态 B — 维护者直接 push 了 `custom`（上游未前进）**：

此时 `hermes update` 对 `upstream/main` 计数为 0，走"仅同步镜像"路径，**不会拉取 `custom` 的新提交**。需要手动快进：

```bash
git fetch origin custom
git merge --ff-only origin/custom
```

（如维护者 rebase 过历史则不是 fast-forward，此时等下一次形态 A 更新，或手动 `git reset --hard origin/custom`。）

日常依赖启动提示即可：`updates.notify: release` 模式下，官方发布新版本 tag 时启动横幅会提示执行 `hermes update`。
