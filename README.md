# Autonomous Ops Agent

An autonomous operations agent that handles unit-level management tasks at physical businesses — inventory reordering, employee scheduling, bill payments, and similar reactive coordination work that a shop/restaurant/rental manager normally does by hand.

The reference business used throughout development is a simulated ice cream shop, but the data model is designed to generalize to other physical business types (retail, rentals, services).

## Why

Most day-to-day management work at a small physical business is:
- **State-driven** — decisions respond to current business state, not intuition
- **Pattern-heavy** — the same situations recur constantly
- **Digitally executable** — orders, payroll, and messaging already happen through digital systems

This project treats that work as automatable: maintain a live model of the business, detect meaningful events, resolve most of them with rules, escalate the rest to statistical models or an LLM, and execute the resulting actions.

## Architecture

Six layers, data flowing bottom-up (state → events → decisions → actions → feedback → owner) and top-down (owner overrides and outcomes feed back into learning):

```
Layer 6 — Owner Interface       (monitor, approve, override)
Layer 5 — Feedback & Learning   (score past decisions, improve future ones)
Layer 4 — Action Execution      (carry out decisions in the real world)
Layer 3 — Decision Engine       (tiered: rules → statistics → LLM)
Layer 2 — Event System          (detect and route everything that happens)
Layer 1 — World Model           (Postgres + Redis: the agent's understanding of reality)
```

## Stack

- **Language:** Python
- **Database:** PostgreSQL (system of record)
- **Cache / event bus:** Redis (working memory + Redis Streams)
- **HTTP:** FastAPI
- **Scheduling:** Celery
- **LLM:** Claude API
- **Comms/payments (later phases):** Twilio, SendGrid, Stripe