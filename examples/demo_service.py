"""Copy into a disposable business repo for Mac mini end-to-end acceptance."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

VERSION = 'A'
HEALTHY = True


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/health':
            self.send_error(404)
            return
        body = json.dumps({'status': 'ok' if HEALTHY else 'error', 'version': VERSION}).encode()
        self.send_response(200 if HEALTHY else 503)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    HTTPServer(('127.0.0.1', 8765), Handler).serve_forever()
