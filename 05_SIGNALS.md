# Proactive issue detection (Problem 1)

Build this **after** the minimum requirements pass their tests. It is the
differentiator, not the foundation.

## Scope decision

Answer Problem 2 (trust) through the authority ladder, which is already load-
bearing for the core requirements, and Problem 1 (proactive detection) as a
separate internal-only view. Say this explicitly in the product note: Problem 2
is addressed structurally rather than as a bolt-on feature, which is why it does
not appear as a separate screen.

## Approach

Deterministic rules over seven tickets and six orders, not clustering. With a
dataset this size an embedding-based anomaly detector would be theatre — it
would fit noise and you could not explain any alert it raised. Rules that name
their own trigger are honest, testable, and produce alerts a human can act on.
Note in the product note that at ParcelPilot's real volume (hundreds of requests
weekly) you would add embedding-based clustering for near-duplicate complaint
detection, and what threshold you would gate it behind.

## Detectors

Each emits a `Signal { id, kind, severity, title, evidence[], affected_accounts[],
recommended_action, computed_at }`.

**1. SLA breach and approaching breach.** For every open ticket: classify
severity, resolve the target through the contract-then-policy ladder, compare
elapsed against target at the snapshot. Emit `breached` above 100% and
`at_risk` above 75%. Expected output on the supplied data: TKT-501 breached
(15-minute Northstar target, 30 minutes elapsed) and TKT-505 breached
(30-minute Enterprise default, 2h30m elapsed). Both P1.

**2. Same root cause across tickets.** Match open tickets against known issues
by keyword and symptom. TKT-502 maps to KI-208. TKT-504 maps to KI-211. Where a
known issue has more than one linked ticket, or one linked ticket plus a
historical one, raise it — KI-208 has TKT-502 open and TKT-451 closed against
the same account, which is a recurrence, not an isolated report.

**3. Carrier concentration.** Group open problems by carrier. SwiftShip appears
in ORD-1001, ORD-4001 and TKT-504 and is the subject of KI-211. RoadRunner has
ORD-2002's missed pickup. Flag any carrier with two or more open problem
signals.

**4. Cancellation spike.** Four of six orders have a cancellation request, three
of them within a two-hour window on 16 August. Against any sane baseline that is
anomalous. Since the pack has no historical baseline, compute the rate over the
snapshot day and compare against a configurable expected rate, and be honest in
the UI that the baseline is assumed rather than learned.

**5. Multi-account impact.** Any known issue or carrier problem touching two or
more accounts is escalated a level. On this data, SwiftShip touches ACCT-001 and
ACCT-004.

**6. Stale guidance.** Tickets whose `historical_resolution` contradicts a
current tier 1–4 source. Emits TKT-450 and TKT-451. This is a genuinely
original signal, it falls straight out of the authority ladder you already
built, and it is the one item on this list a reviewer will not have seen from
other candidates.

## Interface

A `Signals` tab in internal mode: ranked cards, each showing what triggered it,
the evidence rows, and a "Ask the agent about this" button that seeds the chat
with a pre-written investigation query. That button is the integration point
that makes the feature feel like one product rather than two — the detector
finds it, the agent explains it, the confirm flow acts on it.

Ranking: severity × account impact × breach magnitude. Keep the formula in one
readable function and print the contributing terms on the card. An unexplained
priority score is exactly the confident opacity the brief is warning about.

Recompute on request rather than on a schedule. The data is a frozen snapshot;
a cron job would be pretend infrastructure.
