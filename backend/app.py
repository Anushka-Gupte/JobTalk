"""FastAPI backend application for JobTalk."""
from __future__ import annotations

import asyncio
import json
import tempfile
import threading
from collections.abc import AsyncGenerator, Callable
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jobtalk.config import Settings, load_rates
from jobtalk.extract import stream_extract_job, warmup_model
from jobtalk.models import Job, LineItem
from jobtalk.pricing import apply_rates, compute_totals
from jobtalk.render import render_quote
from jobtalk.transcribe import transcribe

DEFAULT_CONFIG: dict[str, Any] = {
    "business_name": "Dave's Plumbing",
    "tax_rate": 0.08,
    "rates_path": "examples/rates.json",
    "model": "qwen2.5:1.5b",
    "host": "127.0.0.1",
    "port": 5000,
    "upload_max_size_mb": 25,
}


def load_app_config(
    config_path: Path | str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    target_path = Path(config_path) if config_path else Path(__file__).parent.parent / "config.yaml"
    if target_path.exists():
        try:
            loaded = yaml.safe_load(target_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except (yaml.YAMLError, OSError):
            loaded = None
    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


def create_app(
    config_path: Path | str | None = None,
    config_overrides: dict[str, Any] | None = None,
    stream_extract_fn: Callable[..., Any] | None = None,
    transcribe_fn: Callable[..., Any] | None = None,
    warmup: bool = False,
) -> FastAPI:
    cfg = load_app_config(config_path, config_overrides)
    rates_file = cfg.get("rates_path")
    app_rates = load_rates(rates_file) if rates_file else {}
    settings = Settings(llm_model=str(cfg.get("model", "qwen2.5:1.5b")))

    extract_fn = stream_extract_fn or stream_extract_job
    audio_transcribe_fn = transcribe_fn or transcribe

    app = FastAPI(title="JobTalk API", version="0.1.0")

    # Local-only CORS policy
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    state = {
        "model_ready": False,
        "model_warmup_error": None,
    }

    if warmup:
        def _warmup_worker() -> None:
            ok = warmup_model(settings)
            if ok:
                state["model_ready"] = True
            else:
                state["model_warmup_error"] = f"Could not reach Ollama at {settings.ollama_host}"

        thread = threading.Thread(target=_warmup_worker, daemon=True)
        thread.start()

    # -------------------------------------------------------------------------
    # API Routes
    # -------------------------------------------------------------------------

    @app.get("/api/health")
    async def get_health() -> dict[str, Any]:
        return {
            "status": "ok",
            "model_ready": state["model_ready"],
            "model": settings.llm_model,
            "whisper_model": settings.whisper_model,
            "business": cfg.get("business_name"),
            "tax": float(cfg.get("tax_rate", 0.08)),
            "error": state["model_warmup_error"],
        }

    @app.post("/api/recalculate")
    async def recalculate(payload: dict[str, Any]) -> dict[str, Any]:
        raw_items = payload.get("items", [])
        custom_rates = payload.get("rates") or app_rates
        tax_rate = float(payload.get("tax_rate", cfg.get("tax_rate", 0.08)))

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

        labor_tot = round(
            sum(it.total for it in job.items if it.kind == "labor" and it.total is not None), 2
        )
        mat_tot = round(
            sum(it.total for it in job.items if it.kind == "material" and it.total is not None), 2
        )

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

        return {
            "items": items_out,
            "totals": {
                "labor": labor_tot,
                "materials": mat_tot,
                "subtotal": totals.subtotal,
                "tax": totals.tax,
                "total": totals.total,
                "unpriced": totals.unpriced,
            },
        }

    @app.post("/api/export")
    async def export_quote(payload: dict[str, Any]) -> HTMLResponse:
        raw_items = payload.get("items", [])
        custom_rates = payload.get("rates") or app_rates
        tax_rate = float(payload.get("tax_rate", cfg.get("tax_rate", 0.08)))
        allow_unpriced = bool(payload.get("allow_unpriced", False))
        business = str(payload.get("business", cfg.get("business_name", "Your Business")))

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
            customer=str(payload.get("customer", "")),
            address=str(payload.get("address", "")),
            job_summary=str(payload.get("job_summary", "")),
            notes=str(payload.get("notes", "")),
            items=items,
        )
        job = apply_rates(job, custom_rates)
        totals = compute_totals(job, tax_rate)

        if totals.unpriced > 0 and not allow_unpriced:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{totals.unpriced} unpriced item(s) must be priced or "
                    "explicitly accepted before export."
                ),
            )

        quote_no = date.today().strftime("%y%m%d") + "-1"
        rendered_html = render_quote(job, totals, business, tax_rate, quote_no)
        return HTMLResponse(content=rendered_html, status_code=200)

    @app.post("/api/extract")
    async def extract_endpoint(request: Request) -> StreamingResponse:
        content_type = request.headers.get("content-type", "")

        transcript = ""
        user_rates = dict(app_rates)

        if "multipart/form-data" in content_type:
            form = await request.form()
            audio = form.get("audio")
            rates_field = form.get("rates")

            if not audio or not hasattr(audio, "read"):
                raise HTTPException(status_code=400, detail="No audio file provided.")

            max_bytes = int(cfg.get("upload_max_size_mb", 25)) * 1024 * 1024
            audio_bytes = await audio.read()
            if len(audio_bytes) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Audio file exceeds maximum size of {cfg.get('upload_max_size_mb', 25)} MB.",
                )

            if rates_field and isinstance(rates_field, str):
                try:
                    user_rates = json.loads(rates_field)
                except (json.JSONDecodeError, TypeError):
                    user_rates = dict(app_rates)

            filename = getattr(audio, "filename", "recording.wav")
            suffix = Path(filename or "recording.wav").suffix or ".wav"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = Path(tmp.name)

            try:
                transcript = audio_transcribe_fn(tmp_path, settings)
            finally:
                tmp_path.unlink(missing_ok=True)

        elif content_type.startswith("application/json"):
            try:
                data = await request.json()
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Invalid JSON body.") from exc
            transcript = str(data.get("transcript", ""))
            if "rates" in data and isinstance(data["rates"], dict):
                user_rates = data["rates"]

        else:
            # Direct binary audio
            max_bytes = int(cfg.get("upload_max_size_mb", 25)) * 1024 * 1024
            body = await request.body()
            if len(body) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Audio upload exceeds {cfg.get('upload_max_size_mb', 25)} MB limit.",
                )

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
                transcript = audio_transcribe_fn(tmp_path, settings)
            finally:
                tmp_path.unlink(missing_ok=True)

        async def event_generator() -> AsyncGenerator[str, None]:
            try:
                # 1. Emit transcript event immediately
                yield f"event: transcript\ndata: {json.dumps({'transcript': transcript})}\n\n"
                await asyncio.sleep(0)

                # 2. Stream line items
                full_json_str = ""
                for kind, payload in extract_fn(transcript, user_rates, settings):
                    if await request.is_disconnected():
                        return

                    if kind == "item":
                        it: LineItem = payload  # type: ignore[assignment]
                        item_dict = {
                            "description": it.description,
                            "kind": it.kind,
                            "qty": it.qty,
                            "unit_price": it.unit_price,
                        }
                        yield f"event: item\ndata: {json.dumps({'item': item_dict})}\n\n"
                        await asyncio.sleep(0)
                    elif kind == "done":
                        full_json_str = str(payload)

                # 3. Emit done event
                job_data = json.loads(full_json_str) if full_json_str else {}
                job = Job.from_dict(job_data)
                job = apply_rates(job, user_rates)
                tax_rate = float(cfg.get("tax_rate", 0.08))
                totals = compute_totals(job, tax_rate)

                labor_tot = round(
                    sum(it.total for it in job.items if it.kind == "labor" and it.total is not None),
                    2,
                )
                mat_tot = round(
                    sum(
                        it.total for it in job.items if it.kind == "material" and it.total is not None
                    ),
                    2,
                )

                done_payload = {
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
                }
                yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

            except Exception as exc:  # noqa: BLE001
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "close"},
        )

    # -------------------------------------------------------------------------
    # Frontend Static Files Mount
    # -------------------------------------------------------------------------
    frontend_dir = Path(__file__).parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app


# Default app instance for uvicorn
app = create_app()
