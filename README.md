# organize-files

[![CI](https://github.com/yeshouziyou/organize-files/actions/workflows/ci.yml/badge.svg)](https://github.com/yeshouziyou/organize-files/actions/workflows/ci.yml)

[中文](README.zh-CN.md)

`organize-files` is an open, safety-gated [Agent Skill](https://agentskills.io/) for classifying, renaming, moving, auditing, cleaning, and deduplicating human-managed files. It uses one inventory, reusable content evidence, declarative decisions, preview-first execution, and bounded verification.

## Compatibility

This repository follows the open Agent Skills format. It is not tied to Codex: any skills-compatible agent that can read local files and run Python commands can use the core workflow. Client-specific metadata under `agents/` is optional and may be ignored by other clients.

Requirements:

- Python 3.10 or newer.
- Windows, macOS, or Linux.
- An agent with local filesystem and command execution access.

See the official [client showcase](https://agentskills.io/clients) for compatible products. Agents without Agent Skills discovery may still be instructed to read `SKILL.md`, but automatic activation depends on the client.

The bundled OpenAI/Codex metadata requires explicit `$organize-files` invocation, preventing an ordinary conversation about tidying files from starting a mutating workflow. Other clients control implicit activation through their own settings; the portable `SKILL.md` remains client-neutral.

## Installation

Install or clone the repository as:

```text
~/.agents/skills/organize-files/
```

`.agents/skills/` is the portable cross-client location. A client may also support its own native skill directory.

## Languages

The repository uses one canonical `SKILL.md`, with bilingual discovery metadata. The workflow accepts Chinese, English, and mixed-language filenames and content, and responds in the user's language by default. It preserves proper nouns, official identifiers, acronyms, and intentional casing.

The public policy uses `"language": "auto"`. A local configuration may select `zh-CN` or `en` without changing the classification or evidence pipeline.

## Local policy

The public default is preview-only. Local configuration discovery uses this order:

1. `ORGANIZE_FILES_CONFIG` environment variable.
2. `~/.config/organize-files/config.json`.
3. `~/.agents/organize-files.local.json`.
4. Legacy `~/.codex/organize-files.local.json` for backward compatibility.

An explicit `--local-config` path takes precedence. The legacy path remains supported so existing Codex installations continue to behave exactly as configured.

On macOS and other non-Windows systems, `.DS_Store`, AppleDouble `._*`, and Office lock-file cleanup is forcibly reduced to preview, even if a Windows cleanup preset was selected. Ordinary and duplicate deletion always require separate confirmation.

## Performance model

- Runtime-platform detection is one constant-time system call.
- Config discovery performs at most a few file-existence checks and no directory walk.
- The scan remains metadata-only and performs one pre-execution recursive enumeration.
- Content evidence is acquired once and reused.
- Hashing is disabled by default and limited to frozen same-size candidates when duplicate checking is explicitly in scope.
- State checks before approved moves use file metadata, not whole-file hashes.

The portability and bilingual changes do not add another scan or content extraction pass.

## Safety

- Preview is the public default.
- Rename and move never overwrite an existing target.
- Approved plans are bound to source size and modification time.
- Generated-metadata cleanup must use the frozen inventory.
- Duplicate deletion uses a separate confirmed plan and revalidates exact hashes.
- Duplicate deletion is irreversible; a mid-batch failure reports deleted and remaining paths instead of claiming rollback.
- Links, junctions, mount points, and selected-root boundaries are not crossed.

## Development

Run the tests:

```text
python -m unittest discover -s tests -v
```

Run the public-release audit:

```text
python scripts/check-public-release.py .
```

For a private release denylist, set `ORGANIZE_FILES_DENYLIST`, use `--denylist`, or store it outside the repository under `~/.config/organize-files/private-denylist.json`.

## License

MIT. See [LICENSE](LICENSE).
