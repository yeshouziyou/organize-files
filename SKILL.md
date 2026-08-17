---
name: organize-files
description: Use when a user asks to inspect, classify, rename, move, organize, archive, clean up, deduplicate, or audit human-managed files and folders. 用于检查、分类、重命名、移动、归档、清理、去重或审计人工管理的文件和文件夹，兼容中英文请求与内容。
license: MIT
---

# Organize Files

## Principle

Use the least repeated work that supports a reliable decision. Use one pre-execution filesystem walk to build a single recursive inventory, then acquire content evidence once and reuse it for classification, naming, audit, execution, and verification. Every applicable human file receives one evidence pass; path and filename are routing hints, not content evidence. Keep full coverage local and send only compact stdout to the conversation. Preview, file actions, folder restructuring, reference updates, and deletion are distinct permissions.

Respond in the user's language unless explicitly requested otherwise. Accept Chinese, English, and mixed-language filenames and content. Preserve verified proper nouns, official identifiers, acronyms, and intentional casing instead of translating or normalizing them merely to make the output monolingual. The `language` policy controls presentation, not evidence coverage or file eligibility; `auto` follows the user's language.

## Load only required references

Verify each selected header version, then read only the named sections:

- Rename: `references/通用文件命名标准.md` `1.6`, sections 1–6 and 10–12.
- Classify: `references/通用文件分类标准.md` `1.7`, sections 1–7 and 12–15.
- Generated metadata found: `references/20260816_自动清理生成元数据规则.md` `1.3`.
- Post-execution empty directories: `references/20260816_空目录清理规则.md` `1.1`.
- Finance root: `references/20260816_财务档案分类配置.md` `1.0`.
- Deletion beyond policy-approved generated metadata and safe empty directories, duplicate confirmation, signed/encrypted files, cross-storage copying, content rewriting, reference changes, or sync anomalies: `references/20260816_文件整理风险与验证规则.md` `1.5`.

A selected-reference mismatch is a hard stop. Update its expected version here with any reference change.

## Workflow

