# Connecting Google Calendar (OAuth setup)

The dashboard shows your Google Calendar, so it needs permission to read it.
Google requires you to create a small (free) set of credentials — a **Client
ID** and **Client Secret** — that identify *your* dashboard to Google. You do
this once, in the [Google Cloud Console](https://console.cloud.google.com/).

It takes about 10 minutes. Follow along step by step.

> 📸 **About the screenshots:** the images below are placeholders. Google's
> console changes its layout from time to time, so capture your own as you go
> (or just follow the written steps — they're the source of truth). Drop your
> images into `docs/img/` with the filenames shown and they'll appear here.

---

## Step 1 — Create a project

1. Open <https://console.cloud.google.com/>.
2. In the top bar, click the **project dropdown** → **New Project**.
3. Name it something like `Family Dashboard` and click **Create**.
4. Make sure the new project is **selected** in the top bar before continuing.

![Creating a project](img/oauth-01-create-project.png)

---

## Step 2 — Enable the Google Calendar API

1. Go to **APIs & Services → Library**
   (<https://console.cloud.google.com/apis/library>).
2. Search for **Google Calendar API**, open it, and click **Enable**.

![Enabling the Calendar API](img/oauth-02-enable-calendar-api.png)

---

## Step 3 — Configure the consent screen

This is what your family sees when they sign in.

1. Go to **APIs & Services → OAuth consent screen**
   (<https://console.cloud.google.com/apis/credentials/consent>).
2. Choose **External** and click **Create**.
3. Fill in the required fields:
   - **App name:** `Family Dashboard` (or whatever you like)
   - **User support email:** your email
   - **Developer contact email:** your email
4. Click **Save and Continue**.
5. On the **Scopes** screen, click **Add or Remove Scopes**, search for and add
   **`.../auth/calendar.readonly`** (Google Calendar API — read calendars), then
   **Update** and **Save and Continue**.

![OAuth consent screen](img/oauth-03-consent-screen.png)

### Should I "Publish" the app?

You'll see the app is in **Testing** mode. You have two choices:

| | Testing mode | Published (Production) |
|---|---|---|
| Who can sign in | Only emails you add as **Test users** (up to 100) | Anyone you authorize in the dashboard |
| Re‑sign‑in | **Every 7 days** (Google expires test refresh tokens) | Stays signed in indefinitely |
| Warning screen | "Unverified app" — click **Advanced → Go to app** | Same one‑time warning, then remembered |

**Recommended:** click **Publish App** (Production) so nobody has to sign in
again every week. Because your app is unverified, each person sees a one‑time
"Google hasn't verified this app" screen — that's expected for a personal app.
They click **Advanced → Go to Family Dashboard (unsafe)** to continue. This is
*your* app reading *their* calendar; it's safe.

> If you'd rather not publish, stay in Testing mode and add each family
> member's email under **Test users** — just know they'll re‑sign‑in weekly.

---

## Step 4 — Create the OAuth Client ID

1. Go to **APIs & Services → Credentials**
   (<https://console.cloud.google.com/apis/credentials>).
2. Click **+ Create Credentials → OAuth client ID**.
3. **Application type:** choose **Web application**.
4. Give it a name like `Dashboard Web Client`.

![Create OAuth client](img/oauth-04-create-client.png)

### Authorized JavaScript origins — the important part

Under **Authorized JavaScript origins**, click **+ Add URI** and add the
address(es) you'll open the dashboard at. **Add each one exactly, with no
trailing slash:**

- Your Pi's local address — e.g. `http://192.168.1.50`
  *(Set a static/reserved IP for the Pi in your router so it never changes.)*
- Your remote tunnel URL, if you set one up later — e.g.
  `https://dashboard.yourdomain.com`

> ⚠️ **`.local` addresses do not work.** Google permanently rejects hostnames
> like `http://dashboard.local`. Use the Pi's **IP address** on your network,
> or a real domain via the Cloudflare tunnel. You do **not** need to add an
> "Authorized redirect URI" — this app uses a popup sign‑in flow.

![Authorized JavaScript origins](img/oauth-05-authorized-origins.png)

5. Click **Create**. Google shows your **Client ID** and **Client Secret** —
   keep this dialog open for the next step.

![Client ID and secret](img/oauth-06-client-credentials.png)

---

## Step 5 — Paste the credentials into the dashboard

1. Open your dashboard and go to **Settings → Admin**.
2. Paste the **Client ID** and **Client Secret** into the Google OAuth fields
   and **Save**.
3. Go to **Settings → My Account** and click **Sign in with Google**. The first
   account to sign in becomes the **owner**.
4. As the owner, open **Settings → Family Members** and **Authorize** each
   family member's email so they can sign in too.

That's it — your calendars will start appearing on the display.

---

## Adding the URL later (e.g. after setting up remote access)

If you add a Cloudflare tunnel (or change the Pi's address) **after** this
setup, you must add the new URL as an authorized origin:

1. Go to **APIs & Services → Credentials**
   (<https://console.cloud.google.com/apis/credentials>).
2. Click your **OAuth 2.0 Client ID** (click the **name**, not the download
   icon).
3. Under **Authorized JavaScript origins**, click **+ Add URI**, enter the new
   `https://…` URL, and **Save**.

Changes can take a few minutes to take effect.

---

## Troubleshooting

- **"Error 400: redirect_uri_mismatch" or "origin not allowed"** — the address
  in your browser's bar isn't in **Authorized JavaScript origins**. Add the
  exact URL (scheme + host, no trailing slash) and wait a few minutes.
- **Sign‑in works but says "This dashboard is private"** — your email isn't
  authorized yet. The owner must add it under **Settings → Family Members**.
- **Members get signed out every week** — your app is in **Testing** mode.
  Publish it (Step 3) for sessions that persist.
- **"Access blocked: app not verified"** — expected for a personal app. Click
  **Advanced → Go to … (unsafe)** to continue.
