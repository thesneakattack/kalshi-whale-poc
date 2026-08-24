import http.server
import threading

import pytest

from tests.support.wait_for_health import wait_for_health


class _OKHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass  # keep test output quiet


@pytest.fixture
def running_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _OKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join()


def test_wait_for_health_returns_once_server_answers(running_server):
    wait_for_health(running_server, attempts=5, delay_sec=0)


def test_wait_for_health_raises_after_exhausting_attempts_against_dead_port():
    # Port 1 is a reserved low port nothing binds to in a test sandbox -
    # connection is refused immediately, every attempt, no live server to race.
    with pytest.raises(SystemExit, match="never became healthy"):
        wait_for_health("http://127.0.0.1:1/", attempts=2, delay_sec=0)
