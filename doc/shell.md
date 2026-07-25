# Shell Automation

Use the simplest shell that matches the target runtime. Alpine/OpenRC targets
often run `/bin/sh`, so shell wrappers and templates must not depend on Bash
features unless the script explicitly uses Bash and the package dependency is
intentional.

Avoid Bash process substitution and explicit `/dev/fd/*` paths in shell code
that may run on Alpine targets. Prefer normal pipes, real temporary files from
`mktemp`, here-docs written to explicit files, or direct command argument
arrays.

Developer Mac wrappers run on macOS Bash 3.2 unless proven otherwise; avoid
newer Bash builtins such as `mapfile`/`readarray`.
