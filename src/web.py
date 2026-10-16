"""Local web UI server for JobTalk using Python standard library http.server."""
from __future__ import annotations

import email
import json
import tempfile
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jobtalk.config import Settings
from jobtalk.extract import stream_extract_job, warmup_model
from jobtalk.models import Job, LineItem
from jobtalk.pricing import apply_rates, compute_totals
from jobtalk.render import render_quote
from jobtalk.transcribe import transcribe

_HTML_TEMPLATE = (Path(__file__).parent / "templates" / "app.html").read_text(encoding="utf-8")


class AppContext:
    def __init__(
        self,
        settings: Settings,
        rates: dict,
        business: str = "Your Business",
        tax_rate: float = 0.0,
        stream_extract_fn: Any = None,
        transcribe_fn: Any = None,
    ) -> None:
        self.settings = settings
        self.rates = rates
        self.business = business
        self.tax_rate = tax_rate
        self.model_ready: bool = False
        self.model_warmup_error: str | None = None
        self.stream_extract_fn = stream_extract_fn or stream_extract_job
        self.transcribe_fn = transcribe_fn or transcribe


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], bytes | None, str | None]:
    """Parse multipart/form-data using stdlib email parser."""
    msg = email.message_from_bytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    fields: dict[str, str] = {}
    file_bytes: bytes | None = None
    filename: str | None = None

    if msg.is_multipart():
        for part in msg.get_payload():
            name = part.get_param("name", header="Content-Disposition")
            part_filename = part.get_filename()
            payload = part.get_payload(decode=True)
            if not payload:
                continue

            if part_filename or name in ("audio", "file"):
                file_bytes = payload
                filename = part_filename or "recording.wav"
            elif name:
                fields[name] = payload.decode("utf-8", errors="replace")
    return fields, file_bytes, filename


