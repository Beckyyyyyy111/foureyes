# Accounts Payable Payment Policy — Northwind Labs Ltd

Every payment an AI agent proposes is reviewed by the Checker before funds are captured.
Clauses P1–P4 are hard controls: a breach always BLOCKS the payment.
Clauses P5–P8 are risk controls: a breach HOLDS the payment for human approval.
The LLM review (P8) may only make a decision stricter, never more lenient.

## Hard controls (BLOCK)

- **P1 — Approved vendors only.** The payee must be an active vendor in the vendor registry.
- **P2 — Registered payout account.** Funds may only go to the payout account on file for that vendor.
  Requests to pay a "new" or "updated" account are blocked until the change is verified out-of-band.
- **P3 — Contract basis.** Every payment must reference an active contract or purchase order,
  and the amount must not exceed the contract's per-invoice cap.
- **P4 — No duplicates.** An invoice number may be paid once. The same vendor and amount within
  30 days is treated as a duplicate.

## Risk controls (HOLD for human approval)

- **P5 — Auto-approval limit.** Payments above £1,000 require human approval.
- **P6 — Historical baseline.** Payments more than 2× the vendor's median historical payment
  require human approval, unless a prior human approval covers the same pattern.
- **P7 — Monthly budget.** Total AP spend may not exceed £10,000 per calendar month.
- **P8 — Content review.** Invoices containing instructions aimed at AI systems, artificial urgency,
  or internal inconsistencies are held for human review.
