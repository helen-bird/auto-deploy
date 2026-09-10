import argparse
import json
import os
import signal
import sys
from pathlib import Path
from .core import Error, Manager


def interrupted(signum, frame):
    # Leave the durable journal in deploying state; a new deployment must not proceed.
    raise SystemExit(128 + signum)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Explicit-SHA deployment manager')
    parser.add_argument('--config', default=os.environ.get('DEPLOY_CONFIG', 'deployment.yaml'))
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'check', 'status', 'health', 'rollback', 'automation-prompt'):
        sub.add_parser(name)
    for name in ('deploy', 'notified'):
        p = sub.add_parser(name)
        p.add_argument('sha', help='full commit SHA; deploy is an explicit approval action')
    sub.add_parser('recover').add_argument('sha', nargs='?', help='only for an interrupted first deployment: explicitly reapprove a detected full SHA')
    args = parser.parse_args()
    manager = None
    try:
        manager = Manager(args.config)
        signal.signal(signal.SIGTERM, interrupted)
        if args.command == 'init':
            result = manager.initialize()
        elif args.command == 'check':
            result = manager.check()
        elif args.command == 'notified':
            result = manager.notified(args.sha)
        elif args.command == 'deploy':
            result = manager.deploy(args.sha)
        elif args.command in ('rollback', 'recover'):
            result = manager.deploy(sha=getattr(args, 'sha', None), rollback=args.command == 'rollback', recover=args.command == 'recover')
        elif args.command == 'health':
            manager.healthy()
            result = {'status': 'healthy'}
        elif args.command == 'automation-prompt':
            template = Path(__file__).resolve().parent.parent / 'docs' / 'automation-prompt.md'
            import shlex
            print(template.read_text().replace('{{CLI}}', shlex.quote(str(Path(__file__).resolve().parent.parent / 'scripts' / 'autodeploy.sh')))
                  .replace('{{CONFIG}}', shlex.quote(str(manager.config_path)))
                  .replace('{{TIME}}', manager.cfg['schedule']['time'])
                  .replace('{{ZONE}}', manager.cfg['schedule']['timezone']))
            return
        else:
            result = manager.status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (Error, OSError, ValueError, KeyError) as exc:
        message = manager.redact(str(exc)) if manager else (str(exc) if isinstance(exc, (Error, KeyError)) else 'Configuration error: ' + type(exc).__name__)
        print(json.dumps({'status': 'error', 'error': message}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
