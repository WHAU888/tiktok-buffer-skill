# Setting up Cloudflare R2 for clip uploads

Walk the user through this when `post_file.py` reports `NO_LOCATION`. The user does every step themselves. Never ask for the keys in chat.

Cloudflare's menu names change from time to time; describe the goal of each step if a label doesn't match.

1. **Cloudflare account**: sign up at dash.cloudflare.com (free).
2. **Enable R2**: in the dashboard sidebar open **R2 Object Storage**. Cloudflare asks for a payment method even for the free tier (10 GB storage free per month; short clips that are deleted after posting stay well inside it).
3. **Create a bucket**: e.g. `tiktok-clips`. Location: automatic.
4. **Public link** (for the default "public" mode): open the bucket → **Settings** → **Public access** → enable the **R2.dev subdomain** (or connect a custom domain). Copy the URL, like `https://pub-xxxx.r2.dev`.
   - Cloudflare describes r2.dev as meant for light/development use and rate-limited; that is fine for a few clips. A custom domain is better for heavier use.
   - Optional: add a bucket **lifecycle rule** to delete objects under `clips/` after e.g. 14 days, as a safety net if cleanup is missed (keep it longer than the furthest-ahead scheduled post).
5. **Account ID**: shown on the R2 overview page (or in the dashboard URL).
6. **API token**: R2 → **Manage API tokens** → **Create API token** → permission **Object Read & Write**, limited to the `tiktok-clips` bucket only. Copy the **Access Key ID** and **Secret Access Key** (shown once).
7. **Save it on the Mac**: in Terminal run
   `python3 ${CLAUDE_SKILL_DIR}/scripts/storage.py setup` (replace `${CLAUDE_SKILL_DIR}` with this skill's folder if your tool doesn't fill it in; use `python` on Windows)
   and answer the prompts. Keys are typed hidden. It then uploads a tiny test file, checks the link and deletes it.

Afterwards: `storage.py list` shows locations, `storage.py test` re-runs the check, `storage.py default <name>` changes the default. Running `setup` again with a new name adds a second location.

If the key leaks: delete the token in Cloudflare, create a new one, and re-run `setup` with the same name.
