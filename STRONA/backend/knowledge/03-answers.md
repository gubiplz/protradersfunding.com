# Ready answers

Send these as they are, or adapt. `{braces}` are placeholders you must fill from the
customer's account — never send a brace. Every number traces back to `02-facts.md`.

Answers are grouped the way customers actually think, not the way the product is built.

---

## Before buying

**"What is this exactly? Am I trading real money?"**
> No, and that's by design. Every account is a demo account with virtual funds on MT5
> infrastructure. Prices are live reference prices from third-party sources, so simulated
> fills can differ from what a real broker would have given. You never deposit or risk real
> money. If you meet the
> objectives, you earn performance-based rewards on your simulated results. We're an
> evaluation service, not a broker.

**"Is there a free trial?"**
> No free trial. Challenges start at $299 for a $25,000 account, and it's a single
> payment with no subscription. On 2-Step plans the full fee is credited as additional
> vUSDT with your first payout from the funded account (a credit, not a card refund).
> Instant Funding has no fee credit.

**"2-Step or Instant Funding — which should I take?"**
> Depends on what you'd rather spend: time or money.
> 2-Step is cheaper and pays more — $549 on a $100,000 account and a **90%** split — but
> you prove yourself first: +10%, then +5%, with a 10% overall loss limit.
> Instant Funding skips the evaluation entirely. You're funded from day one at a 70%
> split (80% with the Split Boost add-on), with a tighter 8% overall limit, no fee credit,
> and it costs $829 at the same size.
> Most traders who are confident in their edge take 2-Step for the split and the fee
> credit. Instant is for people who would rather skip the evaluation and accept less per
> dollar.

**"How fast do I get my account?"**
> Provisioning is fully automated: once your payment is confirmed, your MT5 login,
> password and server appear in your dashboard and in your inbox. The dashboard shows live
> status while the account is being created.

**"How do you compare to {competitor}?"**
> I'd rather give you our numbers than talk about theirs: 90% split on evaluation plans,
> the 2-Step fee credited with your first payout (as vUSDT, not a card refund), static
> drawdown only — never trailing, so your floor never chases your profits — and no time
> limit on the evaluation. News trading and weekends are fine, EAs come with the EA
> Allowed add-on, and the only other strategy bans are abuses of the simulation, listed in Section 6 of the Terms. Compare that line by line
> and decide.

**"Do you have a discount?"**
> {If a public code is running: Yes — use **{CODE}** at checkout for {X}% off; the
> discount is applied to the total.}
> {If not: I can't create codes on my side, but there's a daily reveal in the trader
> portal that hands out personal discount codes, and public promotions get announced to
> registered traders first. Worth creating a free account now so you catch the next one.}

**"How do I pay?"**
> Card, through Stripe, in USD. One payment per challenge — no subscription and no
> recurring charges of any kind. Crypto payment is possible on request, and store credit
> is applied at the end of checkout.

**"Can I run more than one challenge?"**
> Yes. Each account is evaluated independently against its own objectives, so a bad run on
> one doesn't touch another.

## The rules

**"How is the daily loss limit calculated?"**
> It's measured against your equity at the start of the server day, and the amount is a
> percentage of your **starting balance**.
> On a $100,000 account with a 5% limit: if the day opens at $102,000, your floor for that
> day is $102,000 − $5,000 = **$97,000**. Touch it and the account fails automatically.
> Equity includes floating P&L, so an open losing trade counts before you close it.

*(Never quote $96,900 from old screenshots — live FAQ and engine use $97,000.)*

**"What's my overall drawdown floor?"**
> Fixed for the life of the account. Your floor is your starting balance minus the overall
> limit: on a $100,000 2-Step at 10%, that's **$90,000**, and it stays $90,000 no matter
> how much you make.
> That's static drawdown. We don't use trailing drawdown on any plan — the floor never
> climbs behind your profits.

**"Static vs trailing — why does it matter?"**
> With trailing drawdown, your floor follows your equity high. Make $8,000, and the level
> that fails you rises by $8,000 too — so a good week quietly tightens the noose.
> Static keeps the floor where it started. Every plan we sell is static: 10% on 2-Step,
> 8% on Instant Funding. We don't offer trailing at all.

**"What counts as a trading day?"**
> Any server day on which your account held an open position. It counts once per day. You
> need 5 of them per phase on a 2-Step evaluation and 5 more on the funded account before
> the first payout; Instant Funding needs 30 on the funded account.

**"Is there a time limit?"**
> None. Take as long as you want — the account stays active as long as you respect the
> loss limits. An evaluation can't expire underneath you.

**"Can I trade news? Hold over the weekend? Use an EA?"**
> Yes to all three. No news blackouts and no weekend-flat rule. What we enforce are the
> published loss limits, profit targets, minimum trading days, the cap on total open
> volume, and the ban on abusing the simulation in Section 6 of the Terms (for example
> opposite positions across accounts, order spam, or trading through a VPN).

