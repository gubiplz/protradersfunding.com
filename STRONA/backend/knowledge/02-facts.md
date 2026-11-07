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
| Minimum trading days   | 5 per phase + 5 on funded before first payout | 30 on funded before first payout |
| Profit split           | **90%**           | **70%** |
| Leverage               | up to 1:100       | up to 1:100 |
| Weekend trading        | $199 add-on       | $199 add-on |
| Fee credited           | with first payout | with first payout |

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

> Live FAQ and the engine both use **$97,000**. Never quote $96,900 from old screenshots.

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

Requirements, all four enforced in code:
1. account status is `funded`,
2. the funded account has the plan's **minimum trading days** before the first payout —
   **5 on 2-Step**, **30 on Instant Funding**. A day counts once the trader trades on it
   (so N trading days is longer than N calendar days). The counter is on the account card
   ("Trading Days X / Y min"). Evaluation-phase days on 2-Step do **not** replace the
   funded wait — the 5 funded days start after the account is funded,
3. trader's KYC status is `approved`,
4. profit (balance − starting balance) is greater than zero, and the requested amount meets
   the **minimum payout** ($500 or 2% of account size, whichever is higher).

- Available share = profit × the plan's split (90% or 70%).
- The trader may request **part or all** of the available share (subject to the minimum).
- **Methods:** **vUSDT on Polygon only** (PTF's native internal token — not Tether USDT).
  Do **not** offer USDT TRC-20/BEP-20, bank transfer, Wise, or cash. Wallet is confirmed when
  the request is unlocked; changing a saved wallet needs an emailed code.
- **Flow:** `pending` → reviewed by the team → unlocked → trader confirms → `paid`, or
  `rejected` with a reason.
- **First payout on an account includes a full credit of the challenge fee paid for that
  account** as additional vUSDT (not a card refund), added on top of the profit share.
  Every plan, Instant Funding included.
- After a payout the balance is reduced by the profit consumed; a full payout brings the
  account back to its starting balance.

**Minimum payout** is enforced in code ($500 or 2% of account size, whichever is higher).
**Reward frequency (default):** every 14 calendar days between requests (bi-weekly) on
2-Step and Instant, enforced via `PAYOUT_INTERVAL_DAYS`. Accounts with the **On-demand
Payout** add-on (`ondemand_payout`) skip that wait. Rewards are paid in **vUSDT on Polygon**.
The Challenge fee credit on first payout is additional vUSDT, not a card refund.
**vUSDT** is PTF's native internal token on Polygon — **not** Tether USDT. Transferable to the
trader's wallet; **no** cash/fiat redemption by PTF; value not guaranteed. Do **not** publish
a contract address. Do not mention Express Payout ($49 / review queue) — that add-on was withdrawn.

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

**Refund / fee-credit policy:**
- **Fee credit (vUSDT):** on the first approved payout from that funded account, the
  challenge fee is credited as additional vUSDT — not a refund to the card.
- **Card refunds:** a cancellation *before* the MT5 account has been created; our error
  (duplicate charge, or an account not provisioned within 24 hours).
- **Not refunded to card:** after credentials have been delivered and trading is
  possible; after failing a challenge; accounts terminated for prohibited conduct.
  (The fee may still be credited later as vUSDT on a first payout if the account is
  funded and a reward is approved.)
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
referral code and shown under Rewards. The trader claims in the portal as **store credit**
or **vUSDT** (saved Polygon wallet). No cash payout and no commission paid by contacting
support. Do not quote a minimum unclaimed amount. Full rules: `/affiliate-terms`.

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

1. **RESOLVED — FAQ daily-loss example matches the engine ($97,000).** Always quote
   $97,000 for the $102,000 day-start / 5% example. Never quote $96,900 if a customer
   cites an old screenshot — correct them gently to the live FAQ / engine math.

2. **RESOLVED — minimum trading days are 5 per 2-Step phase** (not “typically 3–4”).
   **Say 5.** First payout on a funded 2-Step also needs **5 trading days on the funded
   account**; Instant Funding needs **30**.

3. **RESOLVED — Instant Funding's 30 minimum trading days are enforced** on the payout
   request. **Answer from §5: the wait is real, it counts trading days rather than
   calendar days, and the account card shows the score.**

4. **Reward frequency is bi-weekly (14 calendar days between requests) by default**,
   enforced in code. On-demand Payout ($299 2-Step / $399 Instant) removes the wait for
   that account. **Do not promise a specific payment date** for when a request is paid —
   only when the next request may be submitted.

5. **The objectives table calls the drawdown "Balance-based" while the FAQ calls it
   "static".** Same thing — a fixed floor derived from the starting balance. But the
   breach is measured on **equity**, so "balance-based" misleads. **Explain it as
   §4 does.**

6. **RESOLVED — Affiliate claim is in the portal.** Store credit or vUSDT. No cash /
   no support payout. Do not quote a minimum unclaimed amount. Point to `/affiliate-terms`.

7. **RESOLVED — minimum payout is enforced:** $500 or 2% of account size, whichever is
   higher. **No restricted-country list is published or enforced in code.** Public copy
   only says access is subject to applicable sanctions/AML and we may refuse service.
   For country eligibility questions, escalate — do not invent a blocked/allowed list.

8. **RESOLVED — payout method is vUSDT on Polygon only.** Public FAQ/Terms match.
   Do not mention USDT TRC-20/BEP-20, bank transfer, or Wise as payout options.
