# Shopify AI SEO Generator (Versions 1, 2 and 3)

Connects to your Shopify store and uses a **free** AI API to write:

**Version 1**
- an SEO-optimised product description (HTML)
- a meta title (≤ 60 characters)
- a meta description (≤ 160 characters)

**Version 2**
- **Guess checker:** a second AI pass flags anything the text claims that isn't
  backed by the real product facts, with one click to remove it
- **Image alt text:** the AI looks at each product photo and describes it (≤ 125 characters)
- **Collection pages:** description, meta title and meta description

**Version 3**
- **SEO audit & score:** every product and collection checked against SEO rules, a
  score out of 100 and a list of what to fix. Also a **free audit of any Shopify store**
  from its public pages (no access needed)
- **Backup & undo:** what Shopify had is saved before every publish; Undo puts it back
- **Client stores:** connect other stores with Shopify's install screen and switch between
  them. Each store keeps its own files; the public demo only ever shows the main store

Results go into CSV files (`seo_results.csv`, `collection_results.csv`,
`alt_text_results.csv`) so you can review them. You approve the ones you like and
push only those back to Shopify. Nothing changes on your store until you push.

**Total cost: $0.** You need a free Shopify dev store, a free Shopify Dev Dashboard
account, and a free Google Gemini key (or Groq key).

---

## 1. Install

You need Python 3.10 or newer.

```powershell
cd C:\Users\imuma\Desktop\shopify-seo-ai
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env` using the steps below.

---

## 2. Create a free Shopify development store

A development store is a full Shopify store that costs nothing and never expires.
It isn't open to real customers, so it's a safe place to test.

1. Go to **https://shopify.dev** and click **Log in / Sign up**. Create a free account.
   This opens the **Dev Dashboard**.
2. In the Dev Dashboard, open **Dev stores** in the left menu, then click **Create dev store**.
   - Give it a name, e.g. `my-seo-test`.
   - If you're offered test data, choose it. You'll get sample products to work with.
3. Open the store and add a few products if it's empty (**Products → Add product**).
   A title and a one-line description are enough. The AI improves short descriptions.
4. Note your store domain. It looks like `my-seo-test.myshopify.com`
   (**Settings → Domains** in the store admin).

> ⚠️ Create the store **from the Dev Dashboard**, not from the Shopify admin.
> The token method below only works when the store and the app belong to the same
> Dev Dashboard organisation.

---

## 3. Create an app and get API credentials

Since January 2026 Shopify no longer lets you create "custom apps" inside the store
admin. You create apps in the Dev Dashboard instead:

1. In the **Dev Dashboard**, go to **Apps → Create app**. Choose to create it manually
   and name it something like `SEO Generator`.
2. In the app's version/configuration screen, find **Access → Scopes** (Admin API scopes)
   and add:
   - `read_products`
   - `write_products`
   - `read_files` and `write_files` (Version 2: needed to save image alt text)

   Type them comma-separated: `read_products,write_products,read_files,write_files`
