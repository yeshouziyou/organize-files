# organize-files

[![CI](https://github.com/yeshouziyou/organize-files/actions/workflows/ci.yml/badge.svg)](https://github.com/yeshouziyou/organize-files/actions/workflows/ci.yml)

[English](README.md)

`organize-files` 是一个开放、带安全权限闸门的 [Agent Skill](https://agentskills.io/)，用于分类、重命名、移动、审计、清理和去重人工管理的文件。它复用一份文件清单和内容证据，通过声明式决策、执行前预览和有边界的验证降低重复工作。

## 兼容性

本仓库遵循开放的 Agent Skills 格式，不强绑定 Codex。任何能够读取本地文件、执行 Python 命令并支持 Agent Skills 的 Agent 都可以使用核心工作流。`agents/` 下的客户端专用元数据是可选增强，其他客户端可以忽略。

运行要求：

- Python 3.10 或更高版本。
- Windows、macOS 或 Linux。
- Agent 拥有本地文件读取和命令执行能力。

可查看官方[兼容客户端列表](https://agentskills.io/clients)。不支持自动发现 Skill 的 Agent 仍可被要求直接阅读 `SKILL.md`，但能否自动触发取决于客户端。

仓库附带的 OpenAI/Codex 元数据要求用户显式调用 `$organize-files`，避免普通的文件整理闲聊意外启动可修改文件的工作流。其他客户端由各自设置控制隐式触发；通用 `SKILL.md` 不绑定特定客户端。

## 安装

推荐把仓库安装或克隆到：

```text
~/.agents/skills/organize-files/
```

`.agents/skills/` 是跨客户端通用位置；不同客户端也可能支持自己的原生 Skill 目录。

## 中英文支持

仓库只维护一份权威 `SKILL.md`，并使用中英文触发描述。工作流支持中文、英文及中英混合的文件名和正文，默认使用用户当前语言回复。专有名词、正式编号、缩写和有意保留的大小写不会为了统一语言而被翻译或强制改写。

公开默认策略使用 `"language": "auto"`。本地配置可以指定 `zh-CN` 或 `en`，不会改变分类、取证或审计范围。

## 本地策略

公开默认策略只生成预览。本地配置按以下顺序发现：

1. `ORGANIZE_FILES_CONFIG` 环境变量。
2. `~/.config/organize-files/config.json`。
3. `~/.agents/organize-files.local.json`。
4. 为兼容现有安装而保留的 `~/.codex/organize-files.local.json`。

显式传入的 `--local-config` 优先级最高。保留旧 Codex 路径意味着现有本地配置不需要迁移，行为不会变化。

在 macOS 和其他非 Windows 系统中，即使误选了 Windows 清理 preset，`.DS_Store`、AppleDouble `._*` 和 Office 临时锁文件也会被强制降级为只预览。普通文件和重复文件删除始终需要单独确认。

## 性能模型

- 运行平台判断只进行一次常量时间的系统调用。
- 配置发现最多检查几个固定路径，不递归扫描目录。
- 文件扫描仍然是仅元数据的一次执行前递归枚举。
- 内容证据只获取一次，之后复用于分类、命名、审计和验证。
- 默认不计算哈希；只有明确检查重复文件时，才对冻结清单中的同尺寸候选计算哈希。
- 批准后移动前复核大小和修改时间，不计算整文件哈希。

跨 Agent 和双语改造不会新增全目录扫描或正文提取轮次。

## 安全边界

- 公开默认只预览。
- 改名和移动不会覆盖已有目标。
- 执行计划绑定批准时的文件大小和修改时间。
- 生成元数据清理必须使用冻结清单。
- 重复文件删除使用独立确认计划，并重新核对完整哈希。
- 重复文件删除不可回滚；中途失败会明确报告已删除和未删除路径，不会声称整批回滚。
- 不跨越链接、junction、挂载点和用户选定的根目录边界。

## 开发验证

运行测试：

```text
python -m unittest discover -s tests -v
```

运行公开发布审计：

```text
python scripts/check-public-release.py .
```

私有发布禁词表可以通过 `ORGANIZE_FILES_DENYLIST`、`--denylist`，或仓库外的 `~/.config/organize-files/private-denylist.json` 提供。

## License

采用 MIT License，详见 [LICENSE](LICENSE)。
