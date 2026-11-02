# Metric definitions

Implemented in `backend/app/analytics/metrics.py` and `service.py`. The dashboard and the
AI copilot both use these. Nothing else may compute them differently.

| Metric                  | Definition                                                                 |
| ----------------------- | -------------------------------------------------------------------------- |
| Revenue                 | Σ (quantity × unit price) over order lines, **excluding tax**              |
| Counted orders          | status `confirmed` or `completed`. `pending`, `cancelled`, `returned` are excluded |
| Period                  | an order belongs to the period containing its `order_date`                  |
| Orders                  | count of distinct counted orders                                            |
| Average order value     | revenue ÷ orders (blank when there are no orders)                           |
| Active customers        | distinct customers with ≥1 counted order in the period                      |
| Target                  | sum of monthly targets for every month the period touches (full-month targets, so mid-month achievement is "so far") |
| Achievement %           | revenue ÷ target × 100                                                       |
| Target gap              | target − revenue (negative = target exceeded)                               |
| Required daily run rate | gap ÷ days left in the month including today; only for a single, in-progress month |
| Growth %                | (current − previous) ÷ previous × 100; blank when previous is 0, or when the comparison period starts before the month the organisation's data begins (it would compare against "no data") |
| Comparison period       | month-to-date/whole month → same days of the previous month; otherwise the equal-length window just before |

Targets are set per salesperson per month, so they are **not shown** when filtering by
product, customer segment or territory.

Import status mapping: `delivered`, `invoiced`, `closed`, `paid` → completed;
`open`, `new` → pending; `canceled` → cancelled. Dates are read day-first (03/04/2026 = 3 April).

## Customer health (`app/analytics/insights.py`)

Judged as of the period end, using each customer's whole order history up to then.

| Status   | Rule                                                                                       |
| -------- | ------------------------------------------------------------------------------------------ |
| Dormant  | no counted order for **60+ days**                                                          |
| At risk  | days since last order > **1.5 ×** the customer's typical gap, and at least **14 days**     |
| Active   | otherwise                                                                                  |

* **Typical gap** = average days between distinct order dates; needs 3+ orders, otherwise the
  median typical gap of all customers is used.
* **Monthly value** = revenue in the 182 days up to the last order ÷ 6. "At stake" sums it for
  at-risk and dormant customers.
* **New** in period = first ever order falls in the period; **returning** = ordered in the period
  and before it. Repeat rate = returning ÷ (new + returning).

## Product trends

Compares the period with the comparison period (same rule as growth %). A product is **rising** /
**declining** at ±15% or more, **steady** in between, **new** if it had no sales before, **stopped**
if it had sales before and none now. No labels when the comparison period predates the data or
the period is **shorter than 14 days** (too noisy); the raw change is still shown.

## Pipeline

* Open deals are a **snapshot as of today**; won, lost, win rate (won ÷ (won + lost)), average won
  deal and average cycle (open → won days) cover deals **closed in the period**.
* **Weighted pipeline** = value × fixed stage weight (prospecting 10%, qualification 25%,
  proposal 50%, negotiation 75%). These are conventions, not predictions.
* **Stalled** = open with no completed activity on the deal for more than 21 days (counting from
  when it opened if there has been none). **Overdue** = expected close date has passed.

## Team activity

* Counts completed activities in the period. **Follow-ups** are planned activities; overdue once
  their date has passed. Marking one done records it as done today.
* **Visit → order** = share of visits followed by an order from the same customer within 7 days.
