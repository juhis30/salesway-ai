# Predictive models (Phase 4)

SaleSway predicts four things and suggests next actions. The rule throughout: **a model is only
used if, tested on past data it never learned from, it beats the simpler method already in place.**
Every range and percentage shown to users traces back to counted past errors.

Code: `backend/app/ml/`. Train with `python -m app.ml.train` (all organisations), the **Retrain
now** button on the Forecast page (admins and analysts), or `POST /api/v1/ml/train`. The seed
trains automatically. Each run creates a new model version; the previous active one is kept as
"retired" history.

## Month-end sales forecast (`forecast.py`)

Three methods compete:

| Method | Prediction for the month |
| --- | --- |
| Pace (today's rule) | sales so far ÷ days elapsed × days in month |
| Weekday profile | sales so far + for each remaining day, the average of the last 8 same weekdays |
| Recent run rate | sales so far + remaining days × average daily sales of the last 28 days |

**Back-test:** for every complete month after an 8-week warm-up, and as of days 3, 7, 10, 14, 17,
21, 24 and 28, each method forecasts the month total using only data up to that day. The method with
the lowest mean absolute percentage error is chosen. Needs at least 10 such forecasts (about 2
months after the warm-up).

**Range and chance of target:** the chosen method's past errors at the same stage of the month
(early / mid / late) are applied to today's forecast. The range covers 8 in 10 past outcomes
(10th–90th percentile). "Chance of reaching target" is the share of past outcomes that would reach
it. Company-wide views use the company's errors; a team or one person uses the pooled
individual errors, which are wider because one person's sales vary more.

Demo result (2 Nov 2026): weekday profile chosen, 6.4% average error vs 8.2% for pace; forecast
₹1.84 Cr, likely ₹1.58–2.22 Cr, 38% chance of reaching the ₹1.92 Cr target.

**Next month** uses last month / 3-month average / 6-month trend, chosen the same way, but with only
a handful of past months to test on it is shown as a rough guide with the full spread of past outcomes.

## Customer churn (`churn.py`)

*Churn* = an active customer places no order in the next 60 days. Training examples are snapshots
every 14 days: features from orders up to the snapshot only (days since last order and vs their
usual gap, orders and spend in 90 days, spend trend vs the previous 90 days, tenure, product
range, customer type), labelled by what happened in the next 60 days.

Logistic regression and gradient boosting are evaluated with grouped cross-validation (no customer
appears in both training and test) against the existing rule (days since last order ÷ usual gap).
**Gate:** AUC ≥ 0.65 *and* at least 0.02 better than the rule; at least 15 past examples of churn.

Demo result: gradient boosting AUC 0.878 vs rule 0.867, not clearly better, so **the rule stays in
use** (Customer health page). With real data where churn has subtler causes, a model may win.

## Deal win probability (`deals.py`)

Uses only facts known when the deal was opened: size, source, customer type, whether they already
buy from you, the owner's win rate on deals closed *before* this one, planned length. Logistic
regression, 5-fold cross-validation, compared with the owner's track record alone. Same gate.

Demo result: AUC 0.48 (no better than chance: demo deal outcomes are random), so **stage weights
stay in use**. Real CRM history with stage changes and activity timing would give it more to learn from.

## Next best actions (`recommend.py`)

Refreshed on every training run, per salesperson, at most 3 of a kind and 8 in total, ordered by
kind (promises already made → customers drifting away → stalled deals → new sales) and by value
within a kind:

| Kind | When | Value shown |
| --- | --- | --- |
| Promised follow-up | a planned visit/call is overdue (one per customer) | customer's monthly spend |
| Win back | churn model ≥ 50%, or the at-risk rule when the model is off | monthly spend × probability (or spend) |
| Stalled deal | open, no activity 21+ days | amount × win probability (or stage weight) |
| Cross-sell | ≥ 60% of 5+ similar customers buy a product this one hasn't in 6 months | typical monthly spend × adoption |

Done or dismissed actions are not suggested again for 14 days. Managers see a **Team** tab (top 2
per person); everyone sees their own.

## Where predictions appear

* **Forecast & actions** page: forecast chart and figures, next month, actions, model status.
* **Assistant**: "expected month-end revenue?" / "am I on track?" use the forecast with its range,
  chance and tested error instead of the plain pace, and say which method was used.

## Limits

* Back-testing needs history: no forecast until about 3 complete months; next-month forecasts
  stay rough until there are a couple of years of data (seasonality).
* Models are retrained on demand, not on a schedule (a scheduler arrives with Phase 6).
* No model is stored as a file: the training run scores everything and stores the results, metrics
  and (for logistic regression) coefficients, so nothing executable is read back from the database.
