# Private operations journal

The active controller keeps short operational case notes at
`/var/lib/klokast/operations-journal`. The controller operator owns the tree;
directories have mode `0700` and notes have mode `0600`. The public repository
holds this procedure only. The Instance repository holds desired state, not
case history. Do not put journal content or Platform private state on an
infra-agent host.

Create the directories on the active controller with
`ansible/playbooks/67-ops-operations-journal.yml`. The `ops-controller` role
also keeps their ownership and modes correct. The setup playbook checks active
controller authority before it changes files.

Use a short, lowercase case ID with letters, digits, and hyphens. Put case
notes in `cases/<case-id>/` and coordination messages in `forum/<case-id>/`.
Before Platform work, read the latest relevant case note and forum messages.
After a material change, add a new dated case note. Add a forum message for
an intent, progress update, or handoff that another agent needs. Messages are
informational. They do not reserve a task, approve a change, or replace Git
review, signatures, installation locks, or the active-controller guard.

Each case note has these fields: UTC time, status, goal, completed work, next
action, and evidence paths with SHA-256 checksums when available. Each forum
message has UTC time, agent name, type (`intent`, `progress`, or `handoff`),
scope, and message. Link to protected evidence by path. Do not copy raw logs,
backup archives, private keys, secrets, signed intents, or datasets into a
note. A checksum reference is not proof that an artifact is still present or
fresh; verify the source before use.

Write each entry as a new file. Use an owner-only temporary file in the final
case directory and rename it after the write succeeds. Never edit an existing
entry. This POSIX shell example works for either `cases` or `forum`:

```sh
umask 077
journal=/var/lib/klokast/operations-journal
kind=cases
case_id=example-case
case "$kind" in cases|forum) ;; *) exit 2 ;; esac
case "$case_id" in ''|*[!a-z0-9-]*) exit 2 ;; esac
entry_dir="$journal/$kind/$case_id"
mkdir -m 700 -p "$entry_dir"
entry_tmp="$(mktemp "$entry_dir/.entry.XXXXXXXX")" || exit 1
trap 'rm -f "$entry_tmp"' EXIT HUP INT TERM
cat >"$entry_tmp" <<'NOTE'
UTC: 2026-09-19T10:00:00Z
Status: open
Goal: Example goal.
Done: Example result.
Next: Example next action.
Evidence: /path/to/protected/record, SHA-256: ...
NOTE
entry_time="$(date -u +%Y%m%dT%H%M%SZ)"
entry_suffix="${entry_tmp##*.}"
mv "$entry_tmp" "$entry_dir/$entry_time-$entry_suffix.md"
trap - EXIT HUP INT TERM
```

Use the actual UTC time and case content. The unique temporary suffix lets
agents write concurrently. Read entries in filename order; a newer entry
supersedes old status but does not erase the old record. Keep messages short.
Do not use the forum as an exclusive task claim or lease. If agents may change
the same files, coordinate in the forum and use Git. If they may change the
same Platform resource, use its existing operation lock and authority checks.

This first phase has no standby copy or off-controller backup. The planned S3
backup is separate work. Before controller retirement, copy required journal
notes and referenced evidence through an approved recovery path. If the active
controller is unavailable, do not create a second journal on an infra-agent
host; restore access first.
