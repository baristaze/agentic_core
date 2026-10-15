# Signals: spend_budgets

What model calls may spend: the gate every call passes first, which
sets aside the most the call could cost or refuses it, then settles the
hold at what the call cost.

## Outcomes

- `spend_budgets`: `held`, a call's hold set aside; `refused`, a call
  the gate refused, whose loop parks on `spend_budget` until a budget is
  raised or resets; `overshoot`, a call that cost more than its hold,
  counted in full; and one `settled_<bill>` per hold settled, by its
  bill: `settled_billed`, `settled_unknown` (the whole hold counts),
  and `settled_not_billed`.
