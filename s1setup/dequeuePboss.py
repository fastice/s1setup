#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 25 2026

@author: ian

Recover a stuck parallel_boss task, or cancel one that has not started.

A pworker killed outright (systemd-oomd, OOM killer, a reboot) leaves debris
that nothing cleans up: the task file stranded in par_run/running, its log
stranded in par_run/active_logs, and the worker's comms dir behind. pboss goes
on counting the task as running, can hand a task to the dead worker (silently
losing it), and can hang at shutdown waiting on an unconsumed message.

This settles such a task the way the worker would have, and optionally puts it
back in the queue under a new number. It also handles the simpler case of
pulling a not-yet-started task back out of the queue.
"""
import argparse
import datetime
import glob
import os
import re
import shutil
import stat
import utilities as u

# Header lines the current pworker writes at the top of every active log; the
# only record of which machine (and pid) owns a task.
WORKERKEYS = {'#pworker_hostname:': 'hostname', '#pworker_PID:': 'PID',
              '#pworker_proc_count:': 'procCount',
              '#pworker_invoke_dir:': 'invokeDir'}

DEQUEUE, CANCEL = 'DEQUEUE', 'CANCEL'
# Already settled: reported, never acted on.
DONE, CANCELLED = 'DONE', 'CANCELLED'
SETTLED = (DONE, CANCELLED)


def setupDequeue():
    '''
    Handle command line args
    '''
    parser = argparse.ArgumentParser(
        description='\n\n\033[1mSettle a stuck parallel_boss task so pboss '
        'moves on, or cancel a queued one\033[0m\n\n',
        epilog='Part of the s1setup package.')
    parser.add_argument('taskNumbers', type=int, nargs='+',
                        help='task number(s), e.g. 312')
    parser.add_argument('--parRun', type=str, default='par_run',
                        help='par_run directory [par_run]')
    parser.add_argument('--status', action='store_true', default=False,
                        help='report state only, change nothing')
    parser.add_argument('--requeue', action='store_true', default=False,
                        help='re-add the task to the queue under a new number')
    parser.add_argument('--force', action='store_true', default=False,
                        help='act even when the task still looks live')
    parser.add_argument('--staleMinutes', type=int, default=30,
                        help='log silence before a task counts as dead [30]')
    parser.add_argument('--noPrompt', action='store_true', default=False,
                        help='skip the confirmation prompt')
    args = parser.parse_args()
    #
    if not os.path.isdir(args.parRun):
        u.myerror(f'dequeuePboss: no par_run directory at {args.parRun}')
    #
    return {'taskNumbers': args.taskNumbers, 'parRun': args.parRun,
            'status': args.status, 'requeue': args.requeue,
            'force': args.force, 'staleMinutes': args.staleMinutes,
            'noPrompt': args.noPrompt}


def taskNumberOf(path):
    ''' Task number from a task file name, or None. Matches both the current
    plain task_N and the old task_N_host_pid.count form. '''
    match = re.search(r'task_(\d+)', os.path.basename(path))
    if match is None:
        return None
    return int(match.group(1))


def taskPaths(parRun, number, where):
    ''' Every file in parRun/where matching this task number (a list, since
    the old-style naming allowed more than one form). '''
    found = []
    for path in glob.glob(os.path.join(parRun, where, 'task_*')):
        if taskNumberOf(path) == number and os.path.isfile(path):
            found.append(path)
    return sorted(found)


def findTask(parRun, number):
    ''' Locate the task file, returning (path, dirName), searching in the order
    a stuck task is most likely to be found. '''
    for where in ['running', 'queue', 'done', 'cancelled']:
        found = taskPaths(parRun, number, where)
        if found:
            return found[0], where
    return None, None


def activeLog(parRun, number):
    ''' The active (still-running) log for this task, or None. '''
    for path in glob.glob(os.path.join(parRun, 'active_logs', 'task_*.log')):
        if taskNumberOf(path) == number:
            return path
    return None


def readWorkerInfo(logPath):
    ''' Parse the #pworker_* header the worker writes at the top of the active
    log. Returns {} when the log is missing or predates those headers. '''
    info = {}
    if logPath is None or not os.path.exists(logPath):
        return info
    try:
        with open(logPath, errors='ignore') as fp:
            for line in fp:
                if not line.startswith('#pworker_'):
                    break          # header is contiguous and comes first
                key = line.split()[0]
                if key in WORKERKEYS:
                    info[WORKERKEYS[key]] = line.split(None, 1)[1].strip()
    except OSError:
        pass
    return info


def workerLogPath(parRun, info):
    ''' par_run/worker_logs/worker_<host>.<pid> for this task's worker. '''
    if 'hostname' not in info or 'PID' not in info:
        return None
    path = os.path.join(parRun, 'worker_logs',
                        f'worker_{info["hostname"]}.{info["PID"]}')
    return path if os.path.exists(path) else None


def commsDir(parRun, info):
    ''' par_run/comms/worker_<host>.<pid> for this task's worker. '''
    if 'hostname' not in info or 'PID' not in info:
        return None
    path = os.path.join(parRun, 'comms',
                        f'worker_{info["hostname"]}.{info["PID"]}')
    return path if os.path.isdir(path) else None


def lastLine(path):
    ''' Final non-blank line of a file, or None. '''
    line = None
    try:
        with open(path, errors='ignore') as fp:
            for this in fp:
                if this.strip():
                    line = this.strip()
    except OSError:
        return None
    return line


def tailLines(path, nLines=4):
    ''' Last nLines non-blank lines, cheaply enough for a multi-MB log. '''
    if path is None or not os.path.exists(path):
        return []
    keep = []
    try:
        with open(path, errors='ignore') as fp:
            for line in fp:
                line = line.rstrip()
                # status lines are rewritten with \r; take the last field
                line = line.split('\r')[-1].strip()
                if line:
                    keep.append(line)
                    if len(keep) > nLines:
                        keep.pop(0)
    except OSError:
        return []
    return keep


def ageMinutes(path):
    ''' Minutes since path was last written, or None. '''
    try:
        when = datetime.datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return None
    return (datetime.datetime.now() - when).total_seconds() / 60.


def taskCommand(path):
    ''' The command line from the task script (first line that is not the
    shebang or a comment). '''
    try:
        with open(path, errors='ignore') as fp:
            for line in fp:
                if line.startswith('#') or not line.strip():
                    continue
                return line.strip()
    except OSError:
        return ''
    return ''


def taskIdentity(path):
    ''' The track/frame/orbits tag the task script echoes to its exit log, when
    it has one. Purely informational. '''
    tag = r'(track=\d+ frame=\d+ orbits=[\d-]+)'
    try:
        with open(path, errors='ignore') as fp:
            match = re.search(tag, fp.read())
    except OSError:
        return ''
    return match.group(1) if match else ''


def taskStatus(parRun, number, staleMinutes):
    ''' Everything known about one task. Shared by --status and the prompt so
    the two can never disagree. '''
    path, where = findTask(parRun, number)
    if path is None:
        return None
    logPath = activeLog(parRun, number)
    info = readWorkerInfo(logPath)
    workerLog = workerLogPath(parRun, info)
    #
    # A task in queue/ that has an active log was started: the worker died
    # between the boss handing it over and its own rename into running/.
    if where == 'done':
        mode = DONE
    elif where == 'cancelled':
        mode = CANCELLED
    elif where == 'running' or logPath is not None:
        mode = DEQUEUE
    else:
        mode = CANCEL
    #
    age = ageMinutes(logPath) if logPath else None
    if age is None and workerLog is not None:
        age = ageMinutes(workerLog)
    live = mode == DEQUEUE and age is not None and age < staleMinutes
    #
    comms = commsDir(parRun, info)
    commsFiles = []
    if comms is not None:
        for name in ['to_boss/request.txt', 'to_worker/request.txt',
                     'to_worker/request_received.txt', 'done_count']:
            if os.path.exists(os.path.join(comms, name)):
                commsFiles.append(name)
    #
    # Read the script now: a requeue happens after the task file has been
    # moved, so re-reading it by path at that point would fail.
    try:
        with open(path, errors='ignore') as fp:
            lines = fp.readlines()
    except OSError:
        lines = []
    #
    return {'number': number, 'path': path, 'where': where, 'mode': mode,
            'logPath': logPath, 'info': info, 'workerLog': workerLog,
            'ageMinutes': age, 'live': live, 'comms': comms,
            'commsFiles': commsFiles, 'command': taskCommand(path),
            'identity': taskIdentity(path), 'tail': tailLines(logPath),
            'lines': lines}


def formatAge(age):
    ''' "13:49  11 min ago" style, from a minutes value. '''
    if age is None:
        return 'unknown'
    if age < 90:
        return f'{age:.0f} min ago'
    return f'{age / 60.:.1f} h ago'


def printStatus(state, parRun, staleMinutes):
    ''' The --status report for one task. '''
    number, info = state['number'], state['info']
    print(f'\ntask_{number}   {state["mode"]}   '
          f'{state["where"]}/{os.path.basename(state["path"])}')
    if info:
        worker = f'{info.get("hostname", "?")}.{info.get("PID", "?")}'
        print(f'  worker          {worker}  (proc '
              f'{info.get("procCount", "?")}, {info.get("invokeDir", "?")})')
    elif state['mode'] == DEQUEUE:
        print('  worker          unknown (active log has no #pworker header)')
    #
    if state['logPath'] is not None:
        size = os.path.getsize(state['logPath']) / 1e6
        when = datetime.datetime.fromtimestamp(
            os.path.getmtime(state['logPath']))
        verdict = 'LIVE' if state['live'] else 'STALE'
        print(f'  last activity   {os.path.basename(state["logPath"])}  '
              f'{when:%Y-%m-%d %H:%M}  {formatAge(state["ageMinutes"])}  '
              f'({size:.1f} MB)  -> {verdict} '
              f'(threshold {staleMinutes} min)')
    elif state['mode'] == CANCEL:
        print('  last activity   none - never started')
    elif state['mode'] in SETTLED:
        finished = os.path.join(parRun, 'logs', f'task_{number}.log')
        if os.path.exists(finished):
            when = datetime.datetime.fromtimestamp(os.path.getmtime(finished))
            print(f'  last activity   task_{number}.log  {when:%Y-%m-%d %H:%M}'
                  f'  {formatAge(ageMinutes(finished))}')
        print('  already settled - nothing to do')
    #
    if state['workerLog'] is not None:
        print(f'  worker log      {formatAge(ageMinutes(state["workerLog"]))}'
              f'  {lastLine(state["workerLog"])}')
    #
    if state['identity']:
        print(f'  identity        {state["identity"]}')
    print(f'  command         {state["command"][:140]}')
    #
    if state['comms'] is not None:
        print(f'  comms           {os.path.basename(state["comms"])}  '
              f'[{", ".join(state["commsFiles"]) or "empty"}]')
        if 'to_worker/request.txt' in state['commsFiles']:
            print('                  ^ unconsumed message: this is what makes '
                  'pboss hang at shutdown')
    #
    for line in state['tail']:
        print(f'  log             {line[:140]}')


def plannedActions(state, parRun, requeue, newNumber):
    ''' The exact moves this run would make, as (label, description) pairs. '''
    number, actions = state['number'], []
    if state['mode'] == DEQUEUE:
        actions.append(('move', f'{state["path"]} -> '
                                f'{os.path.join(parRun, "done")}/'
                                f'task_{number}'))
        if state['logPath'] is not None:
            actions.append(('move', f'{state["logPath"]} -> '
                                    f'{os.path.join(parRun, "logs")}/'
                                    f'task_{number}.log'))
        if state['comms'] is not None and not state['live']:
            actions.append(('delete', state['comms']))
    else:
        actions.append(('move', f'{state["path"]} -> '
                                f'{os.path.join(parRun, "cancelled")}/'
                                f'task_{number}'))
    if requeue:
        actions.append(('requeue', f'as task_{newNumber}'))
    return actions


def confirm(state, actions):
    ''' Show the actions and ask. Default is No. '''
    print('')
    for label, what in actions:
        print(f'  would {label:8s}{what}')
    if state['live']:
        print(f'  WARNING         task_{state["number"]} still looks LIVE; '
              'its worker will crash on completion if this is wrong')
    try:
        reply = input(f'Proceed with task_{state["number"]}? [y/N] ')
    except EOFError:
        return False
    return reply.strip().lower() in ['y', 'yes']


def nextTaskNumber(parRun):
    ''' One past the highest task number anywhere, including last_task, so a
    requeued task cannot collide with one a later queue generator writes. '''
    highest = 0
    for where in ['queue', 'running', 'done', 'cancelled']:
        for path in glob.glob(os.path.join(parRun, where, 'task_*')):
            number = taskNumberOf(path)
            if number is not None:
                highest = max(highest, number)
    lastTask = os.path.join(parRun, 'last_task')
    if os.path.exists(lastTask):
        try:
            with open(lastTask) as fp:
                highest = max(highest, int(fp.read().strip()))
        except (OSError, ValueError):
            pass
    return highest + 1


def writeLastTask(parRun, number):
    ''' Keep last_task in step so the queue generator starts above us. '''
    try:
        with open(os.path.join(parRun, 'last_task'), 'w') as fp:
            fp.write(f'{number}\n')
    except OSError as error:
        print(f'  warning: could not update last_task ({error})')


def moveTask(source, targetDir, number, suffix=''):
    ''' Move a task file (or its log) into targetDir under the plain name. '''
    os.makedirs(targetDir, exist_ok=True)
    target = os.path.join(targetDir, f'task_{number}{suffix}')
    os.rename(source, target)
    return target


def requeueTask(state, parRun, newNumber):
    ''' Copy the task script into the queue under a new number, with a comment
    recording where it came from. A new number is essential: the boss tracks
    handed-out tasks by path, so a reused number would never be offered. '''
    lines = list(state['lines'])
    info = state['info']
    worker = f'{info.get("hostname", "?")}.{info.get("PID", "?")}' \
        if info else 'unknown worker'
    stamp = f'{datetime.datetime.now():%Y-%m-%dT%H:%M:%S}'
    if state['mode'] == DEQUEUE:
        why = f'# original run left no exit status (worker {worker} died)\n'
    else:
        why = '# cancelled from the queue before it started\n'
    note = [f'# requeued from task_{state["number"]} by dequeuePboss '
            f'{stamp}\n', why]
    # after the shebang, so the csh script stays valid
    at = 1 if lines and lines[0].startswith('#!') else 0
    lines[at:at] = note
    #
    queueDir = os.path.join(parRun, 'queue')
    os.makedirs(queueDir, exist_ok=True)
    target = os.path.join(queueDir, f'task_{newNumber}')
    with open(target, 'w') as fp:
        fp.writelines(lines)
    os.chmod(target, os.stat(target).st_mode | stat.S_IEXEC)
    writeLastTask(parRun, newNumber)
    return target


def dequeueOne(state, myArgs):
    ''' Carry out the moves for one task. '''
    parRun, number = myArgs['parRun'], state['number']
    if state['mode'] == CANCEL:
        target = moveTask(state['path'],
                          os.path.join(parRun, 'cancelled'), number)
        print(f'  moved   {target}')
        return
    #
    target = moveTask(state['path'], os.path.join(parRun, 'done'), number)
    print(f'  moved   {target}')
    if state['logPath'] is not None:
        target = moveTask(state['logPath'], os.path.join(parRun, 'logs'),
                          number, '.log')
        print(f'  moved   {target}')
    if state['comms'] is not None and not state['live']:
        shutil.rmtree(state['comms'], ignore_errors=True)
        print(f'  removed {state["comms"]}')
    info = state['info']
    if info.get('hostname'):
        print(f'  worker {info["hostname"]}.{info.get("PID", "?")} is gone; '
              f'restore the slot on that host with:\n'
              f'      cd {info.get("invokeDir", "<project dir>")} && '
              f'run_pworkers -s 1')


def main():
    myArgs = setupDequeue()
    parRun = myArgs['parRun']
    #
    for number in myArgs['taskNumbers']:
        state = taskStatus(parRun, number, myArgs['staleMinutes'])
        if state is None:
            print(f'\ntask_{number}: not found in {parRun} '
                  '(queue/running/done/cancelled)')
            continue
        printStatus(state, parRun, myArgs['staleMinutes'])
        if myArgs['status'] or state['mode'] in SETTLED:
            continue
        #
        if state['live'] and not myArgs['force']:
            print(f'  refusing: task_{number} still looks live. Check it on '
                  f'{state["info"].get("hostname", "its host")}, then use '
                  '--force if it really is stuck.')
            continue
        #
        # Allocated per task so several numbers in one run do not collide.
        newNumber = nextTaskNumber(parRun) if myArgs['requeue'] else None
        actions = plannedActions(state, parRun, myArgs['requeue'], newNumber)
        if not myArgs['noPrompt'] and not confirm(state, actions):
            print(f'  skipped task_{number}')
            continue
        #
        dequeueOne(state, myArgs)
        if myArgs['requeue']:
            print(f'  requeued {requeueTask(state, parRun, newNumber)}')


if __name__ == '__main__':
    main()
