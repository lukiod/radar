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

## When it fires, and why not in the morning here

The timer fires at **20:30 local**, and the local clock on this machine is IST.
The recipients are US small businesses across 48 metros, and the run takes
about 75 minutes at a 45 second pace, so the hour the sender is awake is not
the hour that matters.

Measured over the 208 sent rows that carry a timestamp, by the hour they landed
in US Eastern:

| hour landed (US Eastern) | rows |
| --- | --- |
| 00:00 | 11 |
| 01:00 | 104 |
| 02:00 | 30 |
| 03:00 | 14 |
| 15:00 | 24 |
| 16:00 | 25 |

**159 of 208 landed between midnight and 04:00 US Eastern**, and not one landed
between 08:00 and noon, which is when a small business owner reads email. The
01:00 bucket is the scheduled window: 09:43 IST plus a 45 second pace is 00:13
to 01:28 Eastern. The two afternoon buckets are earlier manual runs.

The pool is weighted to Central and Mountain rather than Eastern, so midnight
Eastern is 23:00 Central, 22:00 Mountain and 21:00 Pacific. The whole country
was being mailed in its sleep.

20:30 IST is 15:00 UTC and lands the batch at:

| zone | window |
| --- | --- |
| US Eastern | 11:00 to 12:15 |
| US Central | 10:00 to 11:15 |
| US Mountain | 09:00 to 10:15 |
| US Pacific | 08:00 to 09:15 |

**This is a change to a live test, and the confound is real.** The copy is
unchanged and the volume is unchanged, but the reply rate read at 300 sends
now spans two send windows, so a reply cannot be attributed to the copy alone.
The alternative was to keep spending a hundred emails a day into the US small
hours to protect the purity of a test whose independent variable is not the
one being changed. The record of which rows went out in which window is in
`sent_at`, so the two windows can be read apart later.

The only human reply the program has had is one "stop", out of 185 first touches
and 23 second touches. Timing is not proven to be why, and the deliverability
measurements in `internal-docs/earn/outreach-scale.md` rule out the cheaper
explanations. It is the one variable that was set to a value no one would
choose.

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
