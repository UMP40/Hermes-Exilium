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

- **可配置的更新分支**：新增 `updates.branch` 配置项，可将默认更新目标从 `main` 改为任意分支（本 fork 使用 `custom`）。
- **薄 fork 更新工作流**：检测到上游前进时，`hermes update` 自动执行：同步 `main` 镜像到 `upstream/main` 并推送 → 将 `custom` rebase 到新 `main` → 运行本 fork 的回归测试（fork 新增的 `tests/` 文件）→ 测试通过才 force-push 两分支。rebase 冲突会干净中止（`custom` 不动），测试失败则阻断推送。
- **镜像推送 lease 修复**：修复 single-branch clone 下 `main` 镜像推送因 `--force-with-lease` 基准错误被永久拒绝的问题。

### 启动提示

- **三档更新提示**：新增 `updates.notify` 配置——
  - `release`（默认）：仅当官方发布新的日历版本 tag（`vYYYY.M.D`）时提示；
  - `commit`：官方 `main` 有新提交即提示；
  - `off`：不提示（YAML 中需加引号写作 `"off"`，裸词会被解析为布尔值）。

### Bug 修复

- **配置静默覆盖**：修复 tools/reconfigure 流程中"子流程写入的 vision / langfuse 配置被外层旧配置对象覆盖丢失"的问题。
- **ANSI 颜色渲染**：更新提示改经 prompt_toolkit 渲染器输出（修复被 `patch_stdout` 吞掉转义字符产生的 `?[33m` 乱码）；stderr 警告（配置问题、`.env` 弃用项、xAI 模型退役）改为条件上色——仅在 stderr 本身是 TTY 时输出颜色，重定向/日志不再泄漏裸转义序列。
- **会话归档 CLI 单向门**：`hermes sessions archive` 原本只能归档、无法列出或恢复，归档会话对 CLI 用户不可达。新增 `list`/`browse` 的 `--archived`（仅归档，含 archived+hidden 以保证恢复入口）与 `--all`（归档与活跃并列表），以及 `unarchive <id-or-prefix>`（压缩链整体翻转恢复）；`browse` 归档行标记 `arch`；并修正 session-librarian 技能文档对"列出归档会话"的引用（原误用 prune 专属的 `--include-archived`，改为 `hermes sessions list --archived`）。
- **会话 ID 前缀说明补全**：`sessions delete`/`rename` 的帮助文本补注"接受唯一 ID 前缀"，与原有代码实现保持一致，现与 `unarchive`/`pin`/`unpin`/`export` 的既有说明格式相同。

### 安装

- **安装脚本指向本 fork**：install.sh / install.ps1 / install.cmd / bootstrap-installer 默认从本 fork 的 `custom` 分支安装，并在安装后自动写入 `updates.branch: custom`。

## 从官方安装迁移到本 fork

适用于已按官方方式（git 安装）安装的 Hermes。前提：工作树干净、无未推送提交。

```bash
cd <你的 hermes-agent checkout>   # POSIX 默认 ~/.hermes/hermes-agent；Windows 默认 %LOCALAPPDATA%\hermes\hermes-agent

# 1. origin 指向本 fork，官方仓库改为 upstream
git remote set-url origin https://github.com/UMP40/Hermes-Exilium.git
git remote add upstream https://github.com/NousResearch/hermes-agent.git

# 2. 官方安装是 single-branch main clone，需改为跟踪 custom
git remote set-branches origin custom
git fetch origin custom

# 3. 切到部署分支
git checkout -B custom origin/custom

# 4. 依赖如有变化则同步（Windows venv 内可用 .\venv\Scripts\pip.exe）
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

（以上两条命令在 PowerShell 下完全一致。如维护者 rebase 过历史则不是 fast-forward，此时等下一次形态 A 更新，或手动 `git reset --hard origin/custom`。）


日常依赖启动提示即可：`updates.notify: release` 模式下，官方发布新版本 tag 时启动横幅会提示执行 `hermes update`。

## 下游更新遇冲突中断后的恢复

`hermes update` 在薄 fork 模式下 rebase 失败（与上游冲突）时会**干净中止**：自动 `git rebase --abort`、本地 `custom` 保持原基线不动、不推送任何内容，仅 `main` 镜像被推进到新上游（无害，设计如此）。此后每次重跑 `hermes update` 都会撞同一个冲突——不会自愈，必须等维护者解决并发布新 `custom` 后，在下游执行一次性对齐：

**维护者侧（解决冲突并发布，通常已完成）**：在开发机上 `git rebase main` 手动解决冲突 → 跑 fork 回归测试 → `git push --force-with-lease origin custom`。

**下游侧（对齐被重写的历史）**：

```bash
cd <hermes-agent checkout>   # POSIX 默认 ~/.hermes/hermes-agent；Windows 默认 $env:LOCALAPPDATA\hermes\hermes-agent

# 1. 确认状态干净、无残留 rebase（中止契约正常时本就干净）
git rebase --abort 2>/dev/null; git status --short   # 应为空

# 2. 本地有未推送提交则先停下检查；正常下游应无输出
git log origin/custom..custom --oneline

# 3. 对齐 main 镜像（保持仓库结构规整，可选）
git fetch upstream main && git branch -f main upstream/main

# 4. 拉取重写后的 custom 并硬重置（rebase 重写了历史，无 fast-forward 关系）
git fetch origin custom
git reset --hard origin/custom

# 5. 依赖同步（上游跨度大时必须）
pip install -e '.[all]'

# 6. 重启常驻进程
hermes gateway restart
```

**PowerShell（Windows）等价命令**——与 POSIX 的实际差异仅两处：stderr 丢弃写法（`2>$null`）与安装目录；`git branch -f` 保守拆成两条以便 PowerShell 5.1 也能直接运行（pwsh 7+ 可用 `&&` 合并）：

```powershell
cd <hermes-agent checkout>   # Windows 默认 $env:LOCALAPPDATA\hermes\hermes-agent

# 1. 确认状态干净（PowerShell 丢弃 stderr 用 2>$null，不是 2>/dev/null）
git rebase --abort 2>$null; git status --short

# 2. 检查未推送提交（与 POSIX 相同）
git log origin/custom..custom --oneline

# 3. 对齐 main 镜像（拆成两条以兼容 PowerShell 5.1；pwsh 7+ 可用 && 连接）
git fetch upstream main
git branch -f main upstream/main

# 4. 重置（与 POSIX 相同）
git fetch origin custom
git reset --hard origin/custom

# 5. 依赖同步（与 POSIX 相同；venv 内用 .\venv\Scripts\pip.exe）
pip install -e ".[all]"

# 6. 重启常驻进程（与 POSIX 相同）
hermes gateway restart
```

验证：

```bash
hermes --version        # local hash 应与 origin/custom 一致
hermes update --check   # → Fetching from upstream... ✓ Already up to date.
```

对齐后即恢复正常流程：下次上游前进时 `hermes update` 自动完成 rebase + 测试门 + 推送。多机部署时哪台先跑 update 哪台推送，后跑的机器会遇到 custom 被别处推进的情况——届时同样 `git fetch origin custom && git reset --hard origin/custom` 一次性对齐即可。
