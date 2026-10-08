# Tech-tilbud — Project Overview

Shared source of truth for all agents. Maintained by Phil (planner). Last updated 2026-10-06 from a read of the code at commit `7e55de6`. Items marked **(unverified)** were not confirmed by running them.

## 1. Purpose

Danish telecom providers discount (or give away) phones, tablets, audio and gaming gear if you take a subscription with a 6-month commitment. Tech-tilbud compares those offers and shows the *real* saving:

`saving = market_price − min_cost_6_months`

- `market_price` = lowest price found on **Pricerunner** and **Prisjagt** (not the provider's inflated "before" price).
- `min_cost_6_months` = device price with subscription + 6 months of subscription fees (computed by each scraper).
- A reminder tool emails the user ~6 months later (minus N days) so they can switch provider again.

UI language is Danish (`<html lang="da">`). Domain is tech-tilbud.dk / tech-tilbud.com (both appear in code). Revenue: affiliate deeplinks (Adtraction-style `go.adt284.net` for Oister, `pin.3.dk` for 3; other providers: **unverified**).

## 2. Tech stack

| Area | Details |
|---|---|
| Web | Next.js **16.2.3** (App Router, `src/app`), React **19.2.3**, React Compiler enabled (`next.config.ts`), TypeScript 5 strict, Tailwind CSS 4 (`@tailwindcss/postcss`) |
| Backend | Next route handlers; Neon serverless Postgres (`@neondatabase/serverless`), Resend (email), Upstash QStash (dep present; see §6), `@vercel/analytics` |
| Tests (TS) | `vitest` ^4 (in `dependencies`) |
| Lint | ESLint 9 + `eslint-config-next` (core-web-vitals + typescript) |
| Scrapers | Python 3.11 in CI (`.venv` locally): `playwright`, `playwright-stealth`, `beautifulsoup4`, `lxml`, `requests` (`scrapers/requirements.txt`) |
| Hosting | Vercel (`vercel.json` sets security headers only) |
| Automation | GitHub Actions: daily scrape + daily reminder trigger |

## 3. Directory map

```
src/app/                 pages: page.tsx (offer list), about/, reminder/; layout.tsx
src/app/api/reminders/   route.ts (POST create reminder), send/route.ts (POST send due reminders)
src/components/          OfferCard, ProviderFilter, CategoryFilter, SortSelect, PriceRangeSlider,
                         PaginationControls, ReminderForm, Header, FaqItem, Tooltip
src/hooks/useOffers.ts   all filter/sort/pagination state for the list page
src/lib/offers.ts        loads scraped JSON, merges providers, attaches market_price → `allOffers`
src/lib/db.ts            `sql` tagged template over Neon (`DATABASE_URL`)
src/lib/offers.tests.ts  vitest tests (see §9 – not picked up by default)
src/proxy.ts             Next 16 "proxy" (ex-middleware): in-memory per-IP rate limit, 60 req/min
src/types/offer.ts       `Offer`, `SortOrder`
scrapers/                one scraper per provider + price scrapers + shared utils
data/<provider>/         scraper output JSON, COMMITTED to git (it is the app's data source)
public/images/<provider>/ product images downloaded by scrapers (webp)
.github/workflows/       scrape.yml, reminders.yml
docs/                    this file
```

## 4. Commands

```bash
npm install
npm run dev        # next dev
npm run build      # next build
npm run start
npm run lint       # eslint
npx vitest run     # NOT wired in package.json; also see §9 re: test file name

# scrapers (from repo root; scripts resolve paths relative to their own location)
pip install -r scrapers/requirements.txt && playwright install chromium --with-deps
python scrapers/telmore_scraper.py          # likewise oister, cbb, 3, yousee, norlys, callme, elgiganten
python scrapers/pricerunner_scraper.py      # market prices, needs provider JSON to exist first
python scrapers/prisjagt_scraper.py
python scrapers/audit_market_prices.py      # sanity report: coverage + pricerunner/prisjagt disagreement
python scrapers/test_score_match.py         # plain-script tests for the name matchers (run from scrapers/)
```

## 5. Architecture and data flow

```
provider sites ──(Playwright/BS4 scrapers)──► data/<provider>/*_offers.json  ─┐
product names  ──(pricerunner/prisjagt)────► data/{pricerunner,prisjagt}/*_prices.json ─┤
                                                                               ▼
                                       src/lib/offers.ts  (static JSON imports, merged at build time)
                                                                               ▼
                         useOffers hook (client-side filter/sort/paginate) → OfferCard grid on `/`
```

- **No runtime database for offers.** The JSON files are imported statically, so fresh data requires a redeploy. The scrape workflow commits updated JSON to `master` ("Update scraped data"), which presumably triggers a Vercel deploy (**unverified**).
- **Offer record** (per provider JSON): `link`, `product_name`, `image_url`, `provider`, `type` (`phone|tablet|sound|gaming`), `price_without_subscription`, `price_with_subscription`, `subscription_price_monthly`, `discount_on_product`, `min_cost_6_months`, `saved_at` (`DD-MM-YYYY-HH:MM`); CBB/Telmore-tilgift also `subscription_price_monthly_after_promo`.
- **Price files**: object keyed by *product name* → `{market_price, looked_up_at, matcher_version}`. Joined to offers by case-insensitive exact name; when both sources have a price the lower wins (`lowestMarketPrice`).
- **Plausibility guard** (`offers.ts`): market price < 50% of the provider's cash price is discarded (assumed wrong match) → offer is then hidden (list requires `market_price != null`).
- **List page rules** (`useOffers.ts`): hides offers without market price or subscription price; "hide non-saving" is on by default; default sort `saved_desc`; 50 per page; price slider bounds come from `min_cost_6_months`.
- **Scraper pipeline details**
  - `scraper_utils.py`: `write_json`, `now_timestamp`, `download_image_cached` (images saved to `public/images/<provider>/`), logging helpers, `PRODUCT_NAME_SUBSTITUTIONS` (regex rewrites so messy names become matchable).
  - `product_blacklist.py`: words (brugt, refurbished, loq, legion, bærbar, robotstøvsuger) that exclude a product everywhere; scrapers call `skip_if_blacklisted`.
  - `provider_sources.py`: list of provider JSON files the price scrapers read to collect product names. **Adding a provider = add it here and in `src/lib/offers.ts`, `PROVIDERS`, workflow.**
  - Price scrapers use fuzzy `score_match` (storage, model number, chip, case size, accessory filters), cache results `MAX_PRICE_AGE_DAYS=3`, and re-lookup when `MATCHER_VERSION` is bumped. **Bump the version whenever matching logic changes.**
- **Reminders**: `ReminderForm` → `POST /api/reminders` stores `{email, days_before, send_at = now+6mo−days, message ≤50 chars, ip}` in table `reminders` (one unsent reminder per IP). Daily `reminders.yml` POSTs to `/api/reminders/send` with a bearer secret; the handler emails due rows via Resend then deletes them.

## 6. Configuration (names only)

| Name | Where used |
|---|---|
| `DATABASE_URL` | `src/lib/db.ts` (Neon) |
| `RESEND_API_KEY` | `api/reminders/send` |
| `QSTASH_CURRENT_SIGNING_KEY` | `api/reminders/send` bearer check; GH secret in `reminders.yml` |
| `REMINDER_SEND_URL` | GH Actions secret, URL of the send endpoint |
| `GITHUB_TOKEN` | scrape workflow push |

`.env*` is git-ignored; no `.env.example` exists. No SQL schema/migration for `reminders` is in the repo (columns inferred from queries: `id, email, days_before, send_at, message, ip, sent`).

## 7. Conventions

- TS: 4-space indent, no semicolons, single quotes in `src/`, `@/` alias → `src/`. Components are PascalCase files in `src/components`; `'use client'` on interactive ones. Tailwind utility classes with a dark palette (`#20262f`, `#2a3340`, accent `#4a90b8`).
- Python: lowercase `#` comments, shared helpers in `scraper_utils.py`, paths via `BASE_DIR = Path(__file__).resolve().parent.parent`, output through `write_json`.
- User-facing strings are Danish.
- Commit messages in history are short lowercase sentences.

## 8. Known gaps, risks, TODOs

1. **Tests not runnable as configured**: `src/lib/offers.tests.ts` doesn't match vitest's default `*.test.ts|*.spec.ts` glob and there is no `test` script; `vitest` sits in `dependencies`. `scrapers/test_*.py` are plain scripts, not pytest-collected in CI, and the docstring mentions Windows `py`.
2. **Elgiganten data is empty** (`[]`) though its scraper runs in CI. `telmore_tilgift_scraper.py` exists and its data is used, but it is **not in `scrape.yml`** (its JSON was last updated manually/earlier).
3. **Reminder send auth** compares the *QStash signing key* as a plain bearer token; real QStash requests carry an `Upstash-Signature` JWT (the `@upstash/qstash` receiver is unused). Currently triggered by GH Actions with the key as a shared secret. Also: reminder sending deletes rows rather than marking `sent`, a Resend failure mid-loop can leave the loop half-done, and no email validation / duplicate handling beyond per-IP check.
4. **Rate limiter** (`proxy.ts`) is in-memory per serverless instance, so it is only best-effort on Vercel; it trusts `x-forwarded-for`.
5. **Duplication**: `useOffers.ts` repeats the filter+sort chain twice (page slice + total count); `offers.ts` repeats the per-provider mapping 9 times. Good refactor candidates.
6. **Scrape workflow** runs scrapers sequentially with `run:` steps, so one failing scraper stops the rest and nothing is committed; `git add .` stages everything in the repo. Pricerunner/Prisjagt (~1,900 names each) are slow. Cron for scrape and reminders is the same time (06:00 UTC).
7. `scrapers/__pycache__/*.pyc` are tracked in git despite `.gitignore` (Ian's working tree shows them deleted).
8. Hardcoded sender `reminder@tech-tilbud.com` vs links to `tech-tilbud.dk` — confirm which domain is canonical.
9. Offers in JSON are not validated at build time; `asOffers` casts blindly.
10. Affiliate links are baked into scraped `link` values per scraper (Oister, 3 confirmed); other providers unverified. Affiliate IDs are committed in code.
11. Next/React versions: `eslint-config-next` is 16.1.6 vs `next` 16.2.3 (minor mismatch).

Current data snapshot (counts of offers): Callme 155, Yousee 57, Cbb 44, Norlys 43, 3: 37, Telmore 35 + 8 tilgift, Oister 10, Elgiganten 0. Last scrape in repo: 17-09-2026.

## 9. How to work on this project (agents)

1. Read this file, then the issue. Check `git status` first — Ian has uncommitted changes (`.idea/`, `.gitignore`, `package-lock.json`, `__pycache__` deletions). **Never touch or revert them.**
2. **No `git commit`, `push`, tags or PRs without Ian's explicit permission for that specific change.** Leave work uncommitted for review in WebStorm.
3. Phil plans/documents; Cody implements. Keep changes scoped to the issue.
4. Don't hand-edit `data/**/*.json` or `public/images/**` — they are scraper output. Fix the scraper instead; run it only if the issue asks (it hits live sites and rewrites data).
5. Changing price matching → add a case to `scrapers/test_score_match.py` and bump `MATCHER_VERSION` in both price scrapers.
6. Adding a provider → touch: new scraper + `data/<name>/`, `provider_sources.py`, `src/lib/offers.ts`, `PROVIDERS`, `ProviderFilter`, `scrape.yml`.
7. Before reporting done run `npm run lint` and `npm run build` (and tests once §8.1 is fixed). Never put secret values in code, docs or comments.
8. Update this file when you change structure, commands, env vars or conventions.
