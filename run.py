"""One-command runner for JobTalk: checks Ollama, downloads model, starts server, opens browser."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

import yaml


def check_ollama_running(host: str = "http://localhost:11434") -> tuple[bool, list[str]]:
    """Check if Ollama is accessible and return list of installed model names."""
    try:
        req = urllib.request.Request(f"{host}/api/tags")
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            return True, models
    except Exception:  # noqa: BLE001
        return False, []


def ensure_model_installed(model_name: str, installed_models: list[str]) -> bool:
    """Ensure the required model is pulled in Ollama."""
    # Check if model exists with or without :latest tag
    normalized_target = model_name if ":" in model_name else f"{model_name}:latest"
    is_installed = any(
        m == model_name or m == normalized_target or m.startswith(f"{model_name}:")
        for m in installed_models
    )
    if is_installed:
        return True

    print(f"\n[JobTalk] Model '{model_name}' not found locally.")
    print(f"[JobTalk] Pulling '{model_name}' via Ollama (one-time download)...")
    try:
        res = subprocess.run(["ollama", "pull", model_name], check=True)
        return res.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        print(f"[JobTalk] Failed to pull model via 'ollama pull {model_name}': {exc}", file=sys.stderr)
        return False


def load_config(config_path: Path) -> dict[str, Any]:
    defaults = {
        "model": "qwen2.5:1.5b",
        "host": "127.0.0.1",
        "port": 5000,
        "business_name": "Dave's Plumbing",
        "tax_rate": 0.08,
        "rates_path": "examples/rates.json",
    }
    if config_path.exists():
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                defaults.update(loaded)
        except Exception as exc:  # noqa: BLE001
            print(f"[JobTalk] Warning: Could not parse {config_path}: {exc}", file=sys.stderr)
    return defaults


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-command runner: check Ollama, download model, start server, open browser."
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="Bind to 0.0.0.0 for phone/tablet access on Wi-Fi (NO authentication!)",
    )
    parser.add_argument("--port", type=int, default=None, help="Override server port")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config))
    model_name = str(cfg.get("model", "qwen2.5:1.5b"))
    port = args.port or int(cfg.get("port", 5000))
    bind_host = "0.0.0.0" if args.lan else "127.0.0.1"

    if args.lan:
        print("\n" + "!" * 70)
        print("[WARNING] Running with --lan bound to 0.0.0.0.")
        print("[WARNING] JobTalk has NO authentication! Anyone on your Wi-Fi can access it.")
        print("!" * 70 + "\n")

    # Step 1: Check Ollama connection
    print("[JobTalk] Checking local Ollama connection on http://localhost:11434 ...")
    ollama_ok, models = check_ollama_running()
    if not ollama_ok:
        print("\n" + "=" * 65, file=sys.stderr)
        print("ERROR: Could not connect to local Ollama service at http://localhost:11434", file=sys.stderr)
        print("Ollama is required for local-first quote extraction.", file=sys.stderr)
        print("\nTo fix this:", file=sys.stderr)
        print("  1. Ensure Ollama is installed (download: https://ollama.com)", file=sys.stderr)
        print("  2. Start the Ollama app or run 'ollama serve' in a terminal.", file=sys.stderr)
        print("=" * 65 + "\n", file=sys.stderr)
        return 1

    # Step 2: Ensure default model is downloaded
    print(f"[JobTalk] Verifying model '{model_name}' ...")
    if not ensure_model_installed(model_name, models):
        print(f"[JobTalk] Warning: Could not verify '{model_name}'. Will attempt startup anyway.", file=sys.stderr)

    # Step 3: Launch browser
    url = f"http://127.0.0.1:{port}"
    if not args.no_browser:
        def _open_tab() -> None:
            webbrowser.open(url)
        threading.Timer(1.2, _open_tab).start()

    print(f"\n[JobTalk] Starting server on http://{bind_host}:{port}")
    print(f"[JobTalk] Open in browser: {url}")
    print("[JobTalk] Press Ctrl+C to stop.\n")

    # Step 4: Run uvicorn server
    try:
        import uvicorn

        from backend.app import create_app

        app_instance = create_app(
            config_path=Path(args.config),
            config_overrides={"port": port, "host": bind_host},
            warmup=True,
        )
        uvicorn.run(app_instance, host=bind_host, port=port, log_level="info")
    except KeyboardInterrupt:
        print("\n[JobTalk] Shutting down.")
    except Exception as exc:  # noqa: BLE001
        print(f"[JobTalk] Server error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
