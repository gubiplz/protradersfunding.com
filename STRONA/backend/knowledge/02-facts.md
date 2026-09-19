# Canonical facts — Pro Traders Funding

Single source of truth for the support agent. Every number here was read out of the
running code, not out of marketing copy. Where the public site disagrees with the
engine, that is called out explicitly and the **engine wins**.

If a customer asks something not covered here, say you are checking with the team and
escalate. Never fill a gap with a plausible guess — the gaps in this file are marked,
and a marked gap is safe. An invented answer about money is not.

---

## 1. What the company actually is

- Pro Traders Funding operates a **trading skills evaluation service**.
- It is **not** a broker, investment firm, fund manager or financial adviser, and it does
  not accept deposits or execute real-market transactions. (Terms §1)
- **Every account is a demo account with virtual funds** on MetaTrader 5 infrastructure.
  No real money is traded and no order reaches a real market. (Terms §2)
- Words like "capital", "balance", "profit" and "drawdown" describe values **inside the
  simulation**. Payments to traders are **performance-based rewards** calculated from
  simulated results — not proceeds of real trading. (Terms §2)
- Contact: `support@protradersfunding.com`. Site: `protradersfunding.com`.

## 2. The two models

|                        | 2-Step Evaluation | Instant Funding |
|------------------------|-------------------|-----------------|
| Profit target Phase 1  | +10%              | none — funded from day one |
| Profit target Phase 2  | +5%               | — |
| Max daily loss         | 5%                | 5% |
| Max overall loss       | 10%               | 8% |
| Drawdown type          | static            | static |
| Minimum trading days   | 5                 | 30 (see §9, gap 3) |
| Profit split           | **90%**           | **70%** |
| Leverage               | up to 1:100       | up to 1:100 |
| Weekend trading        | $199 add-on       | $199 add-on |
| Fee refunded           | with first payout | with first payout |

Instant Funding accounts are created with `phase="funded", status="funded"` — funded
immediately on purchase, no evaluation.

## 3. Price list (one-time, USD)

| Size | 2-Step | Instant |
|------|--------|---------|
| $25,000    | $299   | $369   |
| $50,000    | $349   | $529   |
| $100,000   | $549   | $829   |
| $200,000   | $1,049 | $1,569 |
| $300,000   | $1,499 | $2,249 |
| $400,000   | $1,999 | $2,999 |
| $800,000   | $2,999 | $4,499 |
| $1,000,000 | $3,499 | $5,249 |
| $2,000,000 | $5,999 | $8,999 |

$25,000 is the entry size — there is no $10,000 plan and **no free trial**.
$100,000 is flagged "Best value" in the shop.

## 4. How the rules are actually measured

This is the part customers dispute. Be exact.

**Overall (max) drawdown — static, never trailing.**
Floor = starting balance − (max overall loss % × starting balance). It is fixed for the
life of the account and never moves up as you profit.
Example, $100,000 2-Step at 10%: floor is **$90,000**, permanently.

**Daily loss.**
Floor = **your equity at the start of the server day** − (daily loss % × **starting
balance**).
The subtracted amount is a percentage of the *starting balance*, not of today's equity.
Example, $100,000 account at 5%, day opens at $102,000:
floor = 102,000 − 5,000 = **$97,000**.

> The public FAQ shows $96,900 for this exact example. **That is wrong** — it takes 5% of
> the day-start equity instead of the starting balance. The engine fails the account at
> $97,000. See §9, gap 1. Use $97,000. Never quote $96,900.

**What is measured against the floors: EQUITY, including floating P&L.**
An open losing position can breach the account before you close it.
Condition is `equity <= floor` — touching the floor exactly is already a breach.

**What is measured for the profit target: BALANCE (closed trades only).**
Floating profit does not pass a phase; you have to close the trades.

**Trading day** = a server day on which the account held an open position. Counted once
per day.

**Maximum open volume** = 6 lots per $100,000 of account size, minimum 1 lot, rounded to
the nearest 0.5. So $25,000 → 1.5 lots, $100,000 → 6 lots, $1,000,000 → 60 lots.
This is the *combined* volume of all open positions. Enforced only when the volume read
is reliable — a technical failure on our side never fails a trader.

**No time limit.** Evaluations have no deadline. The account stays active as long as the
loss limits are respected.

**Breach types the engine records:** `daily_loss`, `max_drawdown`, `max_lots`,
`time_limit`. On breach the account is marked failed, open positions may be closed, and
the exact rule, time and equity are recorded in the trader's dashboard.

## 5. Payouts

Requirements, all three enforced in code:
1. account status is `funded`,
2. trader's KYC status is `approved`,
3. profit (balance − starting balance) is greater than zero.

- Available share = profit × the plan's split (90% or 70%).
- The trader may request **part or all** of the available share.
- **Methods:** USDT (TRC-20, BEP-20 or Polygon), bank transfer (holder, IBAN, SWIFT/BIC;
  bank name optional), Wise (email).
- **Flow:** `pending` → reviewed by the team → `paid`, or `rejected` with a reason.
- **First payout on an account includes a full refund of the challenge fee paid for that
  account**, added on top of the profit share. Every plan, Instant Funding included.
- After a payout the balance is reduced by the profit consumed; a full payout brings the
  account back to its starting balance.

**No minimum payout amount exists in the code** — any amount above zero can be requested.
**No automated payout schedule exists in the code.** The objectives table publishes
"Bi-weekly" (2-Step) and "Every 7 days" (Instant) as the reward frequency; that is an
operational commitment by the team, not something the software enforces. See §9, gap 4.

