# User Instructions

## Quick Start

```bash
./start.sh
```

That's it. The script checks your system, installs dependencies, and opens the wizard in your browser. See `docs/INSTALL.md` for manual installation if you prefer.

## What This Tool Does

e2n migrates your Evernote notes to Notion — preserving content, formatting, attachments, and structure. Each Evernote notebook becomes a Notion database, and each note becomes a database entry with its full content as Notion blocks.

## Supported Operating Systems

macOS, Ubuntu, and WSL. Native Windows is not supported (use WSL).

---

## Two Ways to Use e2n

### 1. WebUI Wizard (Recommended)

The wizard walks you through the entire migration step by step:

```bash
e2n-ui --open
# Opens http://127.0.0.1:8787/wizard/
```

**Step 1 — Configure Source:** Point to your `.enex` file(s) and choose a processing directory.

**Step 2 — Configure Notion:** Enter your Notion integration key and test the connection.

**Step 3 — Extract:** Parse your Evernote export into individual notes and resources.

**Step 4 — Import:** Upload everything to Notion (databases, pages, blocks, files).

**Step 5 — Review:** See any exceptions that need attention, then use the Resolution Workbench to fix them.

### 2. Command Line

For automation or scripting, every operation is available via CLI:

```bash
# Extract notes from an ENEX file
e2n --converting -e ~/Downloads/Notebooks/Enduring.enex -d ./processing

# Create Notion page structure
e2n --notion-bootstrap -n "My Migration" -k $NOTION_KEY

# Create databases (one per notebook)
e2n --notion-databases -e ~/Downloads/Notebooks -n "My Migration" -k $NOTION_KEY

# Import notes into Notion
e2n --notion-import -e ~/Downloads/Notebooks -d ./processing -n "My Migration" -k $NOTION_KEY

# Auto-resolve Evernote links post-import
e2n --resolve-evernote-links --exceptions-file ./processing/Enduring/exceptions.txt -k $NOTION_KEY
```

---

## Resolution Workbench

After import, open the workbench to handle exceptions:

```
http://127.0.0.1:8787/resolve/
```

### Navigation Modes

- **By Type** — Work through all exceptions of one category (e.g., all Evernote Links)
- **By Page** — Clean up one note at a time (all exception types for that note)

### Available Actions

| Exception Type | Resolution Options |
|---|---|
| **Evernote Links** | Auto-relink (batch single-match), or manual selection from candidates |
| **Encrypted Content** | Enter passphrase → view decrypted content → permanently decrypt in Notion OR delete block |
| **Failed Resources** | Re-upload from local file path |
| **Empty Title / No Content** | Acknowledge (auto-dismiss) |
| **Unsupported Content** | View in Notion → fix manually → confirm resolved |

### Auto-Relink

After importing all your notebooks, click "Auto-Relink" to batch-resolve Evernote links:
- Searches Notion for pages matching each link's text
- Single exact match → automatically resolved
- Multiple matches → left for manual review
- Zero matches → target note may not have been exported

---

## CLI Parameters

### Modes

| Flag | Purpose |
|---|---|
| `--converting` | Extract notes from ENEX into processing directory |
| `--notion-bootstrap` | Create Notion root pages (Evernote Import + Exceptions) |
| `--notion-databases` | Create one database per notebook |
| `--notion-import` | Import extracted notes as Notion pages with blocks |
| `--resolve-evernote-links` | Post-import Evernote link resolution |
| `--rebuild-exceptions` | Rebuild exception projections from extraction artifacts |
| `--reporting` | Reserved (not yet implemented) |

### Common Options

| Flag | Description |
|---|---|
| `-e`, `--enex-source` | Path to `.enex` file or directory of `.enex` files |
| `-d`, `--processing-directory` | Where extraction output is written |
| `-k`, `--notion-key` | Notion integration key (or set `NOTION_KEY` env var) |
| `-n`, `--notion-root` | Notion parent page title for import structure |
| `-w`, `--workers` | Parallel ENEX files (default: 1) |
| `--exceptions-file` | Path to `exceptions.txt` for link resolution |
| `--resume` | Resume a run with committed operations |
| `--reset-run` | Reset one run ID back to pending |
| `--wipe-local` | Delete local processing output for one run |
| `--wipe-remote` | Archive Notion pages for one run and clear mappings |
| `-v`, `--verbose` | Enable debug logging |

---

## Output Structure

For `Enduring.enex`, extraction creates:

```
processing/
  Enduring/
    notes/
      note_000001.enex
      note_000002.enex
    resources/
      screenshot.png
      document.pdf
      manifest.json
    state.db
    master.txt
    success.txt
    errors.txt
    exceptions.txt
```

| File | Purpose |
|---|---|
| `notes/` | Individual note XML files (one per note) |
| `resources/` | Decoded binary attachments (images, PDFs, audio, files) |
| `resources/manifest.json` | Hash → filepath mapping for the File Upload API |
| `state.db` | SQLite database tracking run state, operations, and resume checkpoints |
| `master.txt` | Full scope: note ID, title, path, tags, exception reasons |
| `success.txt` | Successfully extracted notes |
| `errors.txt` | Notes that failed extraction |
| `exceptions.txt` | Notes with issues needing future attention |

---

## Notion Structure Created

```
[Your Root Page]/
├── Evernote Import/
│   ├── Notebook1 (database)
│   │   ├── Note A (page with content blocks)
│   │   └── Note B (page with content blocks)
│   └── Notebook2 (database)
│       └── ...
└── Evernote Import Exceptions/
    └── Import-Exceptions (database)
        ├── Row: link exception (with URL to exact marker block)
        ├── Row: encrypted content (with hint)
        └── Row: unsupported content (with error details)
```

**Import path and Exception path are always separate.** Exception rows link directly to the affected block in the imported page via block-level URLs.

---

## Content Mapping

| Evernote | Notion |
|---|---|
| Headings (h1-h6) | heading_1, heading_2, heading_3 |
| Bullet lists | bulleted_list_item |
| Numbered lists | numbered_list_item |
| Checkboxes | to_do (with checked state) |
| Block quotes | quote |
| Code / preformatted | code (plain text) |
| Horizontal rules | divider |
| Tables | table + table_row |
| Images | image (via File Upload API) |
| PDFs | pdf (via File Upload API) |
| Audio | audio (via File Upload API) |
| Other files | file (via File Upload API) |
| Bold, italic, underline, strikethrough, inline code | Rich text annotations |
| Hyperlinks | Rich text with link |
| Evernote internal links | Warning callout + exception record |
| Encrypted content | Warning callout + exception record |

---

## Encrypted Content

Evernote encrypted blocks (AES-128) are imported as warning markers. Use the Resolution Workbench to:

1. Enter your passphrase
2. View the decrypted content in your browser (never stored)
3. Choose: permanently decrypt in Notion, or delete the block entirely

---

## Getting a Notion Integration Key

1. Go to https://www.notion.so/my-integrations
2. Click "New integration"
3. Name it (e.g., "e2n migration")
4. Copy the "Internal Integration Secret" (`ntn_...`)
5. Share your target Notion page with the integration (page → ... → Connections → Add)

---

## Troubleshooting

**"No .enex files found"** — Check that your path points to actual `.enex` exports from Evernote.

**"API token is invalid"** — Verify your Notion key. Make sure the target page is shared with the integration.

**Import seems stuck** — The tool respects Notion's rate limit (3 req/sec). Large notebooks take time. Check `/wizard/progress` for status.

**Missing content in Notion** — Check the Resolution Workbench (`/resolve/`) for exceptions. Some content types require manual intervention.
