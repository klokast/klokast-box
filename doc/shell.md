# Shell Automation

Use the simplest shell that matches the target runtime. Alpine/OpenRC targets
often run `/bin/sh`, so shell wrappers and templates must not depend on Bash
features unless the script explicitly uses Bash and the package dependency is
intentional.

Avoid Bash process substitution and explicit `/dev/fd/*` paths in shell code
that may run on Alpine targets. Prefer normal pipes, real temporary files from
`mktemp`, here-docs written to explicit files, or direct command argument
arrays.

For a guest probe, use an owner-only temporary directory with a unique name.
Install an exit trap before writing a file. Remove only that probe's files on
success and failure, and verify the directory is empty before removal. Use a
managed state path with an explicit cleanup rule when evidence must survive
the process. Do not write diagnostic output to fixed `/tmp` names or remove a
shared temporary tree.

Developer Mac wrappers run on macOS Bash 3.2 unless proven otherwise; avoid
newer Bash builtins such as `mapfile`/`readarray`.

Keep remote-script here-documents outside `$(...)` in Mac wrappers. Capture
the command's output in a file under an owner-only temporary directory, check
its exit status, then read the result. Test the enclosing Mac shell block as well as the remote
payload. The promotion transport tests can use a specific Bash executable:

```sh
KLOKAST_TEST_BASH=/path/to/bash-3.2 \
  python3 -m unittest discover -s ansible/tests -p 'test_engine_promotion.py'
```
