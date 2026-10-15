"""The CHECKER. Hard controls are plain code; the LLM review can only tighten the outcome."""
import json
import re
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .agents import PaymentRequest, Review
from .config import DATA

AUTO_LIMIT = 1000
BASELINE_MULTIPLIER = 2
MONTHLY_BUDGET = 10000
DUPLICATE_WINDOW_DAYS = 30
TODAY = date(2026, 10, 15)

HARD = {"P1", "P2", "P3", "P4"}
TITLES = {
    "P1": "Approved vendor",
    "P2": "Registered payout account",
    "P3": "Contract basis",
    "P4": "No duplicates",
    "P5": "Auto-approval limit",
    "P6": "Historical baseline",
    "P7": "Monthly budget",
    "P8": "Content review",
}


@dataclass
class Check:
    clause: str
    passed: bool
    evidence: str
    title: str = ""

    def __post_init__(self):
        self.title = TITLES[self.clause]


@dataclass
class Decision:
    outcome: str  # APPROVE | HOLD | BLOCK
    checks: list[Check] = field(default_factory=list)

    @property
    def violated(self) -> list[str]:
        return [c.clause for c in self.checks if not c.passed]

    def to_dict(self) -> dict:
        return {"outcome": self.outcome, "violated": self.violated, "checks": [asdict(c) for c in self.checks]}


def _load(name: str):
    path = DATA / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _norm(account: str) -> str:
    return re.sub(r"\s+", "", account or "").upper()


def _paid_records() -> list[dict]:
    """Seed history plus everything captured during this session."""
    return _load("history.json")["payments"] + (_load("ledger.json") or [])


def evaluate(invoice: dict, req: PaymentRequest, review: Review) -> Decision:
    vendors = _load("vendors.json")
    history = _load("history.json")
    vendor = vendors.get(req.vendor_id or "")
    paid = _paid_records()
    checks: list[Check] = []

    # P1 — approved vendor
    checks.append(Check("P1", vendor is not None and vendor["status"] == "active",
                        f"Registry match: {vendor['name']} ({req.vendor_id})" if vendor
                        else f"'{req.vendor_name_on_invoice}' is not in the vendor registry"))
    if vendor is None:
        return Decision("BLOCK", checks)

    # P2 — payout account must match the registry; also cross-check known incidents
    registered = _norm(vendor["payout_account"])
    requested = _norm(req.payee_account)
    incident = next((i for i in history["incidents"] if requested and requested in _norm(i["summary"])), None)
    p2_evidence = (f"Invoice account {req.payee_account} matches registry"
                   if requested == registered
                   else f"Invoice asks for {req.payee_account}; registry has {vendor['payout_account']}")
    if incident:
        p2_evidence += f". This account appears in incident {incident['date']}: {incident['summary'][:140]}…"
    checks.append(Check("P2", requested == registered, p2_evidence))

    # P3 — contract basis and cap
    contract = vendor["contract"]
    on_contract = (req.contract_ref or "").strip().upper() == contract["id"].upper()
    within_cap = req.amount_gbp <= contract["per_invoice_cap_gbp"]
    checks.append(Check("P3", on_contract and within_cap,
                        f"Contract {contract['id']} cap £{contract['per_invoice_cap_gbp']:,}; "
                        f"invoice cites {req.contract_ref or 'nothing'} for £{req.amount_gbp:,.2f}"
                        + ("" if within_cap else " — exceeds cap") + ("" if on_contract else " — wrong/missing contract")))

    # P4 — duplicates
    vendor_paid = [p for p in paid if p["vendor_id"] == req.vendor_id]
    dup_number = next((p for p in vendor_paid if p["invoice"].upper() == req.invoice_number.upper()), None)
    recent = TODAY - timedelta(days=DUPLICATE_WINDOW_DAYS)
    dup_amount = next((p for p in vendor_paid if abs(p["amount_gbp"] - req.amount_gbp) < 0.01
                       and date.fromisoformat(p["date"]) >= recent), None)
    dup = dup_number or dup_amount
    checks.append(Check("P4", dup is None,
                        (f"{req.invoice_number} already paid on {dup['date']} (£{dup['amount_gbp']})" if dup_number
                         else f"Same vendor and amount as {dup['invoice']}, paid on {dup['date']} (£{dup['amount_gbp']}); "
                              f"'{req.invoice_number}' looks like a renamed duplicate") if dup
                        else f"{req.invoice_number} not previously paid"))

    # P5 — auto-approval limit
    checks.append(Check("P5", req.amount_gbp <= AUTO_LIMIT,
                        f"£{req.amount_gbp:,.2f} vs auto-approval limit £{AUTO_LIMIT:,}"))

    # P6 — historical baseline, unless a human already approved this pattern
    amounts = [p["amount_gbp"] for p in vendor_paid]
    median = statistics.median(amounts) if amounts else None
    human_ok = next((p for p in vendor_paid if p.get("approved_by", "auto") != "auto"
                     and p.get("human_override") and p["amount_gbp"] >= req.amount_gbp * 0.9), None)
    if median is None:
        checks.append(Check("P6", False, "No payment history for this vendor"))
    elif req.amount_gbp <= BASELINE_MULTIPLIER * median:
        checks.append(Check("P6", True, f"£{req.amount_gbp:,.2f} within {BASELINE_MULTIPLIER}× median £{median:,.2f}"))
    elif human_ok:
        checks.append(Check("P6", True, f"Above baseline, but {human_ok['approved_by']} approved a similar "
                                        f"£{human_ok['amount_gbp']:,.2f} payment on {human_ok['date']}"))
    else:
        checks.append(Check("P6", False, f"£{req.amount_gbp:,.2f} is {req.amount_gbp / median:.1f}× the median £{median:,.2f}"))

    # P7 — monthly budget
    month_spend = sum(p["amount_gbp"] for p in paid if p["date"].startswith(TODAY.strftime("%Y-%m")))
    checks.append(Check("P7", month_spend + req.amount_gbp <= MONTHLY_BUDGET,
                        f"October spend £{month_spend:,.2f} + £{req.amount_gbp:,.2f} vs budget £{MONTHLY_BUDGET:,}"))

    # P8 — LLM content review (can only tighten)
    p8_ok = not review.injection_detected and review.risk != "high"
    checks.append(Check("P8", p8_ok, f"LLM risk {review.risk}" + (", prompt injection detected" if review.injection_detected else "")
                        + (": " + "; ".join(f.rstrip(". ") for f in review.flags) if review.flags else "")))

    failed = {c.clause for c in checks if not c.passed}
    outcome = "BLOCK" if failed & HARD else "HOLD" if failed else "APPROVE"
    return Decision(outcome, checks)


def record_payment(req: PaymentRequest, approved_by: str, human_override: bool = False) -> None:
    path = DATA / "ledger.json"
    ledger = _load("ledger.json") or []
    ledger.append({"date": TODAY.isoformat(), "vendor_id": req.vendor_id, "invoice": req.invoice_number,
                   "amount_gbp": req.amount_gbp, "approved_by": approved_by, "human_override": human_override})
    path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
