# UI-BRIEF.md — Snapshot Recovery Web App

## What this is

A web app that lets non-technical staff at a photo/video production company recover files they deleted or overwrote. The files live on a network share they already mount on their Macs. The server keeps automatic point-in-time copies; this app is how they get into those copies without asking IT.

Design for macOS users who know Finder and know nothing about storage.

## The one idea the UI has to get across

**You are looking at your usual share, as it was at a moment in the past.**

Two things move independently: *where* you are in the folder tree, and *when* you are. Changing one must never silently change the other. If I'm three folders deep and I move back in time, I stay three folders deep. If I move across folders, the time I picked stays picked.

Everything below serves that.

## Screens

### 1. Sign in

Username and password, nothing else. Same credentials as their file server. One field for a friendly server name so people maintaining several sites know which one they're on.

### 2. Pick a share

Only shown when more than one share is configured. A short list of share names — the exact names that appear in their Finder sidebar. No folder hierarchy above them. If there's only one, skip this screen entirely.

### 3. Browse (the whole app, really)

A file listing with a time control above it. This is where users spend all their time and it should be the only complex screen.

## Vocabulary

Never use: snapshot, ZFS, dataset, pool, mount, path, restore point, volume.

Use: **version**, **as of**, **earlier copy**, **download**.

The button says **Download**, not Restore. It saves a copy to their Mac; it does not put anything back on the server. That distinction matters and the wording should carry it without a warning banner.

## The time control

The hardest part. Users think in "before I broke it," not in timestamps.

Give them a horizontal timeline of available points in time, newest on the right, with the current live state as the rightmost position and clearly labelled as such (**Now**). Dense clusters of points are normal — hourly for recent days, thinning out to daily and weekly going back. Don't force even spacing; let the visual density communicate that recent history is finer grained.

Selecting a point re-renders the current folder as of that moment. The transition should feel like the same folder changing, not a new page loading — it is the same folder.

Label points by date and time in plain language, relative for recent ones ("Today, 2:00 PM", "Yesterday, 9:00 AM") and absolute beyond that. Never show the underlying version name.

Also offer a plain date picker as an alternative for people who know exactly when things were fine. Snapping to the nearest available point is correct; tell them which one it snapped to.

Keyboard: left/right arrows step through time. This is the fastest way to answer "when did this file change?" and people will use it repeatedly.

## The file listing

Columns: **Name**, **Date Modified**, **Size**, **Kind**. Finder's columns, in Finder's order. Sortable by clicking a header. Folders sort before files.

Rows are folders and files at the current location, as of the selected time. Double-click a folder to enter. Breadcrumb above, starting at the share name, with no way to go above it.

**Change indicators are the payoff.** When the selected time is not Now, mark each row against the live version:

- unchanged since then — no marker
- different then than now — a subtle marker plus the values that differ
- existed then, gone now — marked as deleted, and this is the case most people are here for, so make it findable
- didn't exist yet at that time — simply absent

Show the *then* values in the columns, with the *now* values available on the row (on hover, or in a detail panel) so the comparison is direct. Don't make people open two browser tabs to compare.

### File detail

Selecting a file opens a panel showing that file's history: the points in time where it differs, each with its date, size and modified time, and a download control per version. This answers "which copy do I want?" without hunting through the timeline folder by folder.

Thumbnails for images would earn their keep here — this is a photo and video company, and filenames like `DSC_4471.jpg` tell nobody anything. Treat as a later enhancement, not v1.

## Downloading

- A file downloads directly.
- A folder downloads as a zip, and large ones take a while — show progress and a size estimate before starting, and refuse very large ones with a clear message rather than appearing to hang.
- Downloaded names carry the date so copies don't collide with the live file: `Logo_final.psd` from a Sept 12 version arrives as `Logo_final (2026-09-12 14.00).psd`.
- Multi-select with checkboxes for downloading several at once.

## States that need real design, not afterthoughts

- **Folder didn't exist at the selected time.** Say so, naming the date, and offer to jump to the earliest time it did exist. Do not show an error.
- **Folder existed but is empty at that time.** Different message from the above.
- **No permission.** Say the folder isn't available to them and suggest asking IT. Never reveal a name or path they can't otherwise see.
- **Loading.** Listing a folder inside an old version can take a second or two the first time. Skeleton rows, not a spinner blocking the whole page.
- **Nothing found / empty share.** Plain, unalarming.

## Tone and look

Calm and utilitarian. People arrive here slightly stressed, having lost work. Nothing playful, nothing alarming, no red unless something is genuinely wrong. Dense enough to show 30+ rows without scrolling on a laptop — these are people who look at long file lists all day and will resent a padded-out list.

Light and dark both, following the system setting.

Desktop-first: this is used on a Mac at a desk, next to the Finder window the files are missing from. It should still be usable on an iPad, but don't compromise the desktop density for phone layouts.

## Out of scope

Admin functions, settings screens, creating or deleting versions, putting files back on the server, diffing file contents, sharing links with others.

---

## Component library

All UI elements come from **shadcn/ui** (https://ui.shadcn.com). Components are copied into the project, not installed as a package — the source is ours to edit. Requires Tailwind v4 and the `@/*` path alias. Style: `new-york`. Icons: lucide-react. Do not introduce a second component library.

Where an element below has no shadcn equivalent, build it from Tailwind and the project's theme tokens — never hand-roll something that duplicates an existing component.

### Mapping

**Sign in** — `Card` wrapping a `Field` group: `Input` + `Label` for credentials, `Button` to submit, `Alert` (destructive) for a failed sign-in. Server name as muted `Typography` in the card header.

**Pick a share** — `Item` per share in a vertical stack, or `Card` tiles if only a handful. `Empty` for the no-shares-available case.

**Browse — shell** — `Sidebar` is unnecessary; a single column is enough. Header holds the `Breadcrumb` (starting at the share name, no root-level "up"), a `DropdownMenu` for the signed-in user, and the time control.

**Browse — file listing** — `Data Table` (shadcn's TanStack Table recipe over `Table`, not a standalone component). Sortable headers, `Checkbox` column for multi-select, `Badge` for change indicators, `ContextMenu` on rows for download actions, `Skeleton` rows while a folder loads.

**Change indicators** — `Badge` with distinct variants for *changed* and *deleted*. `Tooltip` or `HoverCard` on the row exposing the current values alongside the historical ones, so the comparison needs no second screen.

**File detail panel** — `Sheet` sliding from the right, containing an `Item` list of versions, each with date, size, modified time and a `Button` to download. Do not use `Dialog` here; the panel should not block the listing behind it.

**Time control** — no shadcn component fits. Build a custom horizontal timeline component, styled entirely with the project's theme tokens so it reads as part of the set. `Slider` is the wrong primitive: available points are irregularly spaced and the spacing itself carries meaning.

Alongside it, `Popover` + `Calendar` (shadcn's Date Picker pattern) for jumping to a known date, with `Toast` confirming which available point it snapped to. `Kbd` in a `Tooltip` to advertise the left/right arrow shortcut.

**Downloads** — `Progress` for zip generation, `Toast` on completion. `Alert Dialog` to confirm before starting a large folder download, stating the size.

**Empty and error states** — `Empty` for every one of them: folder didn't exist yet at this time, folder empty at this time, no permission, nothing found. Each gets its own message per the states section above; do not collapse them into one generic state.

**Theme** — CSS variables, light and dark following the system setting. Set the base colour once in `components.json`; never hardcode colour values in a component.
