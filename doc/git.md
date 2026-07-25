# Git rules for Codex

## Required invariant

- For any task that changes files, the task is not complete until every
  agent-authored change is committed and pushed to the upstream remote.
- "Committed" is not enough. The final state must not leave the current branch
  ahead of its upstream.
- Push immediately after each commit, then verify the branch is synchronized
  before sending the final answer.
- The final answer for a file-changing task must mention the pushed commit hash
  and any remaining uncommitted files that were not authored by the agent.

## Required command sequence

- Before committing, run `git pull --ff-only` and `git status --short --branch`.
- Commit frequently.
- Stage only files authored or intentionally modified by the agent.
- After each commit, run `git push`.
- After pushing, run `git status --short --branch`; the branch must not show
  `[ahead N]`.
- Before ending the answer, all agent-authored changes must be committed and
  pushed.

## Safety rules

- Delete unused or obsolete files when your changes make them irrelevant, for example after refactors or feature removals.
- Moving, renaming and restoring files is allowed.
- Revert files only when the change is yours or explicitly requested.
- Do not remove in-progress edits (changes not yet committed to git) that you didn't author. Commit only the files you touched.
- Ask user if you need additional tools or packages.
- Every change must be reflected in the Ansible playbooks.
- Quote any git paths containing brackets or parentheses (e.g., `src/app/[candidate]/**`) when staging or committing.
- Follow best practices, especially cybersecurity.
- Keep things simple: avoid high level code abstractions, prefer simple CLI tools, reduce the attack surface.
- Do not write custom implementations of authentication, cryptography, or other security-sensitive mechanisms.
- Do not use destructive git operations, e.g. `git reset --hard`, `rm`, `git checkout`/`git restore` to an older commit.
- Do not amend past commits.
- Do not edit `.env` environment variables
