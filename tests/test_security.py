import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from autodeploy.core import Error
from test_deployment import DeploymentFixture, git


class SecurityTest(DeploymentFixture, unittest.TestCase):
    def test_commit_message_is_data_and_cannot_execute(self):
        marker = self.base / 'must-not-exist'
        git(self.source, 'commit', '--allow-empty', '-m', '$(touch "' + str(marker) + '")')
        result = self.m.check()
        self.assertIn('$(touch', result['commits'])
        self.assertFalse(marker.exists())
        self.assertIsNone(self.m.status()['deployed_sha'])

    def test_output_flood_is_stopped_without_partial_secret_logging(self):
        with patch('autodeploy.core.COMMAND_OUTPUT_LIMIT', 4096):
            with self.assertRaisesRegex(Error, 'output exceeded'):
                self.m.run([sys.executable, '-c', 'import os; os.write(2,b"private-fragment"*1000)'])
        log = (self.m.logs / 'deployment.log').read_text()
        self.assertNotIn('private-fragment', log)
        self.assertIn('command_output_limit', log)
        self.assertEqual(self.m.run([sys.executable, '-c', 'print("ok")'])[1], 'ok')

    def test_logs_rotate_and_remain_json(self):
        with patch('autodeploy.core.LOG_BYTES', 1024):
            for i in range(80):
                self.m.log('test', message='x' * 100, index=i)
        files = list(self.m.logs.glob('deployment.log*'))
        self.assertLessEqual(len(files), 4)
        self.assertGreater(len(files), 1)
        for path in files:
            self.assertLessEqual(path.stat().st_size, 1024)
            for line in path.read_text().splitlines():
                json.loads(line)

    def test_redirect_and_oversized_http_responses_are_rejected(self):
        visits = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                visits.append(self.path)
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/target')
                    self.end_headers()
                else:
                    body = b'x' * 70000 if self.path == '/large' else b'{"status":"ok"}'
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path in ('/redirect', '/large'):
                self.m.cfg['health_check'] = {'type':'http', 'url':'http://127.0.0.1:%d%s' % (server.server_port, path),
                                             'timeout_seconds':0.08, 'interval_seconds':0.01}
                with self.assertRaisesRegex(Error, 'Health check timeout'):
                    self.m.healthy()
            self.assertNotIn('/target', visits)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
