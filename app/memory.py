"""Cognee memory: the Checker's experience — vendor history, incidents, human decisions, policy."""
import json
import logging

from . import config  # noqa: F401  (loads .env before cognee)
import cognee
from cognee import SearchType

DATASET = "ap_memory"
log = logging.getLogger(__name__)


def _history_documents() -> list[str]:
    vendors = json.loads((config.DATA / "vendors.json").read_text(encoding="utf-8"))
    history = json.loads((config.DATA / "history.json").read_text(encoding="utf-8"))
    # One document per vendor keeps cognify's LLM calls low (Gemini free tier is rate limited)
    docs = [(config.DATA / "policy.md").read_text(encoding="utf-8")]
    for vid, v in vendors.items():
        lines = [
            f"Vendor {v['name']} (vendor id {vid}) is an approved supplier of Northwind Labs. "
            f"Its genuine email domain is {v['email_domain']}. Its registered payout account is {v['payout_account']}. "
            f"Contract {v['contract']['id']} covers {v['contract']['description']} with a per-invoice cap of £{v['contract']['per_invoice_cap_gbp']}."
        ]
        lines += [
            f"On {p['date']} Northwind Labs paid {v['name']} £{p['amount_gbp']} for invoice {p['invoice']}, approved by {p['approved_by']}."
            for p in history["payments"] if p["vendor_id"] == vid
        ]
        lines += [
            f"Security incident on {i['date']} involving {v['name']}: {i['summary']}"
            for i in history["incidents"] if i["vendor_id"] == vid
        ]
        docs.append("\n".join(lines))
    return docs


async def seed() -> None:
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(metadata=True)
    for doc in _history_documents():
        await cognee.add(doc, dataset_name=DATASET)
    await cognee.cognify(datasets=[DATASET])


async def remember(text: str) -> None:
    await cognee.add(text, dataset_name=DATASET)
    await cognee.cognify(datasets=[DATASET])


async def recall(question: str) -> str:
    try:
        results = await cognee.search(
            query_text=question,
            query_type=SearchType.GRAPH_COMPLETION,
            datasets=[DATASET],
        )
    except Exception as e:  # memory must never take the payment pipeline down
        log.warning("cognee recall failed: %s", e)
        return f"(memory unavailable: {e})"
    parts = []
    for r in results or []:
        val = getattr(r, "search_result", r)
        parts.append(val if isinstance(val, str) else json.dumps(val, default=str))
    return "\n".join(parts).strip() or "(no relevant memory)"


async def render_graph() -> str:
    from cognee.api.v1.visualize.visualize import visualize_graph

    path = str(config.STATIC / "graph.html")
    await visualize_graph(destination_file_path=path, dataset=DATASET, full=True)
    return path
