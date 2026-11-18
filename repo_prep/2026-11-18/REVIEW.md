# GitHub publication review — 2026-11-18

This folder records repository preparation, not a research rerun. Nothing is staged, committed or pushed. No remote, visibility setting, collaborator invitation or project licence was created. Research artifacts remain at their original paths.

The concrete package contains **181 candidate files**; **39 files** remain withheld for unresolved sharing permission. Preparation created **27 files** and changed only the existing `.gitignore`. The new files are README/CONTRIBUTING, six docs, `.gitattributes`, two check scripts and sixteen preparation records. The existing `.env.example` and dependency pins were retained unchanged. All 194 immutable files match their pre-edit hashes; 230 permitted tests pass both here and in a candidate-only copy. See [validation](VALIDATION.md) for limits.

## Review inventories

- [publication_inventory.csv](publication_inventory.csv): every non-environment, non-cache workspace file, with category, purpose, publication status, reason, and teammate access method. Environment/cache directories are represented by category rows. Exact names are metadata, not permission to transfer their contents.
- [commit_files.txt](commit_files.txt): explicitly enumerated proposed first-commit contents. Only `approved candidate for Git` rows appear here. It is a review list, not a staged index.
- [workspace_inventory_before.csv](workspace_inventory_before.csv): initial paths/sizes, excluding `.git`, `.venv` and caches; no `.env` contents were read.
- [git_inventory_before.json](git_inventory_before.json): initial branch, tracked/index counts, refs, sanitized remote inventory and largest workspace files.
- [immutable_before.csv](immutable_before.csv), [immutable_after.csv](immutable_after.csv), [hash_comparison.json](hash_comparison.json): SHA256 evidence for 194 protected/research/config/test files, captured before documentation/config edits and checked after validation.
- [initial_secret_scan.json](initial_secret_scan.json), [repository_check_results.json](repository_check_results.json), [test_results.json](test_results.json), [VALIDATION.md](VALIDATION.md): validation and its limits.
- [fresh_clone_check.json](fresh_clone_check.json), [ignore_check.json](ignore_check.json), [git_exposure_after.json](git_exposure_after.json), [folder_sizes.csv](folder_sizes.csv): candidate-only-copy checks, ignore/newline controls, internal-ref exposure and metadata-only sizes.

## What to include, exclude or resolve

**Include as reviewed candidates:** documentation and notices; unchanged code, tests, pins and configuration; results-only canonical/Elo outputs; results-only diagnostics; independent implementations; hash/open-file records; the first G3 failure and gate-pass hash record; new offline check scripts; this preparation evidence. Code/test fixtures construct mock prices and do not export saved API response files. Real anchor names/identifiers in pre-existing test setup have not been changed.

**Exclude as private/local only:** credentials, virtual environment, caches and machine settings; all `pilot/raw/`, `pilot/normalized/`, `pilot/ledger/`; the independent `external/` checkout. Ledger rows, baseline, HALT/pending and other operational records are preserved locally, not reset or edited. Public upstream inputs can later be retrieved at the documented pin; their presence in the original workspace is not a reason to commit a nested repository.

**Unresolved sharing permission; exclude for now:** Cradl odds observations; odds-derived event/join/crosswalk tables and reports; snapshot/timing datasets; mixed odds/results reports; original/final comparison specifications containing observed odds summaries; G1-G9 narrative gate reports containing odds summaries; R1-R5 and the final comparison report. The inventory gives every exact path. `.gitignore` lists these withheld outputs individually for rights reasons, rather than hiding research outputs wholesale for size. The first failure, passing hash record, sources and other non-quote provenance can still be reviewed in Git. All withheld originals remain unchanged on the owner's workspace.

The new onboarding/status documents summarize methods, sample scope and results-only Elo metrics. They do not reproduce market-loss tables, actual prices or per-game market probabilities. Choosing to share those later needs a documented rights decision and a revised explicit inventory.

## Decisions before publication

1. Confirm ownership and intended sharing of the original project code, and choose whether to add a code licence. None has been assumed; an open-source licence is not required merely to begin controlled team review.
2. Establish what the odds-provider/Cradl terms permit, including GitHub upload to a private repository and dissemination of per-game or aggregate derivatives. Until resolved, keep the withheld files out.
3. Confirm whether institutional or NCAA source rights impose requirements beyond the checked upstream repository-level MIT notice. Attribution has been preserved; source rights were not verified on the network.
4. Approve the exact commit inventory and a repository name/owner. Recommend a **private repository containing only the candidate list** for initial team review. A later public release needs a deliberate licence and data-rights review. Private visibility does not authorize restricted-data upload.

## Recommended publication checkpoint

Suggested first commit subject: `Prepare offline collaboration docs and preserve research provenance`.

After the owner approves the concrete list, stage only the individual entries in `commit_files.txt`, review the staged filenames and diff, and repeat credential/integrity checks before committing. Review `.gitignore` alongside the list; it does not protect already tracked content. Do not stage directories recursively, export the whole workspace, or include `external/`. This preparation has intentionally not executed a staging command, commit, push, repository creation or invitation.

Two pre-existing internal Codex snapshot refs contain withheld derivatives and accounting state even though the branch has no commits. They were inspected and preserved. Do not use a mirror push or copy `.git` as a publication method; the approved future branch commit must contain only the enumerated candidates. The new `.gitattributes` preserves research bytes across checkouts so newline conversion cannot invalidate the frozen hashes.

Teammate reading order: [README](../../README.md), [onboarding](../../docs/TEAM_ONBOARDING.md), [data access](../../docs/DATA_ACCESS.md), [reproducibility](../../docs/REPRODUCIBILITY.md), [research status](../../docs/RESEARCH_STATUS.md), [artifact index](../../docs/ARTIFACT_INDEX.md), then [contributing](../../CONTRIBUTING.md) and the authoritative research records appropriate to their permitted access.