1. Resolve the exact root and mode; never move outside it. Create one task-local temporary directory outside the selected root for the effective policy, inventory, evidence, annotations, contact sheets, decisions, preview, plan, and verification output. Run `scripts/resolve-policy.py --output <task-dir>/policy.json`; it detects the runtime platform and uses the public preview-only default unless the user has selected a published preset in the repository-external local config. On macOS and other non-Windows systems it forcibly downgrades `.DS_Store`, `._*`, and Office-lock cleanup to preview, even if a Windows cleanup preset was selected. Freeze the resulting policy for the task. Policy selection may grant only generated-metadata and safe-empty-directory cleanup; ordinary-file and duplicate deletion always remain confirmation-gated.
2. Run `scripts/scan-files.py <root> --inventory-out <task-dir>/inventory.json`. This is the only pre-execution recursive enumeration. The inventory separately records normal files, generated-metadata candidates, initial empty directories, boundaries, sync flags, and optional same-size groups. Keep it local; use compact stdout for scope counts and `scripts/query-inventory.py <inventory.json>` for filtered or paged rows. Add `--include-duplicate-candidates` only when duplicate confirmation is in scope. Do not cross links, junctions, mount points, or boundaries.
3. Treat the saved inventory as the cleanup source of truth. Run `scripts/cleanup-generated-metadata.py <root> --inventory <inventory.json> --policy <task-dir>/policy.json` for the dry-run. Repeat with `--apply` only when every affected generated-metadata type is `auto` or `auto-if-safe` in the frozen policy; the script enforces this gate. Otherwise present the candidates without deletion. The CLI rejects both dry-run and apply without `--inventory`; both calls validate only exact listed paths and never rescan the root. Apply removes successfully deleted candidates from the inventory and records the cleanup result. Skip active, changed, missing, or uncertain Office locks and preserve fonts/assets. Do not rerun the root scan after expected metadata deletion.
4. Select one classification axis, compare sibling groups for semantic overlap, reuse suitable folders, and keep bundles together. Existing folders and filenames suggest where to inspect first but never prove what a file contains. Apply this folder contract to every directory component the plan will create or rename: stable category folders use a stable noun without a forced date; when date is the primary retrieval field, use `日期_标题`. Do not create `[日期]标题`, `【日期】标题`, `日期-标题`, or a date without a non-empty title. An already-existing legacy dated folder may be reused without renaming, but it remains a reported inconsistency; preservation is not permission to create another legacy-form folder.
5. Establish only structurally verified exclusions, such as a program project identified by project markers or an explicit protected scope. Run `scripts/build-evidence-index.py <inventory.json> --output <task-dir>/evidence.json` with verified `--exclude-prefix` values and a task-local contact-sheet directory. The script reads only inventory-listed paths, validates each evidence key, enforces bounded text and ZIP/OOXML member, total-size, member-count, and compression-ratio limits, batch-extracts supported document/archive content, and marks images, media, legacy formats, resource-limit failures, and unsupported formats that still need evidence.
6. Complete the pending rows without rescanning: convert or locally inspect legacy documents; visually audit every applicable human image from contact sheets and full resolution when needed; inspect video keyframes, audio transcript/sample, and archive listings as appropriate. Store AI findings and their actual summary/proof in one annotations JSON, then merge them with `--existing-evidence <evidence.json> --annotations <annotations.json>`; merge mode validates keys but does not reopen content or rebuild contact sheets. A status label alone is not evidence. Unsupported or unreadable files stay `undecided`; a clear name or folder cannot waive evidence.
7. Use the same evidence index to classify and name every applicable human file. Give a date when the naming evidence ladder supports one, label inferred dates, and freeze chosen dates after naming. When duplicate confirmation is in scope, group by size during the scan and run `scripts/hash-duplicate-candidates.py <inventory.json> --output <task-dir>/duplicates.json`; it hashes only frozen same-size candidates and never deletes. Treat identical hashes as byte identity only; retain contextual copies used for different periods, displays, or workflows.
8. Put per-file judgments in declarative `decisions.json`; do not create directory-specific scripts. Each row records `resolution` as `compliant`, `changed`, `undecided`, or `exclusion`. Run `scripts/compile-plan.py <evidence.json> <decisions.json> --output <plan.json> --preview-out <preview.md>`. The compiler reconciles the original inventory, evidence, and decisions; binds image/media/document/archive completion to type-specific evidence; preserves undecided rows in the preview; validates the ten displayed fields, safe relative paths, collision-free targets, and every new or renamed dated folder component. It writes `expected_size` and `expected_mtime_ns` into every version-2 plan row, blocks non-`日期_标题` dated folders, and writes reused legacy dated folders to `legacy_folder_warnings` and the preview.
9. Detail proposed changes and undecided rows; summarize `保持不变` and exclusions. After feedback, patch only affected annotation or decision rows and recompile; do not reread unchanged content or regenerate evidence whose key is unchanged. Treat the inventory changed only when unlisted external paths appeared, listed size or modification time changed, a cloud placeholder resolved, or a plan conflict indicates drift; only then refresh the affected scope or inventory. Expected deletion of inventory-listed metadata is not drift.
10. After approval, run `scripts/apply-plan.py <plan> --dry-run`, then the same fixed plan without `--dry-run`. It rechecks `expected_size` and `expected_mtime_ns` immediately before each no-replace move, never deletes or overwrites, and removes its newly created empty directories if a failure causes rollback. Its success status means only Plan execution complete. Duplicate deletion is a distinct, irreversible workflow: after showing exact keep/delete rows and receiving separate approval, create a version-1 duplicate deletion plan bound to `duplicates.json`, run `scripts/apply-duplicate-deletions.py <duplicate-plan>` for dry-run, then run it with `--apply --confirmed`; the script rehashes both the retained and deleted copies before deleting. It cannot roll back completed deletions; if a later row fails, report `DUPLICATE_DELETION_PARTIAL`, the deleted paths, and the remaining paths.
11. Verify changed rows from the fixed plan without rereading unchanged content, perform the AI-owned completion audit from the saved evidence and decisions, and rerun the compiler with `--require-complete` before claiming full completion. A partial plan with undecided rows may be previewed or explicitly executed, but remains `completion_eligible=false`. Then run `scripts/cleanup-empty-directories.py <root> --policy <task-dir>/policy.json`; repeat with `--apply` only when `empty_directories` is `auto-if-safe` in the frozen policy; the script enforces this gate. This post-execution directory walk checks newly changed directory state and is not a duplicate pre-scan. Save forward/rollback mappings, audit counts, exceptions, policy source, and empty-directory results in `YYYYMMDD_文件整理记录.md`. After successful delivery, delete the exact task-local temporary directory; on failure, retain it and report its path.

Freeze Skill/reference versions during a directory task; collect rule changes for a separate update afterward.

## AI-owned completion audit

Perform this semantic audit with AI judgment; do not replace it with a complex audit script.