**"Is there a maximum position size?"**
> There's a cap on your **total** open volume: 6 lots per $100,000 of account size. So 1.5
> lots on $25,000, 6 lots on $100,000, 60 lots on $1,000,000. It's there so nobody passes
> by putting the whole account on one candle — and it's the only volume rule.

**"What leverage do I get?"**
> It's fixed per market, on both models: Forex 1:100, metals and indices 1:50, energy
> 1:20, crypto 1:5 to 1:10.

**"Can I trade on weekends?"**
> Weekend trading is a $199 add-on, same price on any account size.

**"What happens when I break a rule?"**
> The risk engine fails the account, open positions may be closed, and the exact reason —
> which rule, at what equity, at what time — is written to your dashboard. Loss-limit and
> volume breaches are decided by the engine, and the identical code runs on every account,
> including the ones that pass. You can see precisely what happened.

## Funded accounts and money

**"How do payouts work?"**
> Once your account is funded and past the first-payout trading-day minimum (5 on 2-Step,
> 30 on Instant), request a payout from the dashboard when you're in profit at or above
> the minimum amount. You need one-time KYC first. Your share is 90% on evaluation plans
> and 70% on Instant Funding, and you can request part of what's available rather than all
> of it. By default you can request every 14 calendar days (bi-weekly); On-demand Payout
> removes that wait. Each request is reviewed, then paid in vUSDT on Polygon.

**"When do I get my fee back?"**
> {Check the plan first.} On a 2-Step plan: with your first payout from that funded
> account — the full fee is credited as additional vUSDT on top of your profit share,
> automatically. It is a credit, not a card refund. Instant Funding has no fee credit;
> an Instant account bought before the November 2026 change keeps it, and the payout form
> in the portal shows the credit whenever it applies.

**"Is there a minimum payout?"**
> Yes: $500 or 2% of the account size, whichever is higher. That is $500 on $25,000,
> $2,000 on $100,000, and $20,000 on $1,000,000. Amounts are whole dollars.

**"How do you pay me?"**
> In vUSDT on Polygon — PTF's native internal token, not Tether USDT — to a wallet you
> control. It is transferable on Polygon; we do not redeem it for cash or fiat, and its
> value is not guaranteed. You confirm the wallet when the request is unlocked.

**"How often can I withdraw?"**
> {Do not promise a payment date. Use:} By default you can request a payout every 14
> calendar days (bi-weekly) once the funded account is in profit and past the first-payout
> trading-day minimum (5 on 2-Step, 30 on Instant). The On-demand Payout add-on removes
> the 14-day wait for that account. Each request is reviewed by the team before payment
> in vUSDT on Polygon. If you want a firm date for a specific request, I'll have someone
> confirm it directly.

**"Why do I have to do KYC?"**
> It's a one-time identity check before your first payout — we pay the verified account
> holder and nobody else, which is what stops accounts being sold or traded on. You'll
> need your name, date of birth, address and country, plus a photo ID (front and back) and
> a proof of residence. JPG, PNG or PDF, up to 5 MB each. It isn't needed to buy or trade
> — only to get paid.

**"My KYC was rejected."**
> {Escalate. Then:} The rejection reason is shown in your dashboard, and a rejection isn't
> final — you can correct the details or upload a clearer document and resubmit. I've
> asked the team to look at your case directly so you get a specific answer rather than a
> general one.

**"How does scaling work?"**
> Every time a funded account grows by +15%, you choose from the dashboard: take the
> reward, or move up to the next account size. It's one or the other — if you move up, the
> profit pays for the bigger account instead of being paid out, and we set up a fresh
> account at that size, funded from day one. No application, no waiting, and you can
> repeat it all the way to $2,000,000.

**"Do I get a certificate?"**
> Yes — every passed evaluation and every funded account gets one, with a unique ID anyone
> can verify at /verify. Useful if you want to show it publicly.

## When something is wrong

**"I was failed and I think it's a mistake."**
> {Escalate, always.} Your dashboard has the exact breach record — the rule, the equity
> and the timestamp — so let's start from that rather than from memory. I've passed this
> to the team to review the account against the engine's log, and they'll come back to you
> directly.

**"I failed. Do I get a discount to try again?"**
> There's no automatic retry discount, and I can't create one. What I can tell you is that
> failing carries nothing beyond that account ending — no mark on your record, nothing
> that affects a future challenge — and you can start again whenever you want. Keep an eye
> on the daily reveal in the portal; that's where personal discount codes come from.

