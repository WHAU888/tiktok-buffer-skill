# TikTok via Buffer: a Claude skill

Lets Claude write TikTok captions, schedule or draft TikTok posts through your Buffer account, and tell you how your posts are doing.

You talk to Claude normally:

- "Write a caption for a clip of my dog catching a frisbee"
- "Schedule ~/Movies/dance.mp4 for Saturday 8pm with that caption"
- "Save it as a Buffer draft instead"
- "How did my TikToks do this week?"

Claude always shows you a preview and waits for your **yes** before anything is uploaded, scheduled or posted.

---

## What you need

| | Needed for | Cost |
|---|---|---|
| **Claude Code** (desktop app or terminal), with Python 3.10+ | Everything | — |
| **TikTok account** | Posting | Free |
| **Buffer account** with TikTok connected | Scheduling, drafts, stats | Free plan works |
| **Buffer API key** saved on your computer | Scheduling, drafts, stats | Free |
| **Cloudflare R2** (optional) | Posting video files straight from your computer | Free tier is usually enough; Cloudflare asks for a payment method |

Captions and hashtags work with none of the accounts.

---

## Install

**As a Claude Code plugin (recommended).** In a Claude Code session:

```
/plugin marketplace add WHAU888/tiktok-buffer-skill
/plugin install tiktok-buffer@whau888-skills
```

Or from a terminal:

```bash
claude plugin marketplace add WHAU888/tiktok-buffer-skill
claude plugin install tiktok-buffer@whau888-skills
```

Start a new session and ask "write a TikTok caption for…" to check it loads. The first time, Claude gives you a short briefing about what's needed.

To get updates: `claude plugin marketplace update whau888-skills`, then `claude plugin update tiktok-buffer@whau888-skills` (or turn on auto-update under `/plugin` → Marketplaces).

**Or as a plain skill.** Copy `skills/tiktok-buffer/` to `~/.claude/skills/tiktok-buffer/`.

## Set up

### 1. TikTok
Sign up in the TikTok app. For a brand, consider a Business account (Settings → Account → Switch to Business Account).

### 2. Buffer
1. Sign up at buffer.com.
2. In Buffer, connect a channel → TikTok, and sign in to TikTok when asked.
3. Optional: schedule one test post in Buffer's website to make sure it works.

### 3. Buffer API key
1. Go to https://publish.buffer.com/settings/api and create a personal key. Copy it (it may be shown only once).
2. In Terminal, run:
   ```bash
   mkdir -p ~/.config/tiktok-buffer && pbpaste > ~/.config/tiktok-buffer/api_key && chmod 600 ~/.config/tiktok-buffer/api_key
   ```
   (Type it in, or paste it before copying the key, so your clipboard still holds the key.)
3. Check it saved (shows the length, not the key): `wc -c ~/.config/tiktok-buffer/api_key`
4. Clear your clipboard: `pbcopy < /dev/null`
5. Ask Claude "check my Buffer connection". It lists your TikTok channel without posting anything.

On Windows or Linux, save the key into the same file with a text editor, or set the `BUFFER_API_KEY` environment variable.

### 4. Optional: Cloudflare R2 (post files from your computer)
Buffer can only fetch videos from a public web link. R2 gives Claude somewhere to put the clip for that.

Follow [`skills/tiktok-buffer/references/r2-setup.md`](skills/tiktok-buffer/references/r2-setup.md), then ask Claude "set up R2 storage for the TikTok skill". It gives you the exact `storage.py setup` command to run in Terminal (the path depends on where the plugin is installed). The setup asks for the details (keys are typed hidden) and runs a quick upload test.

---

## Good to know

- **You approve every post.** One yes covers one post. If anything changes, you see a new preview.
- **Uploaded clips are public by link** until the post goes out, then Claude deletes them. The file name is random, but anyone with the link could watch. Think twice for clips of children or private moments. You can always post those from the TikTok app instead.
- **Drafts** stay in Buffer until you schedule them on buffer.com. Their clips stay uploaded until then.
- **No TikTok sounds or privacy settings via Buffer.** Add music before exporting, or post from the TikTok app. Posts use your account's default privacy.
- **TikTok app drafts aren't visible** to Buffer or Claude.
- **Stats update about once a day** in Buffer.
- **Keys stay on your computer** in `~/.config/tiktok-buffer/`. Never paste them into chat. If one leaks, delete it in Buffer or Cloudflare and make a new one.
- **Buffer free plan limits** apply (posts per channel, API requests).

## What's in this repo

| File | Purpose |
|---|---|
| `.claude-plugin/` | Plugin and marketplace files for Claude Code |
| `skills/tiktok-buffer/SKILL.md` | Instructions Claude follows |
| `skills/tiktok-buffer/scripts/status.py` | Checks what's set up (never shows keys) |
| `skills/tiktok-buffer/scripts/buffer.py` | Talks to Buffer: channels, posts, stats |
| `skills/tiktok-buffer/scripts/post_file.py` | Uploads a local clip, schedules it, cleans up afterwards |
| `skills/tiktok-buffer/scripts/storage.py` | Sets up and tests R2 storage |
| `skills/tiktok-buffer/references/` | Buffer API notes and the R2 setup guide |

No keys or personal settings are stored here. Each person needs their own accounts and keys, kept in `~/.config/tiktok-buffer/` on their own computer.

The scripts use only the Python standard library: no `pip install` needed.

## Remove

```bash
claude plugin uninstall tiktok-buffer@whau888-skills
```

Then delete `~/.config/tiktok-buffer/` to remove saved keys and settings, and delete the API key in Buffer (and the R2 token in Cloudflare).

## License

MIT. Not affiliated with TikTok, Buffer or Cloudflare.
