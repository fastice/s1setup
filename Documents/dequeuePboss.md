# dequeuePboss

Settles a stuck `parallel_boss` task so `pboss` stops waiting on it, or cancels a task that has not started yet. Optionally puts the task back in the queue under a new number.

The motivating case: a `pworker` job killed outright (systemd-oomd, the OOM killer, a reboot) never gets to clean up after itself, so the task file stays in `par_run/running/`, its log stays in `par_run/active_logs/`, and the worker's comms directory is left behind. `pboss` goes on counting the task as running, can hand a task to the dead worker and lose it silently, and can hang at shutdown waiting on a message nobody will read.

Nothing in `parallel_boss` is modified; this only moves files the way a healthy worker would have.

---

## Usage

```
dequeuePboss [options] taskNumber [taskNumber ...]
```

Run it from the project directory (the one holding `par_run/`), or point `--parRun` at the directory.

| Argument | Description |
|----------|-------------|
| `taskNumber` | One or more task numbers, e.g. `312` |

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--parRun DIR` | `par_run` | The `par_run` directory |
| `--status` | False | Report state only; change nothing and exit. Safe on a running task |
| `--requeue` | False | Re-add the task to the queue under a **new** number |
| `--force` | False | Act even when the task still looks live |
| `--staleMinutes N` | `30` | Minutes of log silence before a task counts as dead |
| `--noPrompt` | False | Skip the confirmation prompt |

Every action is confirmed at a `[y/N]` prompt unless `--noPrompt` is given. Files are moved, never deleted; the only removal is the comms directory of a worker already established as dead.

---

## Two modes, detected automatically

| State | Meaning | Action |
|---|---|---|
| in `running/`, or in `queue/` **with** an `active_logs/task_N.log` | Started, then the worker died. The `queue/` variant is a worker killed between the boss handing over the task and its own rename into `running/` | **DEQUEUE** |
| in `queue/` **without** an active log | Never started | **CANCEL** |
| in `done/` or `cancelled/` | Already settled | **DONE** / **CANCELLED** — reported, never acted on |

**DEQUEUE** moves the task to `done/task_N`, promotes `active_logs/task_N.log` to `logs/task_N.log`, and (when the worker is confirmed dead) removes `comms/worker_<host>.<pid>`. It then prints how to restore the lost worker slot on that host.

**CANCEL** moves the task to `cancelled/task_N`. Not to `done/`, which would falsely claim it ran, and not deleted, so it stays recoverable. `pboss` never looks at that directory.

---

## Examples

Inspect without touching anything — works on a task that is still running:

```
dequeuePboss 312 --parRun /Volumes/insar3/ian/SentinelGreenlandUnwrapNew/par_run --status
```

```
task_312   DEQUEUE   running/task_312
  worker          petermann.333522  (proc 60, /Volumes/insar3/ian/SentinelGreenlandUnwrapNew)
  last activity   task_312.log  2026-08-25 13:49  11 min ago  (1.6 MB)  -> LIVE (threshold 30 min)
  worker log      3.4 h ago  2026-08-25 10:35:13.807754:starting task_file:par_run/queue/task_312
  identity        track=25 frame=608 orbits=4921-5096
  command         runS1interferogram ...
```

Settle a killed task and put it back in the queue:

```
dequeuePboss 221 222 --requeue
```

---

## How it knows which machine owned the task

The current `pworker` writes a header at the top of every active log:

```
#pworker_hostname: ryder
#pworker_PID: 2226217
#pworker_proc_count: 8
#pworker_invoke_dir: /Volumes/insar3/ian/SentinelGreenlandUnwrapNew
```

That is the only record of ownership, and it is what makes `--status` able to name a worker on another machine. A log written by an older worker has no header, and the tool reports the worker as unknown but still works.

---

## Protocol notes

Behaviour worth knowing, established by reading the deployed `pworker.py` and `boss.py`:

- **A requeued task must get a new number.** `boss.update_task_list()` tracks handed-out tasks by *path string* in an in-memory list, so re-creating `queue/task_N` for a number already issued would never be offered to a worker. `--requeue` therefore allocates one past the highest number found anywhere (including `last_task`) and rewrites `last_task`.
- **Cancelling a queued task is safe even if the boss has already offered it.** `pworker.get_new_job()` retries a missing task file 10 x 0.5 s, then logs `Task file ... not visible after retries, ignoring` and asks for another job. One wasted round trip, no crash.
- **Deleting a comms directory retires a worker, it does not feed it.** `pworker.consider_retirement()` sets `retired` when its `to_worker` directory is gone, and the run loop exits. For a dead worker there is no process to affect; the cleanup is for the boss's benefit.
- **A dequeued worker slot is not automatically replaced.** Start a new one on that host with `run_pworkers -s 1` from the project directory — the tool prints the exact command.
- **Old-style names are tolerated.** The current worker writes plain `task_N` in `running/` and `done/`; older versions wrote `task_N_<host>_<pid>.<count>`. Both are matched.

---

## See also

- [reprocessS1](reprocessS1.md), [setupS1Tracks](setupS1Tracks.md) — the workflows that generate the queues this operates on
