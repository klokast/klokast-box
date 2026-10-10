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

MacBook Bash scripts must work with the Bash supplied by macOS. Do not require
Homebrew Bash or a separate Bash installation. Avoid newer Bash builtins such
as `mapfile` and `readarray`. See
[MacBook setup](../klokast-dev/runbooks/10-macbook-preparation.md).

Keep remote-script here-documents outside `$(...)` in Mac wrappers. Capture
the command's output in a file under an owner-only temporary directory, check
its exit status, then read the result. Test the enclosing Mac shell block as well as the remote
payload. Application client tests can use a specific Bash executable:

```sh
KLOKAST_TEST_BASH=/bin/bash \
  python3 -m unittest discover -s apps/music/tests
KLOKAST_TEST_BASH=/bin/bash \
  python3 -m unittest discover -s apps/torrent/tests
```
