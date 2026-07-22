# Commercial use & enterprise

**Short version:** MemoryBrain is **MIT-licensed and free for everyone**, including companies.  
If a corporation wants to **pay you** for support, custom work, or a formal commercial deal, that is welcome — it is optional, not a requirement to use the software.

This is practical guidance, **not legal advice**. For a real contract, use a lawyer.

---

## How the open license already works

Under the [MIT License](LICENSE):

| Who | Can they use MemoryBrain? | Do they have to pay? |
|-----|---------------------------|----------------------|
| Individuals / hobbyists | Yes | No |
| Startups / small teams | Yes | No |
| Corporations / enterprises | Yes | No (for MIT use) |
| SaaS products that embed it | Yes (MIT allows this) | No (unless they choose a paid deal with you) |

MIT already includes **commercial use**. You do **not** need a second license just so a company can run MemoryBrain on their servers.

---

## What “buy it from me” usually means

Companies often pay for things that **MIT does not include**:

| Offering | What they get | Why they pay |
|----------|---------------|--------------|
| **Support / SLA** | Guaranteed response times, upgrades help | Risk reduction |
| **Consulting / setup** | You install, wire Claude/Grok/Codex, harden Docker | Time |
| **Custom features** | Private forks, SSO, multi-tenant, hosted version | Roadmap |
| **Training** | Workshops for their AI team | Enablement |
| **Commercial license add-on** | Warranty, indemnity, different terms for legal | Procurement / legal |
| **Sponsorship / paid partnership** | Funding development of features they need | Influence roadmap |
| **Acquisition / exclusive rights** | Rare; negotiated separately | Business sale |

None of these cancel free MIT use for everyone else unless you deliberately re-license the whole project later (a big decision).

---

## Dual model (what this repo uses)

```text
┌─────────────────────────────────────────────┐
│  MIT (default)                              │
│  Free forever for anyone — including corps  │
│  “AS IS”, no warranty                       │
└─────────────────────────────────────────────┘
                    +
┌─────────────────────────────────────────────┐
│  Optional commercial relationship with you  │
│  Support · custom work · formal agreements  │
│  Negotiated case by case                    │
└─────────────────────────────────────────────┘
```

This is a common open-source business pattern: **open core / open tool + paid services**.

---

## What you should *not* do (while keeping MIT)

- Do **not** put “corporations must pay to use this” in the same file as unrestricted MIT — that contradicts MIT and confuses lawyers.
- Do **not** remove MIT for companies only without switching the whole project to a different model (e.g. AGPL + commercial, or a proprietary license). That is a product decision, not a one-line LICENSE tweak.

If you ever want **paid-only corporate use** of the code itself, you need a different license strategy (talk to counsel). Until then: free use for all, optional paid extras.

---

## How a company can reach you

1. Open a GitHub issue or Discussion on  
   https://github.com/Zarakilian/MemoryBrain  
   titled **“Commercial enquiry”** (or similar).  
2. Or contact via the author’s GitHub profile:  
   https://github.com/Zarakilian  

Include: company name, use case (internal tools / product / many seats), what they want (support vs features vs license), timeline.

---

## Suggested reply when someone asks “Can our company use this?”

> Yes. MemoryBrain is MIT-licensed — free for commercial and internal use.  
> If you need support, an SLA, custom development, or a formal commercial agreement, contact me and we can discuss. That is optional; the open-source software remains free under MIT.

---

## Optional next steps for you (Miguel)

When commercial interest appears:

1. Decide a simple menu (e.g. setup package / monthly support / custom day rate).  
2. Use a short consulting agreement or MSA from a lawyer when money is involved.  
3. Keep the public repo MIT unless you intentionally productise a separate “Enterprise” edition.  
4. Never put customer secrets or private forks into the public GitHub history.

---

## Summary

| Question | Answer |
|----------|--------|
| Can corps use MemoryBrain free? | **Yes**, under MIT. |
| Can corps buy support / custom / a deal from you? | **Yes**, optional — see contact above. |
| Does paying change MIT for others? | **No**, unless you re-license the project. |
| Where is this stated? | [LICENSE](LICENSE) (notice) + this file. |
