"""Start the BloodLink demo services and dashboard without Docker."""

import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
SERVICES = (
    ("Inventory Service", ROOT_DIR / "services" / "inventory_service" / "app.py", 5001),
    ("Request Service", ROOT_DIR / "services" / "request_service" / "app.py", 5002),
    ("Notification Service", ROOT_DIR / "services" / "notification_service" / "app.py", 5003),
)


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def log_message(self, format, *args):
        pass


def validate_files():
    missing = [str(path.relative_to(ROOT_DIR)) for _, path, _ in SERVICES if not path.is_file()]
    if not (FRONTEND_DIR / "index.html").is_file():
        missing.append("frontend/index.html")
    if missing:
        raise FileNotFoundError("Project files are missing: " + ", ".join(missing))


def wait_until_healthy(name, port, process, timeout=20):
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name} stopped during startup (exit code {process.returncode}).")
        try:
            response = requests.get(url, timeout=0.5)
            if response.ok and response.json().get("status") == "healthy":
                print(f"[LAUNCHER] {name} is healthy at {url}")
                return
        except (requests.RequestException, ValueError):
            time.sleep(0.25)
    raise TimeoutError(f"{name} did not become healthy within {timeout} seconds ({url}).")


def main():
    try:
        validate_files()
    except FileNotFoundError as error:
        print(f"[LAUNCHER] ERROR: {error}", file=sys.stderr)
        return 1

    print("=" * 65)
    print(" BloodLink: Distributed Blood Inventory Coordination System")
    print(" Starting Local Microservices Cluster")
    print("=" * 65)
    processes = []
    frontend_server = None
    try:
        for name, script, port in SERVICES:
            env = os.environ.copy()
            env.update(PORT=str(port), ENABLE_RABBITMQ="false")
            if name == "Request Service":
                env["INVENTORY_SERVICE_URL"] = "http://127.0.0.1:5001"
                env["NOTIFICATION_SERVICE_URL"] = "http://127.0.0.1:5003"
            print(f"[LAUNCHER] Starting {name} on port {port}...")
            proc = subprocess.Popen([sys.executable, str(script)], cwd=str(script.parent), env=env)
            processes.append((name, port, proc))
            wait_until_healthy(name, port, proc)

        frontend_server = ThreadingHTTPServer(("127.0.0.1", 8080), FrontendHandler)
        threading.Thread(target=frontend_server.serve_forever, daemon=True).start()
        print("\nAll services are online.")
        print(" - Dashboard:             http://localhost:8080")
        print(" - Inventory Service:     http://localhost:5001/health")
        print(" - Request Service:       http://localhost:5002/health")
        print(" - Notification Service:  http://localhost:5003/health")
        print("Press Ctrl+C to shut down all services.\n")
        webbrowser.open("http://localhost:8080")
        while True:
            for name, _, proc in processes:
                if proc.poll() is not None:
                    raise RuntimeError(f"{name} stopped unexpectedly (exit code {proc.returncode}).")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[LAUNCHER] Shutting down...")
    except (OSError, RuntimeError, TimeoutError) as error:
        print(f"\n[LAUNCHER] ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        if frontend_server is not None:
            frontend_server.shutdown()
            frontend_server.server_close()
        for name, _, proc in reversed(processes):
            if proc.poll() is None:
                print(f"[LAUNCHER] Stopping {name}...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