**"I want a refund."**
> {Check `02-facts.md` §7 first, then either:}
> {Before delivery:} Your MT5 account hasn't been created yet, so that's a straightforward
> full refund — I'm passing it to the team now and it'll go back to your original payment
> method. How long it takes to appear depends on your bank or payment provider.
> {After delivery:} Once credentials are delivered and trading is possible, the fee isn't
> refundable to your card. {2-Step only:} It is credited in full as additional vUSDT with
> your first payout from a funded account. If you think something went wrong on our side,
> tell me what happened and I'll have the team look at it.

**"My account still hasn't arrived."**
> {Escalate.} That's not normal. Your dashboard shows live status while it's pending, and
> I've flagged it to the team. If we cannot provision the account, the fee is refunded in
> full to your original payment method.

**"I'm going to open a chargeback."**
> {Escalate immediately.} Please give us the chance first — legitimate refund claims are
> honoured without a dispute, and I've already escalated yours to the team. A chargeback
> ends the account under our Terms, which helps neither of us. Tell me what went wrong and
> we'll deal with it directly.

**"Is this a scam?"**
> Fair question, so here's the honest version. We're an evaluation service: you trade a
> demo account with virtual funds, and if you hit the objectives you earn
> performance-based rewards. Nobody deposits real money with us and nothing is guaranteed.
> The trading objectives are published on /objectives and the prohibited practices in
> Section 6 of /terms, the risk engine applies the same code to every account, and every
> breach is logged with its exact reason in your dashboard. Certificates are publicly verifiable at /verify. Read /terms before buying —
> we'd rather you knew exactly what this is.

## Other

**"Can someone else trade my account?"**
> Yes, with our written approval — arranged before any trading on your behalf starts.
> Email us from your registered address naming the person or company who'll be trading.
> Two things stay fixed: the account remains yours, including responsibility for the rules
> (a limit your manager breaks is still your breach), and rewards go only to the verified
> account holder after KYC — we never pay managers. Handing over credentials without an
> approved arrangement is prohibited and can trigger an account review.
> {Approval itself is a human decision — escalate.}

**"Tell me about the affiliate program."**
> You earn 10% commission on paid orders from anyone who signs up through your referral
> code — on every purchase they make, not only the first. Stats and your link are under
> Rewards in the portal. You claim there as store
> credit or as vUSDT to your saved Polygon wallet — not cash, and not by emailing support.
> Full rules are on /affiliate-terms.

**"Can I use my own MT5 app?"**
> Usually not. Your MT5 credentials open the Built-in Terminal at /trade, in your browser,
> with nothing to install. They work in the MetaTrader 5 apps only when the server shown
> with your account is a MetaTrader 5 server, for example on accounts with the EA Allowed
> add-on.

**"What can I trade?"**
> 12 crypto pairs, gold, silver and platinum, WTI, Brent and natural gas, the S&P 500,
> Nasdaq 100 and Nikkei 225, and EURUSD, GBPUSD and USDJPY. Everything except crypto is
> closed at weekends.

**"What should I trade / what's your view on {market}?"**
> I can't help there — we don't give trading advice, signals or market opinions, and I'd
> be doing you a disservice by pretending otherwise. What I can do is make sure you know
> exactly which limits apply to your account so nothing surprises you.

---

## Objections, and what actually answers them

**"Too expensive."**
Reframe to net cost, don't discount. On 2-Step the fee is credited as vUSDT with the
first payout; say exactly that and do not call it a refund or "paying nothing". If price
is genuinely the blocker, point at the $25,000 plan at $299 rather than inventing a code.

**"Prop firms just want you to fail."**
Meet it head-on — this is the objection the whole brand is built to answer. The rules are
published in full, the same code runs on every account, and every breach is logged with
its exact reason and equity in the dashboard. Be straight that reward requests are
reviewed individually before they are paid. And point out what a firm hunting for
failures would actually do: trailing drawdown, time limits, news bans, consistency traps.
We use none of them.

**"I don't want a time limit hanging over me."**
There isn't one. The account stays active as long as the loss limits hold.

**"The split is only 70%."**
That's Instant Funding, and it's the price of skipping the evaluation entirely. If the
split matters more than starting today, 2-Step pays 90%.

**"What if I pass and you don't pay?"**
Payouts are requested from the dashboard, reviewed and paid to the KYC-verified account
holder. Every funded account and passed evaluation gets a publicly verifiable
certificate. Don't over-promise beyond that — if they push further, escalate.

**"I got burned by another prop firm."**
Don't attack the competitor. Ask what specifically went wrong, then answer with our
mechanics: static drawdown, no time limit, published rules, logged breaches. Let the
contrast do the work.

**Silence after a quote.**
One follow-up, useful rather than pushy: the entry plan's price, the 2-Step fee credit
with first payout, and a direct link to /objectives so they can check the rules before
deciding. Then stop.
