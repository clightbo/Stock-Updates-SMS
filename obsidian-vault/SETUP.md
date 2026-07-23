---
tags:
  - setup
  - command-center
---

# Finance Command Center — Setup

Follow these steps once on your HP laptop (Windows). After that, notes sync via Git and stay usable in both Obsidian and Cursor.

## 1. Open the vault

1. Clone this repo (or pull latest) onto your machine.
2. Install [Obsidian](https://obsidian.md).
3. **Open folder as vault** → select the `obsidian-vault` folder inside this repo.
4. Confirm Settings:
   - **Files & Links → Default location for new attachments** → `z_Attachments` (already set in `.obsidian/app.json`).
   - **Templates → Template folder location** → `z_Templates`.
   - New notes default to `00_Inbox`.

## 2. Version control (GitHub sync)

Your vault lives inside this private repo, so you do **not** need a separate `obsidian-vault` repository unless you want one.

### Option A — Obsidian Git plugin (recommended)

1. Obsidian → **Settings → Community Plugins → Browse**.
2. Install **Obsidian Git**.
3. Enable it, then in plugin settings:
   - **Vault backup interval**: `10` minutes (or your preference).
   - **Pull updates on startup**: on.
   - **Push on backup**: on (after you trust the setup).
4. Authorize GitHub with a **Personal Access Token** (classic or fine-grained) that can read/write this repo.
5. Result: while you work, notes commit and push automatically.

### Option B — plain Git

```bash
cd path/to/personal-automation-hub
git pull
# edit notes in Obsidian…
git add obsidian-vault
git commit -m "Update finance notes"
git push
```

### Auto-generated markets notes

**Actions → Obsidian vault sync → Run workflow** regenerates:

- `03_Finance_Data/Markets/Calendar.md`
- `03_Finance_Data/Markets/Watchlist.md`
- `01_Projects/Interview Prep/This Week.md`

Pull in Obsidian Git (or `git pull`) to refresh.

## 3. Intelligence plugins

### Smart Connections

1. Community Plugins → install **Smart Connections**.
2. Let it build a local index of your notes.
3. Use the sidebar while writing (e.g. “Private Equity”) to surface related internship / research notes.

### Auto-Tag

1. Community Plugins → install **Auto Tag** (or the Auto Tag plugin you prefer).
2. Add an OpenAI API key in the plugin settings.
3. Set a **monthly spending limit** on the OpenAI billing dashboard (e.g. $5).
4. Use it to clean `00_Inbox` — accept suggested tags so the vault stays searchable without clutter.

## 4. Link to Cursor Pro

1. In Cursor: **File → Open Folder…** and select the repo root (or `obsidian-vault` itself).
2. Press `Ctrl+K` (Windows) / `Cmd+K` (Mac), then type `@` and pick a note.
3. Example:

   > `@03_Finance_Data/Markets/Calendar.md summarize the next high-impact Fed events and sketch a Python checklist to track them.`

4. For research write-ups: `@01_Projects/Project Destined/...` plus `@` a 10-K note from `02_Resources`.

## 5. Visualize

Use Obsidian’s native **Graph View** (left ribbon) to see how finance concepts connect. No separate visualization tool required.

## Folder cheat sheet

| Folder | Put here |
|---|---|
| `00_Inbox` | Fleeting notes; process weekly with Auto-Tag |
| `01_Projects` | Active workstreams with an end state |
| `02_Resources` | Evergreen reference (PDFs, book notes, 10-K excerpts) |
| `03_Finance_Data` | Numbers / CSVs / synced market tables |
| `z_Attachments` | Images, PDFs dropped into notes |
| `z_Templates` | Meeting, 10-K review, daily note formats |
