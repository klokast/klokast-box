"""Keep workspace tests away from the live tmux server of the runner.

Agents run these tests in a tmux pane on their runner. Workspace code runs
`tmux` and signals the client processes of a pane, so a missed patch can
close the agent's own pane. Import this module before the workspace code:
tmux then finds no server.
"""
import atexit
import os
import shutil
import tempfile

os.environ.pop('TMUX', None)
os.environ.pop('TMUX_PANE', None)
# tmux skips a missing TMUX_TMPDIR and uses /tmp, so give it an empty directory.
os.environ['TMUX_TMPDIR'] = tempfile.mkdtemp(prefix='klokast-test-tmux-')
atexit.register(shutil.rmtree, os.environ['TMUX_TMPDIR'], ignore_errors=True)
