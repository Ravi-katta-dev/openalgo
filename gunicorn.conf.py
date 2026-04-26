"""
Gunicorn configuration for OpenAlgo.

The when_ready hook kills the health-check stub (started by start.sh) once
gunicorn is ready to accept connections.  This eliminates the gap that causes
502 errors on Render and other cloud platforms that probe the health endpoint
continuously during and after deployment.
"""

import os
import signal
import threading
import time


def when_ready(server):
    """Called after the gunicorn master is ready to handle requests.

    Terminates the health-check stub so gunicorn takes sole ownership of
    the port.  A 1-second delay gives the single eventlet worker enough
    time to fully initialise its green-thread loop before we pull the stub.
    """
    stub_pid_str = os.environ.get("HEALTH_STUB_PID", "")
    if not stub_pid_str:
        return

    def _kill_stub():
        time.sleep(1)
        try:
            os.kill(int(stub_pid_str), signal.SIGTERM)
        except (ValueError, OSError):
            pass

    t = threading.Thread(target=_kill_stub, daemon=True)
    t.start()
