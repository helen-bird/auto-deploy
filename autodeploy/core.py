import contextlib
import datetime
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import selectors
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import urllib.request
import uuid


COMMAND_OUTPUT_LIMIT = 8 * 1024 * 1024
LOG_BYTES = 5_000_000
HEALTH_BODY_LIMIT = 64 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Error(RuntimeError):
    pass


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def load_config(path):
    text = Path(path).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError as exc:
            raise Error('YAML support requires setup: run scripts/setup.sh, then use scripts/autodeploy.sh') from exc
        try:
            result = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise Error('Invalid YAML configuration') from exc
        if not isinstance(result, dict):
            raise Error('Configuration must be a YAML mapping')
        return result


def env_file(path):
    result = {}
    if not path.exists():
        return result
    if path.stat().st_mode & 0o077:
        raise Error('Secrets file must have mode 600')
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise Error('Invalid .env entry; use KEY=value (no shell expansion)')
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


class Manager:
    def __init__(self, config):
        self.config_path = Path(config).expanduser().resolve()
        self.cfg = load_config(self.config_path)
        c = self.cfg
        if c['repository']['branch'] != 'main' or c['deployment']['approval_required'] is not True or c['rollback']['enabled'] is not True:
            raise Error('MVP requires main, explicit approval and rollback')
        url = c['repository']['url']
        if not (re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?', url) or (c.get('test_mode') is True and Path(url).is_absolute())):
            raise Error('Use a public https://github.com/OWNER/REPO.git URL without credentials')
        self.root = Path(c['root']).expanduser().resolve()
        self.repo = self.root / 'repo'
        self.cache = self.root / 'source.git'
        self.state = self.root / 'state'
        self.logs = self.root / 'logs'
        tool = Path(__file__).resolve().parent.parent
        if self.repo == tool or self.repo in tool.parents or tool in self.root.parents or self.root == tool:
            raise Error('Application root and deployment tool checkout must be separate')
        if self.repo in self.config_path.parents:
            raise Error('Configuration must live outside production repo')
        name = c['service']['name']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]+', name):
            raise Error('Invalid launchd label')
        if c['service'].get('manager', 'launchd') != 'launchd' and not c.get('test_mode'):
            raise Error('Production requires launchd')
        for key in ('timeout_seconds', 'interval_seconds'):
            if c['health_check'][key] <= 0:
                raise Error('Health timing must be positive')
        if c['health_check']['type'] not in ('process', 'http', 'command'):
            raise Error('Unknown health check type')
        for p in (self.root, self.state, self.logs, self.root / 'config'):
            p.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.secrets = env_file(self.root / 'config' / '.env')
        self.environment = {'PATH': c.get('path', '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'),
                            'HOME': str(Path.home()), 'LANG': 'en_US.UTF-8',
                            'DEPLOY_ROOT': str(self.root), 'DEPLOY_REPO': str(self.repo),
                            'GIT_TERMINAL_PROMPT': '0'}
        self.environment.update(self.secrets)
        self.stage = 'idle'

    def redact(self, value):
        text = str(value)
        for secret in sorted(self.secrets.values(), key=len, reverse=True):
            if secret:
                for variant in (secret, json.dumps(secret)[1:-1]):
                    text = text.replace(variant, '[REDACTED]')
        text = re.sub(r'(?i)((?:token|password|api[_-]?key|secret)\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', text)
        return text

    def scrub(self, value):
        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        return self.redact(value) if isinstance(value, str) else value

    def log(self, event, **fields):
        entry = dict(timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(), stage=self.stage,
                     repository=self.cfg['repository']['url'], event=event, **fields)
        handler = RotatingFileHandler(self.logs / ('watcher.log' if self.stage == 'watch' else 'deployment.log'),
                                      maxBytes=LOG_BYTES, backupCount=3, encoding='utf-8')
        try:
            message = json.dumps(self.scrub(entry), ensure_ascii=False)
            if len(message.encode('utf-8')) > LOG_BYTES // 2:
                message = json.dumps({'event': event, 'stage': self.stage, 'detail': 'Log entry omitted: size limit exceeded'})
            handler.emit(logging.LogRecord('deployment', logging.INFO, '', 0, message, (), None))
        finally:
            handler.close()

    def run(self, args, cwd=None, check=True, timeout=None, secrets=False):
        start = time.monotonic()
        env = dict(self.environment)
        if not secrets:
            for key in self.secrets:
                env.pop(key, None)
        p = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
        output, stderr = bytearray(), bytearray()
        deadline = start + (timeout or self.cfg['deployment'].get('command_timeout_seconds', 600))
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(p.stdout, selectors.EVENT_READ, output)
                selector.register(p.stderr, selectors.EVENT_READ, stderr)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(args, timeout)
                    for key, _ in selector.select(min(remaining, 0.2)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if len(output) + len(stderr) + len(chunk) > COMMAND_OUTPUT_LIMIT:
                            raise Error('Command output exceeded 8 MiB at ' + self.stage)
                        key.data.extend(chunk)
                p.wait(timeout=max(0.001, deadline - time.monotonic()))
        except BaseException as exc:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
            # Do not log partial output: a secret may straddle the cutoff.
            if isinstance(exc, subprocess.TimeoutExpired):
                self.log('command_timeout', duration=time.monotonic()-start)
                raise Error('Command timeout at ' + self.stage) from exc
            if isinstance(exc, Error):
                self.log('command_output_limit', duration=time.monotonic()-start)
            raise
        finally:
            p.stdout.close()
            p.stderr.close()
        output = output.decode(errors='replace')
        self.log('command', command=args, exit_code=p.returncode, duration=time.monotonic()-start, output=output, stderr=stderr.decode(errors='replace'))
        if check and p.returncode:
            raise Error('Command failed at %s (exit %s); see sanitized deployment log' % (self.stage, p.returncode))
        return p.returncode, output.strip()

    def git(self, *args, repo=None, check=True):
        return self.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                         '-c', 'protocol.ext.allow=never', '-C', str(repo or self.repo), *args], check=check)[1]

    @contextlib.contextmanager
    def lock(self, operation="check"):
        # Never unlink the lock inode: flock is released by the OS on exit/crash.
        with (self.state / 'deployment.lock').open('a+') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Error('Another check or deployment holds the lock')
            self.lock_token = uuid.uuid4().hex
            lock.seek(0)
            lock.truncate()
            lock.write(json.dumps({'operation': operation, 'token': self.lock_token, 'pid': os.getpid()}))
            lock.flush()
            yield

    def status(self):
        return read_json(self.state / 'deployment_status.json', {'status': 'uninitialized', 'deployed_sha': None, 'previous_sha': None})

    def save(self, status):
        # This atomic journal is authoritative; text files are compatibility mirrors.
        atomic(self.state / 'deployment_status.json', json.dumps(status, indent=2) + '\n')
        for key in ('deployed_sha', 'previous_sha'):
            atomic(self.state / key, (status.get(key) or '') + '\n')

    def fetch(self, repo):
        if self.git('remote', 'get-url', 'origin', repo=repo) != self.cfg['repository']['url']:
            raise Error('Origin URL differs from configured source')
        self.git('fetch', '--no-tags', 'origin', '+refs/heads/main:refs/remotes/origin/main', repo=repo)

    def initialize(self):
        with self.lock():
            for path, bare in ((self.cache, True), (self.repo, False)):
                if not path.exists():
                    self.run(['git', 'init', *(['--bare'] if bare else []), str(path)])
                    self.git('remote', 'add', 'origin', self.cfg['repository']['url'], repo=path)
                if self.git('remote', 'get-url', 'origin', repo=path) != self.cfg['repository']['url']:
                    raise Error('Existing repository origin differs from configuration')
            self.fetch(self.cache)
            self.log('initialized')
        return {'status': 'initialized', 'root': str(self.root)}

    def check(self):
        self.stage = 'watch'
        with self.lock():
            current = self.status()
            if current['status'] in ('deploying', 'critical'):
                return {'status': 'needs_attention', 'deployment': current}
            self.fetch(self.cache)
            sha = self.git('rev-parse', 'origin/main', repo=self.cache)
            deployed = self.status().get('deployed_sha')
            if sha == deployed:
                return {'status': 'unchanged', 'sha': sha}
            notified = (self.state / 'last_notified_sha').read_text().strip() if (self.state / 'last_notified_sha').exists() else None
            pending = read_json(self.state / 'pending.json', {})
            if sha == notified and pending.get(sha, {}).get('base_sha') == deployed:
                return {'status': 'already_notified', 'sha': sha}
            span = deployed + '..' + sha if deployed else sha
            commits = self.git('log', '--format=%H %s', span, repo=self.cache)
            diff = self.git('diff', '--no-ext-diff', '--no-textconv', '--stat', deployed, sha, repo=self.cache) if deployed else self.git('show', '--no-ext-diff', '--no-textconv', '--format=', '--stat', sha, repo=self.cache)
            pending = read_json(self.state / 'pending.json', {})
            pending[sha] = {'base_sha': deployed, 'detected_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
            atomic(self.state / 'pending.json', json.dumps(pending))
            return {'status': 'changed', 'current_sha': deployed, 'target_sha': sha, 'commits': self.redact(commits),
                    'diff_stat': self.redact(diff), 'first_deployment': deployed is None}

    def notified(self, sha):
        with self.lock():
            if sha not in read_json(self.state / 'pending.json', {}):
                raise Error('SHA has not been detected')
            atomic(self.state / 'last_notified_sha', sha + '\n')
        return {'status': 'notification_recorded', 'sha': sha}

    def clean(self):
        if self.git('status', '--porcelain', '--untracked-files=all'):
            raise Error('Working tree has local changes; refusing to overwrite or stash')

    def verify(self, sha):
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', sha or ''):
            raise Error('An exact full commit SHA is required')
        self.git('cat-file', '-e', sha + '^{commit}')
        self.git('merge-base', '--is-ancestor', sha, 'origin/main')

    def shell(self, command, timeout=None, secrets=False):
        self.activate_venv()
        return self.run(['/bin/bash', '-e', '-o', 'pipefail', '-c', command], cwd=self.repo, timeout=timeout, secrets=secrets)

    def activate_venv(self):
        sha = self.git('rev-parse', '--verify', 'HEAD', check=False)
        venv = self.root / 'venvs' / sha
        base = self.cfg.get('path', '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin')
        self.environment['PATH'] = str(venv / 'bin') + ':' + base if venv.exists() else base
        self.environment.pop('VIRTUAL_ENV', None)
        if venv.exists():
            self.environment['VIRTUAL_ENV'] = str(venv)

    def dependencies(self):
        self.stage = 'install'
        self.environment['PATH'] = self.cfg.get('path', '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin')
        self.environment.pop('VIRTUAL_ENV', None)
        command = self.cfg['deployment'].get('install_command', 'auto')
        if command != 'auto':
            if command:
                self.shell(command)
            return
        if (self.repo / 'uv.lock').exists():
            env = self.environment
            env['UV_PROJECT_ENVIRONMENT'] = str(self.root / 'venvs' / self.git('rev-parse', 'HEAD'))
            self.run(['uv', 'sync', '--frozen'], cwd=self.repo)
        elif (self.repo / 'requirements.txt').exists() or (self.repo / 'pyproject.toml').exists():
            venv = self.root / 'venvs' / self.git('rev-parse', 'HEAD')
            self.run(['python3', '-m', 'venv', str(venv)])
            self.run([str(venv / 'bin/python'), '-m', 'pip', 'install', *(['-r', 'requirements.txt'] if (self.repo / 'requirements.txt').exists() else ['.'])], cwd=self.repo)
        elif (self.repo / 'package-lock.json').exists():
            self.run(['npm', 'ci'], cwd=self.repo)
        elif (self.repo / 'package.json').exists():
            raise Error('Missing supported lock file; configure install_command explicitly')

    def prepare(self):
        self.dependencies()
        self.stage = 'build'
        if self.cfg['deployment'].get('build_command'):
            self.shell(self.cfg['deployment']['build_command'])
        self.clean()

    def service(self, action):
        self.stage = action
        s = self.cfg['service']
        if s.get('manager') == 'test':
            self.shell(s[action + '_command'])
            return
        target = 'system/' + s['name']
        if action == 'stop':
            code, _ = self.run(['/bin/launchctl', 'print', target], check=False)
            if code == 0:
                self.run(['/usr/bin/sudo', '-n', '/bin/launchctl', 'bootout', target])
        else:
            self.run(['/usr/bin/sudo', '-n', '/bin/launchctl', 'bootstrap', 'system', '/Library/LaunchDaemons/' + s['name'] + '.plist'])
            self.run(['/usr/bin/sudo', '-n', '/bin/launchctl', 'kickstart', '-k', target])

    def healthy(self):
        self.stage = 'health'
        h = self.cfg['health_check']
        deadline = time.monotonic() + h['timeout_seconds']
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                if h['type'] == 'command':
                    self.shell(h['command'], timeout=remaining, secrets=True)
                elif h['type'] == 'http':
                    if not re.match(r'^http://(localhost|127\.0\.0\.1|\[::1\])(?=[:/])', h['url']):
                        raise Error('HTTP health check must use a loopback URL')
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
                    with opener.open(h['url'], timeout=min(5, remaining)) as response:
                        body = bytearray()
                        while len(body) <= HEALTH_BODY_LIMIT:
                            if time.monotonic() >= deadline:
                                raise Error('HTTP health response exceeded deadline')
                            chunk = response.read1(min(8192, HEALTH_BODY_LIMIT + 1 - len(body)))
                            if not chunk:
                                break
                            body.extend(chunk)
                        if len(body) > HEALTH_BODY_LIMIT:
                            raise Error('HTTP health response exceeds 64 KiB')
                        payload = json.loads(body)
                        if response.status != 200 or not isinstance(payload, dict) or payload.get('status') != 'ok':
                            raise Error('Unhealthy HTTP response')
                else:
                    _, output = self.run(['/bin/launchctl', 'print', 'system/' + self.cfg['service']['name']], timeout=remaining)
                    match = re.search(r'\bpid = (\d+)', output)
                    if not match:
                        raise Error('Service PID missing')
                    os.kill(int(match[1]), 0)
                    child = read_json(self.state / 'service_process.json', {})
                    if child.get('supervisor_pid') != int(match[1]) or child.get('sha') != self.git('rev-parse', 'HEAD'):
                        raise Error('Service child is not ready for the current checkout')
                    os.kill(child['pid'], 0)
                self.log('health_success')
                return
            except (Error, OSError, ValueError) as exc:
                self.log('health_retry', reason=str(exc))
            time.sleep(min(h['interval_seconds'], max(0, deadline-time.monotonic())))
        raise Error('Health check timeout')

    def checkout(self, sha):
        self.clean()
        self.git('checkout', '--detach', '--no-overwrite-ignore', sha)

    def deploy(self, sha=None, rollback=False, recover=False):
        with self.lock("deploy"):
            before = self.status()
            if before['status'] in ('deploying', 'critical') and not recover:
                raise Error('Interrupted or critical deployment; inspect logs and use recover')
            old = before.get('deployed_sha')
            if recover and old and sha:
                raise Error('Recover restores the last successful version; omit SHA')
            target = (old or sha) if recover else (before.get('previous_sha') if rollback else sha)
            if not target:
                raise Error('No known successful version to restore')
            if not rollback and not recover and target == old:
                raise Error('SHA is already deployed; no deployment needed')
            if (not rollback and not recover) or (recover and not old):
                pending = read_json(self.state / 'pending.json', {})
                if target not in pending:
                    raise Error('Target must be detected by check before approval')
                if pending[target]['base_sha'] != old:
                    raise Error('Approval context is stale; run check and review again')
            self.stage = 'validate'
            self.clean()
            if old and not recover and self.git('rev-parse', 'HEAD') != old:
                raise Error('Working tree HEAD differs from last successful version')
            self.fetch(self.repo)
            self.verify(target)
            if old:
                self.git('cat-file', '-e', old + '^{commit}')
            start = time.monotonic()
            record = dict(deployed_sha=old, previous_sha=before.get('previous_sha'), status='deploying', lock_token=self.lock_token, target_sha=target, old_sha=old, started_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            self.save(record)
            self.log('deployment_started', old_sha=old, target_sha=target, recovery=recover, rollback=rollback)
            touched = False
            try:
                # Stop before changing files or dependencies. KeepAlive cannot race checkout.
                touched = True
                self.service('stop')
                self.stage = 'checkout'
                self.checkout(target)
                self.prepare()
                self.service('start')
                self.healthy()
                record.update(status='success', deployed_sha=target, previous_sha=before.get('previous_sha') if recover else old,
                              deployed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), duration_seconds=round(time.monotonic()-start, 3))
                self.save(record)
                self.log('deployment_success', **record)
                return record
            except Exception as exc:
                record.update(status='failed', deployed_sha=old, previous_sha=before.get('previous_sha'), error=self.redact(str(exc)), failed_stage=self.stage)
                if touched and old:
                    try:
                        self.service('stop')
                        self.checkout(old)
                        self.prepare()
                        self.service('start')
                        self.healthy()
                        record['rollback_status'] = 'healthy'
                    except Exception as rollback_error:
                        record.update(status='critical', rollback_status='failed', rollback_error=self.redact(str(rollback_error)))
                else:
                    try:
                        self.service('stop')
                    except Exception:
                        record['stop_failed'] = True
                    record['rollback_status'] = 'unavailable_first_deployment'
                record['duration_seconds'] = round(time.monotonic()-start, 3)
                self.save(record)
                self.log('deployment_failed', **record)
                raise Error(json.dumps(record, ensure_ascii=False)) from exc
