#!/usr/bin/env python3
"""Run once with sudo on the Mac mini. Does not start unapproved application code."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from autodeploy.core import load_config


def render(config, config_path, tool, python, username, user_home=None):
    service = config['service']
    label = service['name']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]+', label) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', username):
        raise ValueError('Invalid label or username')
    if service.get('manager') != 'launchd':
        raise ValueError('Installer requires launchd service manager')
    plist_path = '/Library/LaunchDaemons/' + label + '.plist'
    plist = {'Label': label, 'UserName': username,
             'ProgramArguments': [python, '-m', 'autodeploy.service_runner', config_path],
             'WorkingDirectory': tool, 'EnvironmentVariables': {'PYTHONPATH': tool, 'HOME': user_home or '/Users/' + username},
             'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,
             'ExitTimeOut': 20, 'Umask': 0o077,
             'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null'}
    # Only fixed service targets. Never allow arbitrary root shell commands.
    commands = ['/bin/launchctl bootout system/' + label,
                '/bin/launchctl bootstrap system ' + plist_path,
                '/bin/launchctl kickstart -k system/' + label]
    sudoers = username + ' ALL=(root) NOPASSWD: ' + ', '.join(commands) + '\n'
    return plist_path, plistlib.dumps(plist), sudoers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--python', required=True, help='absolute Python 3 path installed on Mac mini')
    parser.add_argument('--user', required=True)
    parser.add_argument('--output-dir', help='render only for inspection; no administrator changes')
    args = parser.parse_args()
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    tool = str(Path(__file__).resolve().parent.parent)
    if not Path(args.python).is_absolute() or not Path(args.python).is_file():
        parser.error('--python must be an existing absolute executable path')
    path, data, sudoers = render(config, str(config_path), tool, args.python, args.user, pwd.getpwnam(args.user).pw_dir)
    if args.output_dir:
        output = Path(args.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        (output / Path(path).name).write_bytes(data)
        (output / 'sudoers').write_text(sudoers)
        print('Rendered service plist and narrow sudoers rules; service not installed or started.')
        return
    if sys.platform != 'darwin' or os.geteuid() != 0:
        parser.error('Installation requires sudo on macOS')
    if pwd.getpwnam(args.user).pw_uid == 0:
        parser.error('Service must run as a non-root user')
    sudoers_path = Path('/etc/sudoers.d/autodeploy-' + config['service']['name'].replace('.', '-'))
    if Path(path).exists() or sudoers_path.exists():
        parser.error('Existing installation found; inspect it before replacing it')
    with tempfile.NamedTemporaryFile(mode='w') as candidate:
        candidate.write(sudoers)
        candidate.flush()
        subprocess.run(['/usr/sbin/visudo', '-cf', candidate.name], check=True)
    sudoers_path.parent.mkdir(mode=0o755, exist_ok=True)
    Path(path).write_bytes(data)
    os.chmod(path, 0o644)
    sudoers_path.write_text(sudoers)
    os.chmod(sudoers_path, 0o440)
    print('Installed. First approved deployment will bootstrap the service; no code started now.')


if __name__ == '__main__':
    main()
