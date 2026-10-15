"""FastAPI app: maker proposes → Stripe authorises → Checker decides → capture / cancel / hold."""
import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import config
from . import memory, stripe_ops
from .agents import PaymentRequest, llm_review, payer_extract, transcribe
from .checker import evaluate, record_payment

log = logging.getLogger("checker")
app = FastAPI(title="FourEyes")
memory_lock = asyncio.Lock()
AUDIT = config.DATA / "audit.json"


UPLOADS = config.DATA / "uploads.json"


def _uploads() -> list[dict]:
    return json.loads(UPLOADS.read_text(encoding="utf-8")) if UPLOADS.exists() else []


def _invoices() -> list[dict]:
    return json.loads((config.DATA / "invoices.json").read_text(encoding="utf-8")) + _uploads()


def _audit() -> list[dict]:
    return json.loads(AUDIT.read_text(encoding="utf-8")) if AUDIT.exists() else []


def _save_audit(rows: list[dict]) -> None:
    AUDIT.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")


async def _remember(text: str) -> None:
    async with memory_lock:
        try:
            await memory.remember(text)
            await memory.render_graph()
        except Exception as e:
            log.warning("memory write failed: %s", e)


@app.get("/")
def index():
    return FileResponse(config.STATIC / "index.html")


@app.get("/graph")
def graph():
    path = config.STATIC / "graph.html"
    if not path.exists():
        return HTMLResponse("<p style='font-family:sans-serif'>Graph not rendered yet — run seed first.</p>")
    return FileResponse(path)


@app.get("/api/invoices")
def invoices():
    return _invoices()


class NewInvoice(BaseModel):
    sender: str
    label: str = ""
    text: str = ""
    file_b64: str = ""
    mime: str = ""


@app.post("/api/invoices")
async def add_invoice(body: NewInvoice):
    text = body.text.strip()
    if body.file_b64:
        data = base64.b64decode(body.file_b64)
        if body.mime.startswith("text/") or body.mime in ("message/rfc822", ""):
            text = data.decode("utf-8", errors="replace")
        else:
            text = await asyncio.to_thread(transcribe, data, body.mime)
    if not text:
        raise HTTPException(400, "empty invoice")
    rows = _uploads()
    inv = {"id": "up-" + uuid.uuid4().hex[:6], "label": body.label or f"Uploaded invoice from {body.sender}",
           "expected": "?", "from": body.sender, "text": text, "uploaded": True}
    rows.append(inv)
    UPLOADS.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return inv


@app.get("/api/audit")
def audit():
    return _audit()


@app.get("/api/policy")
def policy():
    return {"markdown": (config.DATA / "policy.md").read_text(encoding="utf-8")}


@app.post("/api/process/{invoice_id}")
async def process(invoice_id: str, bg: BackgroundTasks):
    inv = next((i for i in _invoices() if i["id"] == invoice_id), None)
    if inv is None:
        raise HTTPException(404, "unknown invoice")

    req = await asyncio.to_thread(payer_extract, inv)
    pi = await asyncio.to_thread(
        stripe_ops.authorize, req.amount_gbp,
        f"{req.vendor_name_on_invoice} {req.invoice_number}",
        {"invoice": req.invoice_number, "vendor_id": req.vendor_id, "payee_account": req.payee_account},
    )

    async with memory_lock:
        recalled = await memory.recall(
            f"What do we know about {req.vendor_name_on_invoice}, payout account {req.payee_account}, "
            f"and sender {inv['from']}? Include security incidents, the genuine domain and account, "
            f"typical payment amounts and who approved them."
        )
    review = await asyncio.to_thread(llm_review, inv, req, recalled)
    decision = evaluate(inv, req, review)

    if decision.outcome == "APPROVE":
        pi = await asyncio.to_thread(stripe_ops.capture, pi.id)
        record_payment(req, approved_by="auto")
    elif decision.outcome == "BLOCK":
        pi = await asyncio.to_thread(stripe_ops.cancel, pi.id)

    row = {
        "id": uuid.uuid4().hex[:8],
        "at": datetime.now().isoformat(timespec="seconds"),
        "invoice_id": invoice_id,
        "label": inv["label"],
        "from": inv["from"],
        "request": req.model_dump(),
        "decision": decision.to_dict(),
        "review": review.model_dump(),
        "memory": recalled,
        "stripe": {"id": pi.id, "status": pi.status, "url": stripe_ops.dashboard_url(pi.id)},
        "human": None,
    }
    rows = _audit()
    rows.insert(0, row)
    _save_audit(rows)

    # cognify costs several LLM calls; on the free tier only human decisions are written back by default
    if decision.outcome == "BLOCK" and os.getenv("REMEMBER_BLOCKS", "false") == "true":
        bg.add_task(_remember,
                    f"On {row['at'][:10]} the payment checker BLOCKED invoice {req.invoice_number} claiming to be from "
                    f"{req.vendor_name_on_invoice} (sender {inv['from']}) for £{req.amount_gbp}, payee account "
                    f"{req.payee_account}. Violated clauses: {', '.join(decision.violated)}.")
    return row


class HumanDecision(BaseModel):
    approver: str = "Sarah Chen (Finance Lead)"
    note: str = ""


@app.post("/api/approve/{row_id}")
async def approve(row_id: str, body: HumanDecision, bg: BackgroundTasks):
    rows = _audit()
    row = next((r for r in rows if r["id"] == row_id), None)
    if row is None or row["decision"]["outcome"] != "HOLD" or row["human"]:
        raise HTTPException(400, "not a pending hold")
    pi = await asyncio.to_thread(stripe_ops.capture, row["stripe"]["id"])
    req = PaymentRequest(**row["request"])
    record_payment(req, approved_by=body.approver, human_override=True)
    row["stripe"]["status"] = pi.status
    row["human"] = {"action": "approved", "by": body.approver, "note": body.note, "at": datetime.now().isoformat(timespec="seconds")}
    _save_audit(rows)
    bg.add_task(_remember,
                f"On {row['at'][:10]} {body.approver} manually approved £{req.amount_gbp} to {req.vendor_name_on_invoice} "
                f"for invoice {req.invoice_number} although it breached {', '.join(row['decision']['violated'])}. "
                f"Reason given: {body.note or 'none'}.")
    return row


@app.post("/api/reject/{row_id}")
async def reject(row_id: str, body: HumanDecision, bg: BackgroundTasks):
    rows = _audit()
    row = next((r for r in rows if r["id"] == row_id), None)
    if row is None or row["decision"]["outcome"] != "HOLD" or row["human"]:
        raise HTTPException(400, "not a pending hold")
    pi = await asyncio.to_thread(stripe_ops.cancel, row["stripe"]["id"], "requested_by_customer")
    row["stripe"]["status"] = pi.status
    row["human"] = {"action": "rejected", "by": body.approver, "note": body.note, "at": datetime.now().isoformat(timespec="seconds")}
    _save_audit(rows)
    bg.add_task(_remember, f"{body.approver} rejected invoice {row['request']['invoice_number']} from "
                           f"{row['request']['vendor_name_on_invoice']}. Reason: {body.note or 'none'}.")
    return row


@app.post("/api/graph")
async def refresh_graph():
    async with memory_lock:
        await memory.render_graph()
    return {"ok": True}


@app.post("/api/reset")
def reset():
    for name in ("audit.json", "ledger.json", "uploads.json"):
        (config.DATA / name).unlink(missing_ok=True)
    return {"ok": True}
