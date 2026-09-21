# What runs the daily send

`daily.py` builds the day's queue and sends it. Nothing ran it on a schedule
until 09 21, so every send before that happened because a session was open and
somebody ran the sender by hand. The schedule is now the thing that makes the
machine a machine, and it is also the thing that is easiest to lose, because
it lives outside every repository.

## Linux

    bash scheduling/install.sh

Symlinks both units into `~/.config/systemd/user/`, reloads, and enables the
timer. It deliberately does not start the timer: `Persistent=true` means a
missed run fires the moment the timer starts, which on a fresh machine is a
hundred emails at once. Start it when that is what you want:

    systemctl --user start radar-daily.timer

The units are symlinked rather than copied, because a copy drifts from the
version in this repository within a week and nobody notices which one is real.
The live memory directory for the agent drifted exactly that way and was a file
behind before anyone looked.

Check it with `systemctl --user list-timers radar-daily.timer`. The run appends
to `internal-docs/comms/outreach-state/daily.log`.

## Windows

There is no systemd, so the unit files above do nothing there and the schedule
would simply not exist. That failure is silent: the machine would look idle,
not broken, and a machine that looks idle earns nothing.

`scheduling/radar-daily.cmd` is the run, written so it finds the repository
from its own location rather than from a hardcoded path. Register it:

    schtasks /create /tn "radar daily" /sc daily /st 09:43 ^
      /tr "\"C:\path\to\radar\scheduling\radar-daily.cmd\"" /f

Then open the task in Task Scheduler and tick **Run task as soon as possible
after a scheduled start is missed**, which is what `Persistent=true` does on
Linux. Without it a laptop asleep at 09:43 skips the day. Setting
`RandomDelay` to `PT7M` on the trigger matches `RandomizedDelaySec=7min`.

## What has to move with the machine

- `~/.gmail-mcp/credentials.json` and `~/.gmail-mcp/gcp-oauth.keys.json`. The
  sender authenticates from these files and from nothing else, so without them
  the run fails at its first send. They are secrets and are not in any
  repository; copy them by hand into the new profile's home directory.
- The Python version. `daily.py` is standard library only, so no packages
  need installing, but the wrapper prefers the `py -3` launcher and falls back
  to `python`.

Everything else is inside the repositories. There are no hardcoded absolute
paths in `tools/`: the queue, backlog and state locations are derived from
`__file__`, and the credential directory is `os.path.expanduser("~/.gmail-mcp")`,
which resolves on either platform.

## What was not portable, and is now

`gmail_send.py` and `verify_email.py` imported `fcntl` at module scope. That
module does not exist on Windows, so the import raised before anything ran and
`daily.py` died on its first import, with nothing logged, because the logging
sits behind the import. Both use `filelock` now, which picks its backend at
import and imports `fcntl` or `msvcrt` inside the function that needs it.
`tests/test_filelock.py` asserts that no module holds `fcntl` in its namespace,
which is where a module scope import lands.