def create_handler(ctx: AppContext) -> type[BaseHTTPRequestHandler]:
    class JobTalkHandler(BaseHTTPRequestHandler):
        server_version = "JobTalkHTTP/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            # Suppress default noisy console logs during tests/normal requests
            pass

        def _send_json(self, status: int, data: dict | list) -> None:
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)

        def _send_html(self, status: int, html_text: str) -> None:
            raw = html_text.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            url_path = self.path.split("?")[0]
            if url_path == "/" or url_path == "/index.html":
                self._send_html(200, _HTML_TEMPLATE)
            elif url_path == "/api/status":
                self._send_json(200, {
                    "model_ready": ctx.model_ready,
                    "model": ctx.settings.llm_model,
                    "whisper_model": ctx.settings.whisper_model,
                    "business": ctx.business,
                    "tax": ctx.tax_rate,
                    "error": ctx.model_warmup_error,
                })
            else:
                self.send_error(404, "Not Found")

        def do_POST(self) -> None:
            url_path = self.path.split("?")[0]
            content_len = int(self.headers.get("Content-Length", 0))
            content_type = self.headers.get("Content-Type", "")
            body = self.rfile.read(content_len) if content_len > 0 else b""

            if url_path == "/api/recalculate":
                self._handle_recalculate(body)
            elif url_path == "/api/export":
                self._handle_export(body)
            elif url_path == "/api/extract":
                self._handle_extract(content_type, body)
            else:
                self.send_error(404, "Not Found")

        def _handle_recalculate(self, body: bytes) -> None:
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:  # noqa: BLE001
                self._send_json(400, {"error": "Invalid JSON body"})
                return

            raw_items = data.get("items", [])
            custom_rates = data.get("rates") or ctx.rates
            tax_rate = float(data.get("tax_rate", ctx.tax_rate))

            items: list[LineItem] = []
            for it in raw_items:
                up = it.get("unit_price")
                items.append(LineItem(
                    description=str(it.get("description", "")),
                    kind="labor" if str(it.get("kind", "")).lower() == "labor" else "material",
                    qty=float(it.get("qty") or 1.0),
                    unit_price=float(up) if up is not None and up != "" else None,
                ))

            job = Job(items=items)
            job = apply_rates(job, custom_rates)
            totals = compute_totals(job, tax_rate)

            labor_tot = round(sum(it.total for it in job.items if it.kind == "labor" and it.total is not None), 2)
            mat_tot = round(sum(it.total for it in job.items if it.kind == "material" and it.total is not None), 2)

            items_out = [
                {
                    "description": it.description,
                    "kind": it.kind,
                    "qty": it.qty,
                    "unit_price": it.unit_price,
                    "total": it.total,
                }
                for it in job.items
            ]

            self._send_json(200, {
                "items": items_out,
                "totals": {
                    "labor": labor_tot,
                    "materials": mat_tot,
                    "subtotal": totals.subtotal,
                    "tax": totals.tax,
                    "total": totals.total,
                    "unpriced": totals.unpriced,
                },
            })

        def _handle_export(self, body: bytes) -> None:
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:  # noqa: BLE001
                self._send_json(400, {"error": "Invalid JSON body"})
                return

            raw_items = data.get("items", [])
            custom_rates = data.get("rates") or ctx.rates
            tax_rate = float(data.get("tax_rate", ctx.tax_rate))
            allow_unpriced = bool(data.get("allow_unpriced", False))
            business = str(data.get("business", ctx.business))

            items: list[LineItem] = []
            for it in raw_items:
                up = it.get("unit_price")
                items.append(LineItem(
                    description=str(it.get("description", "")),
                    kind="labor" if str(it.get("kind", "")).lower() == "labor" else "material",
                    qty=float(it.get("qty") or 1.0),
                    unit_price=float(up) if up is not None and up != "" else None,
                ))

            job = Job(
                customer=str(data.get("customer", "")),
                address=str(data.get("address", "")),
                job_summary=str(data.get("job_summary", "")),
                notes=str(data.get("notes", "")),
                items=items,
            )
            job = apply_rates(job, custom_rates)
            totals = compute_totals(job, tax_rate)

            if totals.unpriced > 0 and not allow_unpriced:
                self._send_json(400, {
                    "error": (
                        f"{totals.unpriced} unpriced item(s) must be priced or "
                        "explicitly accepted before export."
                    )
                })
                return

            quote_no = date.today().strftime("%y%m%d") + "-1"
            rendered_html = render_quote(job, totals, business, tax_rate, quote_no)
            self._send_html(200, rendered_html)

        def _handle_extract(self, content_type: str, body: bytes) -> None:
            # Set up Server-Sent Events headers
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.close_connection = True

            def send_event(event: str, payload: dict) -> None:
                msg = f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()
                self.wfile.write(msg)
                self.wfile.flush()

            transcript = ""
            user_rates = ctx.rates

            try:
                if "multipart/form-data" in content_type:
                    fields, audio_bytes, filename = _parse_multipart(content_type, body)
                    if not audio_bytes:
                        send_event("error", {"error": "No audio file provided in upload."})
                        return

                    if "rates" in fields:
                        try:
                            user_rates = json.loads(fields["rates"])
                        except (json.JSONDecodeError, TypeError):
                            user_rates = ctx.rates

                    suffix = Path(filename or "temp.wav").suffix or ".wav"
                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                        tmp.write(audio_bytes)
                        tmp_path = Path(tmp.name)

                    try:
                        transcript = ctx.transcribe_fn(tmp_path, ctx.settings)
                    finally:
                        tmp_path.unlink(missing_ok=True)

                elif content_type.startswith("audio/"):
                    suffix = ".wav"
                    if "webm" in content_type:
                        suffix = ".webm"
                    elif "mp3" in content_type:
                        suffix = ".mp3"
                    elif "m4a" in content_type:
                        suffix = ".m4a"

                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                        tmp.write(body)
                        tmp_path = Path(tmp.name)

                    try:
                        transcript = ctx.transcribe_fn(tmp_path, ctx.settings)
                    finally:
                        tmp_path.unlink(missing_ok=True)

                else:  # Assume JSON
                    data = json.loads(body.decode("utf-8"))
                    transcript = data.get("transcript", "")
                    user_rates = data.get("rates") or ctx.rates

                # 1. Emit transcript event immediately
                send_event("transcript", {"transcript": transcript})

                # 2. Stream extraction line items
                full_json_str = ""
                for kind, payload in ctx.stream_extract_fn(transcript, user_rates, ctx.settings):
                    if kind == "item":
                        item: LineItem = payload  # type: ignore[assignment]
                        send_event("item", {
                            "item": {
                                "description": item.description,
                                "kind": item.kind,
                                "qty": item.qty,
                                "unit_price": item.unit_price,
                            }
                        })
                    elif kind == "done":
                        full_json_str = str(payload)

                # 3. Emit done event with complete Job and pricing totals
                job_data = json.loads(full_json_str) if full_json_str else {}
                job = Job.from_dict(job_data)
                job = apply_rates(job, user_rates)
                totals = compute_totals(job, ctx.tax_rate)
                labor_tot = round(sum(it.total for it in job.items if it.kind == "labor" and it.total is not None), 2)
                mat_tot = round(sum(it.total for it in job.items if it.kind == "material" and it.total is not None), 2)

                send_event("done", {
                    "job": {
                        "customer": job.customer,
                        "address": job.address,
                        "job_summary": job.job_summary,
                        "notes": job.notes,
                        "items": [
                            {
                                "description": it.description,
                                "kind": it.kind,
                                "qty": it.qty,
                                "unit_price": it.unit_price,
                                "total": it.total,
                            }
                            for it in job.items
                        ],
                    },
                    "totals": {
                        "labor": labor_tot,
                        "materials": mat_tot,
                        "subtotal": totals.subtotal,
                        "tax": totals.tax,
                        "total": totals.total,
                        "unpriced": totals.unpriced,
                    },
                })

            except Exception as exc:  # noqa: BLE001
                send_event("error", {"error": str(exc)})

    return JobTalkHandler


def run_server(
    host: str = "0.0.0.0",
    port: int = 5000,
    settings: Settings | None = None,
    rates: dict | None = None,
    business: str = "Your Business",
    tax_rate: float = 0.0,
    warmup: bool = True,
    stream_extract_fn: Any = None,
    transcribe_fn: Any = None,
) -> ThreadingHTTPServer:
    if settings is None:
        settings = Settings()
    if rates is None:
        rates = {}

    ctx = AppContext(
        settings=settings,
        rates=rates,
        business=business,
        tax_rate=tax_rate,
        stream_extract_fn=stream_extract_fn,
        transcribe_fn=transcribe_fn,
    )

    if warmup:
        def _do_warmup() -> None:
            ok = warmup_model(ctx.settings)
            if ok:
                ctx.model_ready = True
            else:
                ctx.model_warmup_error = f"Could not reach Ollama at {ctx.settings.ollama_host}"

        warmup_thread = threading.Thread(target=_do_warmup, daemon=True)
        warmup_thread.start()

    handler_cls = create_handler(ctx)
    server = ThreadingHTTPServer((host, port), handler_cls)
    return server