3. Click **Release** to save that version. (After adding scopes to an installed app,
   open the store admin and approve the app's new permissions.)
4. From the app's overview page, click **Install app**, pick your dev store and approve.
5. Open the app's **Settings** page and copy the **Client ID** and **Client secret**.

Put them in `.env`:

```env
SHOPIFY_STORE_DOMAIN=my-seo-test.myshopify.com
SHOPIFY_CLIENT_ID=paste-client-id
SHOPIFY_CLIENT_SECRET=paste-client-secret
```

The tool trades these for an access token automatically. Shopify tokens from this
method last 24 hours, and the tool fetches a new one whenever it runs.

**Already have an old-style token?** If you have an older store with a custom app
token starting with `shpat_`, put it in `SHOPIFY_ACCESS_TOKEN=` and leave the
client ID/secret empty.

---

## 4. Get a free AI API key

Pick **one**:

| Provider | Free key | Default model | Notes |
|---|---|---|---|
| **Google Gemini** (recommended) | https://aistudio.google.com/apikey | `gemini-flash-lite-latest` | Sign in with Google → *Create API key*. No credit card. |
| **Groq** | https://console.groq.com/keys | `llama-3.3-70b-versatile` | Sign up → *Create API Key*. Very fast. No credit card. |

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=paste-your-key
```

Free tiers have per-minute and per-day limits. `AI_DELAY_SECONDS=5` keeps the
tool under the per-minute limit. If you hit the daily limit, run `generate` again
the next day. It skips products that are already done and carries on.

Notes:
- **Gemini free tier:** Google may use what you send to improve its products.
  That's usually fine for product descriptions, which are public anyway.
- **Changing models:** If a default model is retired, set `AI_MODEL=` to a newer
  one listed on the provider's site.
- **Brand voice:** Set `STORE_CONTEXT=` to a sentence about your brand and the AI
  will write in that voice.

---

## 5. Test the connection

```powershell
python cli.py check
```

You should see:

```
Shopify store: my-seo-test.myshopify.com
  OK - connected to 'my-seo-test'
AI provider: gemini
  OK - model replied: OK
```

---

## 6. Use it

### Option A: Website (easiest)

```powershell
python app.py
```

Open **http://127.0.0.1:8001**. The website has these pages:

| Page | What it's for |
|---|---|
| **Home** (`/`) | About the project, all versions, the developer, live numbers |
| **Guide** (`/guide`) | How to use everything, for beginners |
| **Audit** (`/audit`) | SEO score and what to fix: full audit of the store, plus a free audit of any Shopify store (Version 3) |
| **Chat** (`/chat`) | Type what you want, e.g. *"generate SEO for 3 products"*, *"approve all"*, *"do a dry run push"*. A real publish always asks you to click **Yes, publish** first. |
| **Review** (`/review`) | Read, edit, approve, publish and **undo** product by product |
| **Stores** (`/stores`) | Owner only: connect client stores and pick the one to work on (Version 3) |

Using the **Review** page:

1. Click **1. Generate**. Products are fetched and processed one by one; the log
   shows progress. Tip: set **Limit** to `3` for your first try.
2. Review each product. The current text is on the left and the AI version is on the
   right. Edit anything you like (it saves when you click away). Use **Show preview**
   to see the description rendered. Character counters turn red when a field is too long.
3. Tick **Approve** on the products you're happy with.
4. Tick **Dry run** and click **2. Push approved** to see what would change.
   Then untick Dry run and push for real.

### Option B: Command line + Excel / Google Sheets

```powershell
python cli.py generate --limit 3     # try 3 products first
python cli.py generate               # all products (skips ones already done)
```

Open `seo_results.csv` in Excel or Google Sheets. Edit the `new_...` columns if
needed and type `yes` in the **approved** column for rows you want to publish.
Save as CSV, **close Excel**, then:

```powershell
python cli.py push --dry-run         # preview
python cli.py push                   # publish approved rows
```

Other commands:

```powershell
python cli.py generate --regenerate          # redo products that already have content
python cli.py guesses                        # Version 2: flag guesses in existing text
python cli.py generate --type collections    # Version 2: collection pages
python cli.py generate --type images         # Version 2: image alt text
python cli.py push --type images             # publish approved alt text
python cli.py undo                           # Version 3: put the originals back on Shopify
```

`--type products|collections|images` works with `generate`, `guesses`, `push` and `undo`.

### Client stores (Version 3)

Open **Stores**. It walks you through making one small app per client in the Shopify
Dev Dashboard (Shopify's *custom distribution* locks an app to one store), with copy
buttons for the App URL, Redirect URL and scopes. Paste that app's Client ID and secret,
click **Connect**, approve on Shopify's page, then click **Work on this store**.

- Keys and tokens are saved in `stores.json`; each client's CSVs, backups and audit live
  in `stores/<store-name>/`. Both are in `.gitignore`.
- The redirect goes to `PUBLIC_URL` (default `http://127.0.0.1:8001`). Running locally,
  you must click Connect yourself on this computer (with staff/collaborator access to the
  store). Online, set `PUBLIC_URL` to the site's address so clients can approve themselves.

---

## 7. Online demo & owner login

The website has a **Guide** page (`/guide`) that explains every feature for beginners.

| Where it runs | Who can do what |
|---|---|
| **Your PC** (default, `HOST=127.0.0.1`, no `ADMIN_PASSWORD`) | Only you can open it, so there's no login and you can do everything. |
| **Online** (`HOST=0.0.0.0`) | **Visitors** get a read-only demo: browse results, read the Guide, and ask the chat questions (10 messages/hour, chat can't run actions). The **owner** clicks *Owner login* and uses `ADMIN_PASSWORD` to generate, edit, approve and publish. |

Settings for online use (`.env` or the hosting provider's environment variables):

```env
HOST=0.0.0.0
ADMIN_PASSWORD=a-long-unique-password
PUBLIC_DEMO=true          # false = visitors see nothing until they log in
SESSION_SECRET=any-long-random-text
```

Safety built in: every action that changes something needs the owner; login tries are
limited (5 per 15 minutes); the login cookie is signed, HTTP-only and lasts 7 days.

**Deploying on Vercel (free, read-only demo):** push this folder to GitHub and import it in
Vercel. It finds `app.py` by itself (`vercel.json` gives it 60 seconds per request). On Vercel
the site is **always a read-only demo**: nobody can log in or change anything, because Vercel
can't save files. It shows the snapshot in `demo_data/`. After new work on your PC, refresh it with
`python cli.py demo-snapshot`, then commit and push. Settings in Vercel → Environment Variables:

```env
GEMINI_API_KEY=...        # for the demo chat (use a separate key, it is limited per visitor)
HIRE_URL=https://www.fiverr.com/your-gig   # "Hire me on Fiverr" replaces your email address
```

No Shopify keys are needed online. Do the real work (generate, publish, client stores) on your PC.

**Deploying on Render:** `render.yaml` is ready for [Render](https://render.com)'s free plan. It asks
for the secrets when you create the service. Note: free hosting has no permanent disk, so
CSV changes made online are lost when the service restarts. Do real work locally and use
the online site as a demo.

---

## CSV columns

| Column | Meaning |
|---|---|
| `product_id` / `collection_id` / `media_id` | Which item |
| `current_*` | What is on Shopify right now (for comparison) |
| `new_meta_title`, `new_meta_description`, `new_description_html`, `new_alt` | AI suggestion. Edit freely. |
| `guess_flags` | Version 2: possible guesses as JSON (`[]` = none, empty = not checked yet) |
| `approved` | Type `yes` to publish this row |
| `status` | `generated`, `edited`, `published`, `restored` (undone), `error: ...` or `publish_error: ...` |

Rows that are already `published` are never pushed twice. If you edit text after
publishing, the status becomes `edited` and the row needs approving and pushing again.
Regenerating clears approval so you review the new text.

Version 3: before each publish, the old Shopify content is added to `backups.csv`
(`table`, `key`, old description/meta/alt, `backed_up_at`, `restored_at`). Undo uses the
newest unused backup; rows published before Version 3 fall back to the CSV's original text.

The guess checker uses one extra AI call per item. Turn it off with `GUESS_CHECK=false`
in `.env` if you hit free-tier limits.

---

## Project files

| File | What it does |
|---|---|
| `config.py` | Loads settings from `.env` |
| `shopify_client.py` | Shopify Admin GraphQL API: auth, read/update products, collections, image alt text |
| `ai_generator.py` | Prompts, calls Gemini/Groq, guess checker, image alt text |
| `csv_store.py` | Reads/writes the three CSV files |
| `pipeline.py` | The jobs: `generate`, `check_guesses`, `remove_guesses`, `push_approved`, `undo` |
| `backups.py` | Version 3: backups saved before publishing, used by Undo |
| `audit.py` | Version 3: SEO audit rules and scores (full + free public audit) |
| `stores.py` | Version 3: the main store + connected client stores, and which one is active |
| `shopify_oauth.py` | Version 3: connecting a client store with Shopify's install screen |
| `cli.py` | Command-line interface |
| `chatbot.py` | Chat assistant: understands messages and picks an action |
| `app.py` | Website server (pages + API) |
| `auth.py` | Owner login, visitor demo mode, rate limits |
| `render.yaml` | Settings for free hosting on Render |
| `static/home.html`, `guide.html`, `audit.html`, `chat.html`, `review.html`, `stores.html`, `login.html` | Website pages |
| `static/style.css`, `nav.js` | Shared look and the login link in the top bar |

---

Built by **Syeda Umaima** (Owner & Developer) · imumaimabukhari@gmail.com

---

## Troubleshooting

- **"Could not get a Shopify access token":** Make sure the app version is
  *released* with `read_products` and `write_products`, the app is *installed* on the
  store, and the store was created from the Dev Dashboard (same organisation).
- **Image alt text fails with "may not change images" / access denied:** Add the
  `read_files,write_files` scopes (see step 3), release, and approve on the store.
- **401 / 403 from Shopify:** Scopes are missing. Add them, release a new version, and
  reinstall or approve the update on the store.
- **AI error 429:** Free-tier rate limit. Increase `AI_DELAY_SECONDS` (e.g. 10), or wait
  and re-run. Finished products are skipped.
- **"Can't save seo_results.csv - is it open in Excel?":** Close the file in Excel and retry.
- **API version error:** Set `SHOPIFY_API_VERSION` in `.env` to a current version
  (see https://shopify.dev/docs/api/usage/versioning).

## Security

- Secrets live only in `.env`, which is in `.gitignore`. Never share or commit it.
- The dashboard listens on `127.0.0.1`, so only your own computer can open it.
- Client stores: the callback checks Shopify's signature and a one-time `state` value.
  Visitors never see client data. The free audit refuses private network addresses and
  is rate limited (5 per hour per visitor).
