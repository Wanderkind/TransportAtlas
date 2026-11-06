"""Exercise the actual loopback Uvicorn launcher with an isolated temporary DB."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen


def main():
    root = Path('data/local-verification')
    root.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with TemporaryDirectory(prefix='transport-atlas-startup-') as directory:
        command = [sys.executable, '-m', 'transport_atlas', '--db', str(Path(directory)/'startup.duckdb'), 'serve', '--port', str(port)]
        started = time.perf_counter()
        with (root/'startup.log').open('w', encoding='utf-8') as log:
            proc = subprocess.Popen(command, stdout=log, stderr=log,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:
                deadline = time.monotonic()+90
                while time.monotonic()<deadline:
                    if proc.poll() is not None:
                        raise RuntimeError(f'Backend exited: see {root / "startup.log"}')
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/api/v1/health', timeout=2) as response:
                            health = json.load(response)
                        assert health == {'status':'ok','database_backend':'duckdb'}
                        print(f'Backend startup passed on loopback in {time.perf_counter()-started:.3f}s')
                        return
                    except OSError:
                        time.sleep(0.2)
                raise TimeoutError('Backend startup timed out')
            finally:
                if os.name=='nt':
                    subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=15)
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=15)


if __name__=='__main__':
    main()