## 6. KYC

- Statuses: `none`, `pending`, `approved`, `rejected`.
- Required at the first payout request, not at purchase.
- Data collected: full name, country, date of birth, address, ID type
  (Passport / National ID / Driver's License), ID number.
- Documents: ID front, ID back, proof of residence. JPG, PNG or PDF, **max 5 MB each**.
- Rejection comes with a reason and **the trader can correct and resubmit**.
- Minimum age **18**. (Terms §3)
- **No restricted-country list exists in the code.** Do not tell anyone their country is
  blocked or allowed — escalate.

## 7. Money, refunds, programs

**Payment:** Stripe Checkout, one-time, in USD.

**Refund policy:**
- Refunded: the full fee with your first payout; a cancellation *before* the MT5 account
  has been created; our error (duplicate charge, or an account not provisioned within
  24 hours).
- Not refunded: after credentials have been delivered and trading is possible; after
  failing a challenge; accounts terminated for prohibited conduct.
- With a coupon, a refund equals the amount **actually paid**, not the list price.
- Approved refunds return to the original payment method, normally **5–10 business days**.
- Chargebacks: ask the customer to contact us first. An unwarranted chargeback leads to
  termination.

**Scaling:** every time a funded account grows **+15%**, the trader chooses — take the
reward, *or* move up to the next size. Mutually exclusive: moving up applies the profit
to the larger account and pays no reward for that period. A new simulated account is
created at the larger size, funded from the start, under that plan's objectives and
split. No application process. Available up to $2,000,000.

**Coupons:** `WELCOME10` −10%, `VIP20` −20%, `BLACKFRIDAY` −30%. `LUCKY10` / `LUCKY15`
are personal, time-limited rewards from the in-portal daily reveal and only work for the
trader who won them. There is also an "Upgrade Your Size" promo which, while active,
gives the next size up for the same fee.
**Do not invent codes and do not offer one that is not on this list.**

**Affiliate:** 10% commission on paid orders from referred traders, tracked by a unique
referral code and shown in the affiliate panel.
**No payout threshold and no automated commission payout exist in the code** — do not
promise when or how commission is paid. Escalate. See §9, gap 6.

## 8. Platform, delivery, conduct

- **MetaTrader 5.** Credentials work on desktop, mobile and the web terminal.
- Provisioning is automated; credentials usually arrive **within a minute** of checkout
  and appear in the dashboard and by email.
- Markets: Forex, Indices, Gold & Metals, Crypto CFDs.
- **Allowed:** news trading, weekend holding, expert advisors. No strategy bans, no news
  blackouts, no weekend-flat requirement.
- **Prohibited (Terms §6):** exploiting demo-environment artifacts (off-quotes, latency,
  feed errors); buying, selling or transferring accounts; letting someone else trade
  outside an approved arrangement; fraud, chargeback abuse, identity misrepresentation,
  KYC falsification; attacking or reverse-engineering the platform.
- **Managed accounts (Terms §7):** allowed **with written approval obtained before any
  trading on the trader's behalf begins**. Request from the registered email address,
  naming the person or company. The account stays registered to the trader, who remains
  responsible — a limit breached by the manager is still a breach. Rewards are paid only
  to the verified account holder after KYC; we never pay managers. Approval can be
  withdrawn at any time. Undisclosed credential sharing stays prohibited.
- Certificates are issued for every passed evaluation and funded account, each with a
  unique ID anyone can check at `/verify`.
- One person holds one registration; running several challenges under it is allowed, and
  each account is evaluated independently.
- Failing has no consequence beyond that account ending — a new challenge can be started
  at any time. **There is no automatic discount or free retry after a failure.**

## 9. Known gaps and contradictions — READ BEFORE ANSWERING

These are places where the public site and the engine disagree, or where the site
promises something no code enforces. Until the owner resolves them, follow the guidance
in bold.

1. **FAQ daily-loss example is wrong by $100.** FAQ: floor $96,900. Engine: $97,000
   (verified by running the risk engine). The error is against the customer — the account
   fails $100 earlier than the FAQ promises. **Always quote the engine's math. If a
   customer cites the FAQ figure and was failed between the two numbers, escalate
   immediately — do not argue, that is a legitimate complaint.**

2. **FAQ says minimum trading days are "typically 3–4".** The plans actually sold enforce
   **5** (2-Step). The 3–4 figure comes from unused legacy presets. **Say 5.**

3. **Instant Funding advertises 30 minimum trading days, but nothing enforces it.** The
   account is funded from day one and a payout request only checks funded status, KYC and
   profit. **Do not tell a customer they must wait 30 days, and do not tell them they can
   ignore it. If asked directly, escalate.**

4. **Reward frequency ("Bi-weekly" / "Every 7 days") is published but not enforced by any
   scheduler.** Treat it as the team's service commitment. **Do not promise a specific
   payment date.**

5. **The objectives table calls the drawdown "Balance-based" while the FAQ calls it
   "static".** Same thing — a fixed floor derived from the starting balance. But the
   breach is measured on **equity**, so "balance-based" misleads. **Explain it as
   §4 does.**

6. **Affiliate commission has no threshold, schedule or payout mechanism in code.**
   **State the 10% rate, escalate anything about receiving the money.**

7. **No minimum payout amount and no country restrictions exist in code.** If asked,
   say there is no minimum; for countries, escalate.
