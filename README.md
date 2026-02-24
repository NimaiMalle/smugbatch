# SmugBatch

A command-line tool that batch-creates [SmugMug](https://www.smugmug.com/) galleries with [smart rules](https://www.smugmug.com/help/smart-rules) (keyword and date filters).

## What does this do?

If you shoot events (competitions, tournaments, sports) and upload hundreds of photos to SmugMug, you probably need to split them into per-person or per-group galleries. SmugMug's smart rules can do this automatically based on keywords and dates, but setting them up one gallery at a time through the web UI is tedious.

SmugBatch lets you define all your galleries in a simple YAML file and creates them in one go - including the smart rules, privacy settings, passwords, and everything else.

## Prerequisites

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** (Python package manager) - install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- A **SmugMug** account with API access

## Quick start

### 1. Install

```bash
git clone https://github.com/NimaiMalle/smugbatch.git
cd smugbatch
uv sync
```

### 2. Get API credentials

Apply for an API key at <https://api.smugmug.com/api/developer/apply>.

### 3. Create config file

Create `~/.smugbatch/config.yaml`:

```yaml
oauth:
  consumer_key: "YOUR_API_KEY"
  consumer_secret: "YOUR_API_SECRET"
  access_token: ""
  access_token_secret: ""

session:
  smsess: ""

user:
  nickname: "YourSmugMugNickname"
```

### 4. Set the session cookie

Smart rules use an internal SmugMug endpoint that requires a browser session cookie (the public API doesn't support smart rules).

1. Log in to [smugmug.com](https://www.smugmug.com) in your browser
2. Open DevTools (F12) > Application > Cookies > look for `SMSESS`
3. Copy the value into your config file under `session.smsess`

> **Note:** This cookie expires periodically. If smart rules stop working, grab a fresh one.

### 5. Authenticate

Run the OAuth flow (one-time):

```bash
uv run smugbatch auth
```

This opens your browser to SmugMug's authorization page. Grant access, then paste the 6-digit PIN back into the terminal. Tokens are saved to your config file and don't expire.

Verify everything works:

```bash
uv run smugbatch auth --check
```

### 6. Create a batch file

See [`example-batch.yaml`](example-batch.yaml) for the full format. Here's the gist:

```yaml
event: "2026-Spring-Recital"
parent_folder: "/Dance/Studio/2026-Recital"
date_start: "05/10/2026"
date_stop: "05/10/2026"
common_keywords:
  - "recital"
privacy: "Unlisted"
use_unlisted: true
match: "All"

gallery_settings:
  SecurityType: "Password"
  Password: "changeme"

galleries:
  - "Jane Doe"
  - "John Smith"
  - "Group A"
```

Each gallery entry becomes:
- **Gallery name** - the display name (e.g. "Jane Doe")
- **URL name** - derived by replacing spaces with hyphens (e.g. "Jane-Doe")
- **Smart rule keywords** - all `common_keywords` plus the gallery name itself
- **Smart rule date filter** - the `date_start`/`date_stop` range

### 7. Run it

```bash
uv run smugbatch batch my-event.yaml
```

To test with just the first gallery:

```bash
uv run smugbatch batch my-event.yaml --limit 1
```

It's safe to re-run - existing galleries with smart rules are skipped.

## Batch file reference

### Top-level fields

| Field | Required | Description |
|---|---|---|
| `event` | yes | Event name (for your reference, not used by the API) |
| `parent_folder` | yes | SmugMug folder path where galleries are created |
| `date_start` | yes | Start date for smart rule filter (MM/DD/YYYY) |
| `date_stop` | yes | End date for smart rule filter (MM/DD/YYYY) |
| `common_keywords` | no | Keywords added to every gallery's smart rule |
| `privacy` | no | `"Public"`, `"Unlisted"`, or `"Private"` (default: `"Unlisted"`) |
| `use_unlisted` | no | Include unlisted galleries in smart rule search (default: `true`) |
| `match` | no | `"All"` or `"Any"` - how keywords are combined (default: `"All"`) |
| `gallery_settings` | no | SmugMug API album fields (see below) |
| `galleries` | yes | List of gallery names to create |

### Gallery settings

The `gallery_settings` block accepts any [SmugMug API v2 album field](https://api.smugmug.com/api/v2/doc/reference/album.html). Common ones:

| Setting | API Field | Values |
|---|---|---|
| Access | `SecurityType` | `"None"`, `"Password"` |
| Visitor Password | `Password` | string |
| Allow Downloads | `AllowDownloads` | true/false |
| Download Password | `DownloadPassword` | string |
| Show Sharing | `Share` | true/false |
| Allow Comments | `Comments` | true/false |
| Proof Delay | `ProofDays` | integer (0 = off) |
| Show Filenames | `Filenames` | true/false |
| Slideshow | `Slideshow` | true/false |
| Map Features | `Geography` | true/false |
| Sort Method | `SortMethod` | `"Date Taken"`, `"Date Uploaded"`, etc. |
| Sort Direction | `SortDirection` | `"Ascending"`, `"Descending"` |

## CLI reference

```
smugbatch auth            # Run OAuth flow
smugbatch auth --check    # Verify credentials
smugbatch batch FILE      # Create galleries from batch file
smugbatch batch FILE --limit N        # Only process first N galleries
smugbatch batch FILE --force-settings # Re-apply settings even if unchanged
```

## How it works

For each gallery in your batch file, SmugBatch:

1. Checks if the gallery already exists (by URL name) - skips if it already has smart rules
2. Creates the gallery under the parent folder (or uses the existing one)
3. Applies `gallery_settings` via the SmugMug v2 API
4. Applies smart rules via SmugMug's internal RPC endpoints (keyword + date filters)

### Why the session cookie?

SmugMug's public API (v2) doesn't expose smart rules. This tool uses internal endpoints (`/rpc/gallery.mg` and `/services/api/json/1.4.0/`) that require a browser session cookie (`SMSESS`) rather than OAuth. This is the same mechanism the SmugMug web UI uses.

## Security notes

- Your config file (`~/.smugbatch/config.yaml`) contains OAuth tokens and a session cookie. Keep it private.
- Batch YAML files may contain gallery passwords. The `.gitignore` excludes `*.yaml` by default to prevent accidental commits.
- Never commit your config file or real batch files to version control.

## Contributing

Contributions are welcome! This is a small project - just open an issue or PR.

## License

[MIT](LICENSE)
