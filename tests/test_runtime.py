import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from autodeploy.core import Error, Manager
from test_deployment import DeploymentFixture


class RuntimeTest(DeploymentFixture, unittest.TestCase):
    def runner(self):
        return subprocess.run([sys.executable, '-m', 'autodeploy.service_runner', str(self.config)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)

    def test_runner_executes_successful_version_and_redacts(self):
        self.first()
        env = self.root / 'config' / '.env'
        env.write_text('TEST_SECRET=not-a-real-credential\n')
        env.chmod(0o600)
        self.cfg['service']['command'] = [sys.executable, '-c', 'import os; print(os.environ["TEST_SECRET"])']
        self.write_config()
        p = self.runner()
        self.assertEqual(p.returncode, 0, p.stderr)
        log = (self.m.logs / 'service.log').read_text()
        self.assertIn('[REDACTED]', log)
        self.assertNotIn('not-a-real-credential', log)
        self.assertFalse((self.m.state / 'service_process.json').exists())

    def test_runner_stops_unbounded_line_without_logging_fragment(self):
        self.first()
        self.cfg['service']['command'] = [sys.executable, '-c',
            'import os; os.write(1, b"private-fragment" * 100000)']
        self.write_config()
        p = self.runner()
        self.assertNotEqual(p.returncode, 0)
        log = (self.m.logs / 'service.log').read_text()
        self.assertIn('output line exceeded', log)
        self.assertNotIn('private-fragment', log)

    def test_runner_rejects_interrupted_transaction_even_during_check(self):
        self.first()
        self.cfg['service']['command'] = [sys.executable, '-c', 'raise RuntimeError("MUST_NOT_EXECUTE")']
        self.write_config()
        state = self.m.status()
        state.update(status='deploying', lock_token='crashed-transaction')
        self.m.save(state)
        with self.m.lock('check'):
            p = self.runner()
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn('MUST_NOT_EXECUTE', p.stderr)
        self.assertIn('No active deployment', p.stderr)
        p = self.runner()
        self.assertIn('Interrupted deployment', p.stderr)

    def test_runner_allows_current_approved_transaction(self):
        self.first()
        self.cfg['service']['command'] = [sys.executable, '-c', 'print("approved runner")']
        self.write_config()
        with self.m.lock('deploy'):
            state = self.m.status()
            state.update(status='deploying', lock_token=self.m.lock_token)
            self.m.save(state)
            p = self.runner()
            self.assertEqual(p.returncode, 0, p.stderr)

    def test_http_health_response(self):
        class Handler(BaseHTTPRequestHandler):
            healthy = True

            def do_GET(self):
                body = json.dumps({'status': 'ok' if self.healthy else 'error'}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.m.cfg['health_check'] = {'type': 'http', 'url': 'http://127.0.0.1:%d/health' % server.server_port,
                                         'timeout_seconds': 0.12, 'interval_seconds': 0.01}
            self.m.healthy()
            Handler.healthy = False
            with self.assertRaises(Error):
                self.m.healthy()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_first_interrupted_deployment_can_be_reapproved(self):
        self.m.check()
        self.m.save({'status': 'deploying', 'deployed_sha': None, 'previous_sha': None, 'target_sha': self.a})
        self.assertEqual(self.m.check()['status'], 'needs_attention')
        with self.assertRaises(Error):
            self.m.deploy(recover=True)
        self.m.deploy(self.a, recover=True)
        self.assertEqual(self.m.status()['deployed_sha'], self.a)

    def test_build_failure_restores_old_version(self):
        self.first()
        b = self.commit('broken')
        self.m.check()
        self.m.cfg['deployment']['build_command'] = 'test "$(cat version)" != broken'
        with self.assertRaises(Error):
            self.m.deploy(b)
        self.assertEqual(self.m.status()['failed_stage'], 'build')
        self.assertEqual(self.m.status()['rollback_status'], 'healthy')

    def test_restart_failure_restores_old_version(self):
        self.first()
        b = self.commit('broken')
        self.m.check()
        self.m.cfg['service']['start_command'] = 'test "$(cat version)" != broken'
        with self.assertRaises(Error):
            self.m.deploy(b)
        self.assertEqual(self.m.status()['failed_stage'], 'start')
        self.assertEqual(self.m.status()['rollback_status'], 'healthy')

    def test_command_timeout_is_bounded(self):
        start = time.monotonic()
        with self.assertRaises(Error):
            self.m.run([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=0.05)
        self.assertLess(time.monotonic() - start, 2)
        self.assertIn('command_timeout', (self.m.logs / 'deployment.log').read_text())

    def test_prompt_has_resolved_paths_and_timezone(self):
        p = subprocess.run([sys.executable, '-m', 'autodeploy.cli', '--config', str(self.config), 'automation-prompt'],
                           capture_output=True, text=True, check=True)
        self.assertNotIn('{{', p.stdout)
        self.assertIn(str(self.config), p.stdout)
        self.assertIn('Asia/Singapore', p.stdout)
        self.assertIn('09:00', p.stdout)
