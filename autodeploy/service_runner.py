"""launchd child: execute the configured service as its non-root owner."""
import logging
import fcntl
import json
from logging.handlers import RotatingFileHandler
import os
import signal
import subprocess
import sys
from .core import Manager, atomic, read_json


def main():
    os.umask(0o077)
    manager = Manager(sys.argv[1])
    sha = manager.git('rev-parse', 'HEAD')
    status = manager.status()
    if status['status'] == 'deploying':
        with (manager.state / 'deployment.lock').open('a+') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.seek(0)
                owner = json.load(lock)
                if owner.get('operation') != 'deploy' or owner.get('token') != status.get('lock_token'):
                    raise SystemExit('No active deployment owns this transaction')
            else:
                raise SystemExit('Interrupted deployment; explicit recovery required')
        if sha not in (status.get('target_sha'), status.get('deployed_sha')):
            raise SystemExit('Unexpected checkout')
    elif sha != status.get('deployed_sha') or status['status'] == 'critical':
        raise SystemExit('No verified running version; explicit deployment/recovery required')
    env = dict(manager.environment)
    venv = manager.root / 'venvs' / sha
    if venv.exists():
        env['VIRTUAL_ENV'] = str(venv)
        env['PATH'] = str(venv / 'bin') + ':' + env['PATH']
    logger = logging.getLogger('service')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(manager.logs / 'service.log', maxBytes=5_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)
    process = subprocess.Popen(manager.cfg['service']['command'], cwd=manager.repo, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)

    pid_file = manager.state / 'service_process.json'
    atomic(pid_file, json.dumps({'supervisor_pid': os.getpid(), 'pid': process.pid, 'sha': sha}))

    def force_stop(sig, frame):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGALRM, force_stop)

    def stop(sig, frame):
        try:
            os.killpg(process.pid, sig)
            signal.alarm(10)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # Bound a single line without leaking fragments of a secret across chunks.
    for raw in iter(lambda: process.stdout.readline(1024 * 1024 + 1), b''):
        if len(raw) > 1024 * 1024:
            logger.error('Service output line exceeded 1 MiB; stopping child')
            force_stop(None, None)
            break
        logger.info(manager.redact(raw.decode(errors='replace').rstrip()))
    result = process.wait()
    if read_json(pid_file, {}).get('supervisor_pid') == os.getpid():
        pid_file.unlink(missing_ok=True)
    sys.exit(result)


if __name__ == '__main__':
    main()