1. Review every top-level group and every extension reported by the scan. Check every evidence row for content-versus-name, content-versus-classification, date support, opaque titles, root scatter, and conflicts. Inspect target directory components as well as filenames: confirm each new or renamed dated folder uses `日期_标题`, review sibling naming consistency, and explicitly report every `legacy_folder_warning`. Reuse the acquired evidence; reopen a file only when evidence is missing, contradictory, or too weak for the proposed decision.
2. Treat program projects and machine internals as excluded only when project markers, references, or an explicit protected scope provide structural evidence. Never exclude a high-level folder merely because its name contains `资源`, `案例`, or `交付`; never silently ignore an unrecognized extension.
3. Report: total files, applicable human files, evidence-complete files, already compliant, changed, undecided, and explicit exclusions with reasons. Reconcile exactly: `all files = applicable human files + explicit exclusions` and `applicable human files = compliant + changed + undecided`.
4. Require evidence coverage to reconcile as well: every applicable human file must have a completed evidence status. Sampling is allowed for deciding how deeply to inspect a coherent bundle, never for omitting files from the evidence index.
5. If undecided is nonzero, evidence coverage is incomplete, or either equation does not reconcile, report partial completion and the remaining rows. Only after full reconciliation and zero undecided rows may the final result say Directory organization complete.

Compact output changes transport, not coverage: calculate the reconciliation from every row in the saved inventory, plan, and exclusion set. A small stdout summary never authorizes sampling away files.

## Evidence coverage

- Modern documents: batch-extract internal title, relevant text, and available internal dates once. Move from first-page/sheet/slide evidence to deeper extraction only when the decision needs it.
- Images: every applicable human image receives visual evidence. Contact sheets are a batching interface, not a sampling exemption; open full resolution only when the thumbnail is insufficient.
- Video and audio: record container metadata and inspect representative keyframes or a transcript/content sample sufficient to identify the human-managed item.
- Archives: inspect the member listing; inspect contained content only when listing evidence cannot support classification or naming.
- Legacy or unsupported formats: use a local converter, application inspection, OCR, or another bounded fallback. If no evidence can be obtained, keep the row undecided.
- Reuse evidence by `relative_path + size + modification time + reparse/sync state`. A move carries evidence through the fixed plan mapping; only a changed key or contradiction triggers re-extraction.

## Image audit

Classify every image as `human archive`, `program/design asset`, `machine-generated image`, or `undecided` before proposing a filename.

- Keep program/design asset names unchanged when paths, project markers, static/assets/icon folders, multi-resolution variants, paired formats, or references indicate machine use. Renaming referenced assets requires separate reference-update authorization.
- For each human archive image, choose the date from EXIF capture time, reliable event/file/folder context, then filesystem time as an explicitly inferred date. Build a descriptive title from verified context or visible content.
- Batch recurring images by directory and pattern. Include every applicable image in contact sheets for visual triage and inspect full-resolution files only when a thumbnail or context is insufficient.
- Preserve meaningful sequence, variant, and official identifiers inside the title. Report counts for all four image classes and list every undecided image.

## Preview and permissions

Detailed rows preserve:

`原路径 | 建议分类 | 建议文件名 | 最终路径 | 分类依据 | 分类可信度 | 日期依据 | 日期可信度 | 建议动作 | 风险或说明`

Actions: `保持不变`, `仅改名`, `仅移动`, `新建文件夹后移动`, `移动并改名`, `跳过`.

Row approval covers only listed folder creation, move, and rename. Generated metadata and safe post-execution empty directories are deletion exceptions only when the frozen effective policy explicitly enables them; all other deletion, restructuring, reference/content changes, and out-of-root movement need separate approval.

## Verification

- Ordinary same-root rename/move: source gone, target present, expected count, unchanged size and modification time.
- Safe empty directories: dry-run, boundary/emptiness/exclusion check, then apply; do not hash.
- Duplicate: full hash only after same-size grouping.
- Protected file, ordinary file deletion, cross-storage copy, anomaly, or explicit audit: use targeted high-risk checks.
- Never create a whole-root content digest by default or print bulk hashes in previews/Markdown records.

Stop the task for wrong root, reference mismatch, broad boundary/sync uncertainty, or plan-wide authorization mismatch. Otherwise stop only affected rows. Wording/presentation corrections do not restart valid work.

## Avoid

- Trusting an existing path or filename as proof of content, classification, or compliance.
- Omitting applicable human files from evidence acquisition; optimize batching and reuse instead of reducing coverage.
- Rereading unchanged files after their evidence key is stable.
- Printing the full inventory when a saved inventory plus filtered queries is available.
- Rerunning the root scan for wording, presentation, or a small row correction.
- Running generated-metadata cleanup as a separate root search after an inventory already exists.
- Rebuilding the normal-file inventory after expected metadata cleanup; update and reuse the saved inventory instead.
- Regenerating a full preview after a small correction.
- Creating directory-specific scripts or scattering intermediate files across the workspace; keep task logic declarative and all intermediates in the single task directory.
- Hashing the whole root for ordinary moves.
- Treating permission to preserve or reuse an existing legacy folder as permission to create new folders in that legacy format.
