import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from autodeploy.core import Manager, Error, load_config
from scripts.install_service import render


def git(path, *args):
    p = subprocess.run(['git', '-C', str(path), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return p.stdout.strip()


class DeploymentFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='deploy-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'upstream'
        self.source.mkdir()
        git(self.source, 'init', '-b', 'main')
        git(self.source, 'config', 'user.email', 'test@example.invalid')
        git(self.source, 'config', 'user.name', 'Test')
        self.a = self.commit('good')
        self.root = self.base / 'application'
        self.config = self.base / 'deployment.yaml'
        self.cfg = load_config(Path('deployment.example.yaml'))
        self.cfg.update(root=str(self.root), test_mode=True)
        self.cfg['repository']['url'] = str(self.source)
        self.cfg['service'] = {'name': 'test.service', 'manager': 'test',
                               'stop_command': 'echo stopped > "$DEPLOY_ROOT/service"',
                               'start_command': 'echo running > "$DEPLOY_ROOT/service"'}
        self.cfg['health_check'] = {'type': 'command', 'command': 'test "$(cat version)" != broken',
                                    'timeout_seconds': 0.12, 'interval_seconds': 0.01}
        self.cfg['deployment']['install_command'] = ''
        self.write_config()
        self.m = Manager(self.config)
        self.m.initialize()

    def write_config(self):
        self.config.write_text(json.dumps(self.cfg))

    def commit(self, value):
        (self.source / 'version').write_text(value)
        git(self.source, 'add', '.')
        git(self.source, 'commit', '-m', value)
        return git(self.source, 'rev-parse', 'HEAD')

    def first(self):
        self.m.check()
        self.m.deploy(self.a)

class DeploymentTest(DeploymentFixture, unittest.TestCase):
    def test_exact_sha_and_read_only_watch(self):
        self.first()
        b = self.commit('second')
        report = self.m.check()
        self.assertEqual(report['target_sha'], b)
        self.assertEqual(git(self.m.repo, 'rev-parse', 'HEAD'), self.a)
        c = self.commit('third')
        self.m.deploy(b)
        self.assertEqual(git(self.m.repo, 'rev-parse', 'HEAD'), b)
        self.assertEqual(self.m.status()['deployed_sha'], b)
        self.assertEqual(self.m.check()['target_sha'], c)

    def test_notification_ack_and_unchanged(self):
        self.assertEqual(self.m.check()['status'], 'changed')
        self.assertEqual(self.m.check()['status'], 'changed')
        self.m.notified(self.a)
        self.assertEqual(self.m.check()['status'], 'already_notified')
        self.m.deploy(self.a)
        self.assertEqual(self.m.check()['status'], 'unchanged')

    def test_unapproved_and_short_sha_rejected(self):
        with self.assertRaises(Error):
            self.m.deploy(self.a)
        self.m.check()
        with self.assertRaises(Error):
            self.m.deploy(self.a[:7])
        self.assertFalse((self.m.repo / 'version').exists())

    def test_health_failure_rolls_back(self):
        self.first()
        broken = self.commit('broken')
        self.m.check()
        with self.assertRaises(Error):
            self.m.deploy(broken)
        self.assertEqual(self.m.status()['rollback_status'], 'healthy')
        self.assertEqual(self.m.status()['deployed_sha'], self.a)
        self.assertEqual(git(self.m.repo, 'rev-parse', 'HEAD'), self.a)
        self.assertEqual((self.root / 'service').read_text().strip(), 'running')

    def test_first_failure_has_no_fake_success(self):
        broken = self.commit('broken')
        self.m.check()
        with self.assertRaises(Error):
            self.m.deploy(broken)
        self.assertIsNone(self.m.status()['deployed_sha'])
        self.assertEqual(self.m.status()['rollback_status'], 'unavailable_first_deployment')
        self.assertEqual((self.root / 'service').read_text().strip(), 'stopped')

    def test_dirty_tree_rejected(self):
        self.first()
        b = self.commit('second')
        self.m.check()
        for path in ('version', 'unknown'):
            with self.subTest(path=path):
                old = (self.m.repo / path).read_text() if (self.m.repo / path).exists() else None
                (self.m.repo / path).write_text('local work')
                with self.assertRaises(Error):
                    self.m.deploy(b)
                self.assertEqual((self.m.repo / path).read_text(), 'local work')
                if old is None:
                    (self.m.repo / path).unlink()
                else:
                    (self.m.repo / path).write_text(old)
        self.assertEqual(self.m.status()['deployed_sha'], self.a)

    def test_feature_branch_and_force_push_rejected(self):
        self.first()
        b = self.commit('second')
        self.m.check()
        git(self.source, 'checkout', '-b', 'feature')
        feature = self.commit('feature')
        with self.assertRaises(Error):
            self.m.deploy(feature)
        git(self.source, 'checkout', 'main')
        git(self.source, 'reset', '--hard', self.a)
        with self.assertRaises(Error):
            self.m.deploy(b)
        self.assertEqual(git(self.m.repo, 'rev-parse', 'HEAD'), self.a)

    def test_manual_rollback_and_stale_approval(self):
        self.first()
        b = self.commit('second')
        self.m.check()
        c = self.commit('third')
        self.m.check()
        self.m.deploy(b)
        with self.assertRaises(Error):
            self.m.deploy(c)
        self.m.deploy(rollback=True)
        self.assertEqual(self.m.status()['deployed_sha'], self.a)
        self.assertEqual(self.m.status()['previous_sha'], b)

    def test_concurrent_process_lock(self):
        with self.m.lock():
            p = subprocess.run([sys.executable, '-m', 'autodeploy.cli', '--config', str(self.config), 'check'], capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn('holds the lock', p.stderr)

    def test_interrupted_and_recover(self):
        self.first()
        status = self.m.status()
        status['status'] = 'deploying'
        self.m.save(status)
        self.m.check()
        with self.assertRaises(Error):
            self.m.deploy(self.a)
        self.m.deploy(recover=True)
        self.assertEqual(self.m.status()['status'], 'success')

    def test_rollback_failure_is_critical(self):
        self.first()
        b = self.commit('broken')
        self.m.check()
        self.m.cfg['health_check']['command'] = 'false'
        with self.assertRaises(Error):
            self.m.deploy(b)
        self.assertEqual(self.m.status()['status'], 'critical')
        self.assertEqual(self.m.status()['deployed_sha'], self.a)

    def test_install_failure_rolls_back(self):
        self.first()
        b = self.commit('broken')
        self.m.check()
        self.m.cfg['deployment']['install_command'] = 'test "$(cat version)" != broken'
        with self.assertRaises(Error):
            self.m.deploy(b)
        self.assertEqual(self.m.status()['failed_stage'], 'install')
        self.assertEqual(self.m.status()['rollback_status'], 'healthy')

    def test_secrets_redacted(self):
        secret = 'special"key\\value'
        self.m.secrets['TOKEN'] = secret
        self.m.log('test', output=secret, command=['echo', secret])
        data = (self.m.logs / 'deployment.log').read_text()
        self.assertNotIn(json.dumps(secret)[1:-1], data)
        self.assertIn('[REDACTED]', data)

    def test_ignored_file_not_overwritten(self):
        (self.source / '.gitignore').write_text('collision\n')
        a = self.commit('ignore')
        self.m.check()
        self.m.deploy(a)
        (self.m.repo / 'collision').write_text('local ignored data')
        (self.source / 'collision').write_text('remote')
        git(self.source, 'add', '-f', 'collision')
        b = self.commit('collision commit')
        self.m.check()
        with self.assertRaises(Error):
            self.m.deploy(b)
        self.assertEqual((self.m.repo / 'collision').read_text(), 'local ignored data')

    def test_service_plist_runs_as_user(self):
        self.cfg['service']['manager'] = 'launchd'
        path, data, rules = render(self.cfg, '/Users/test/config.json', '/Users/test/tools', '/usr/bin/python3', 'test')
        plist = plistlib.loads(data)
        self.assertEqual(plist['UserName'], 'test')
        self.assertTrue(plist['KeepAlive'])
        self.assertTrue(plist['RunAtLoad'])
        self.assertNotIn('*', rules)
        self.assertIn('bootstrap system ' + path, rules)


if __name__ == '__main__':
    unittest.main()
