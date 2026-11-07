# DESIGN-HANDOFF.md — Snapshot Recovery Web App

Read `UI-BRIEF.md` first. This file adds only the decisions made in the design mockups that the brief leaves open. Where the two conflict, the brief wins, except for the status colours below, which were changed on purpose.

`mockups/*.dc.html` are design reference files, not production code. They render in a design canvas runtime (`support.js`), so they won't open as plain HTML pages. Read them for layout, sizes, copy and behaviour. `mockups/Main.dc.html` holds the whole Browse screen. Its `<script>` block (`model()` and `renderVals()`) is a working reference for the timeline scale, change detection, labels, date snapping and download names. The other Browse files only import Main with different props. `canvas.json` lists every screen and its title.

All data in the mockups is made up: server "Studio Server", the shares, the files and the user "mlaurent".

## Screens (mockup → brief)

| Mockup | Shows |
|---|---|
| SignIn | Sign in (tweak `showError` shows the destructive Alert) |
| Shares | Pick a share |
| Main | Browse as of a past time, hover card on a changed row |
| Detail | File history Sheet open |
| DatePicker / Snapped | Popover + Calendar; toast saying which version it snapped to |
| Dark | Dark theme |
| Missing / Empty / Denied / Loading / Blank | The five folder states |
| Confirm / TooLarge / Progress | Folder download: confirm, refuse, zip progress |

## Theme

- shadcn/ui `new-york`, base colour `zinc`, light + dark. Follows the system setting by default; a header button offers System, Light and Dark.
- Font: Geist (UI) and Geist Mono (download file names, Kbd).
- Status colours: add these as theme tokens in `globals.css` (light / dark). Never hardcode them in components.

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--status-deleted` | `#dc2626` | `#f87171` | deleted row text, Deleted badge background (white text), filter dot |
| `--status-deleted-bg` | `#fef2f2` | `#1f0a0a` | deleted row background tint |
| `--status-changed` | `#15803d` | `#4ade80` | changed row text, Changed badge text/border, differing-value dot, filter dot |
| `--status-changed-bg` | `#f0fdf4` | `#052e16` | Changed badge background |
| `--timeline-selected` | `#2563eb` | `#3b82f6` | selected point marker, band from selected point to Now |
| `--timeline-track` | `#e7e7ea` | `#29292d` | panel behind the timeline, so its start and end are visible |

- Unchanged rows use `--foreground` for every column (no grey columns).
- Red is used for deleted items and for the failed sign-in Alert. Nothing else is red.

## Browse layout (1440 × 960 reference)

- Header 72 px, 24 px padding on the sides, so the logo has the same space on all sides: server name on the left; "All shares", the theme button and the user `DropdownMenu` on the right.
- Below the header, 24 px around and between zones. Each zone is its own box: 1 px border, 10 px radius.
- Time section box, muted background, 16 px padding on all sides:
  - Row 1, 28 px: "As of" + point label (17 px, semibold) + full date in muted text. At Now, just "As of Now".
  - Filter `ToggleGroup`: All / Changed / Deleted, each with its count. Hidden at Now.
  - Step ← → buttons with a `Tooltip` holding `Kbd` ← →.
  - "Pick a date" (`Popover` + `Calendar`).
  - "Back to Now" (primary button, hidden at Now).
  - Row 2, 12 px below: the timeline, 60 px. Its marks sit as far from the top edge as the labels from the bottom.
- `Breadcrumb` box, 40 px, starting at the share name, above the listing.
- Listing box: `Data Table`. Header 28 px, rows 22 px, no row borders, 13 px text.
  - Columns: checkbox 40 · status 84 · Name (flex) · dot 20 · Date Modified 208 · Size 96 (right-aligned) · Kind 208.
  - Status column: every row has a 60 px pill, so the column reads as one: "Changed", "Deleted", or an empty outlined placeholder. Always shown, also at Now.
  - Dot column: the dot marking a date that differs, so the dates stay aligned.
- Item count under the listing, bottom left, 12 px below it: "34 files and 56 folders", "23 folders", "5 files", "1 file", "1 folder". It counts the rows shown, so it follows the filter. Empty while loading and when a folder state replaces the listing. When items are selected, a 36 px bar appears below the listing with "N selected" and a Download button.
- File detail `Sheet`: 384 px box on the right, level with the breadcrumb box. It pushes the breadcrumb and listing narrower instead of overlaying them: no overlay, non-modal. It opens for files only.

## Timeline

- Available points assumed: hourly for the last 48 h, daily at 00:00 for the 14 days before that, weekly (Sunday 00:00) before that, then Now at the far right.
- Horizontal position uses a square-root scale: `x = L + (R − L) × (1 − sqrt(ageHours / maxAgeHours))`, so recent points cluster on the right.
- Ticks: hourly 8 px tall, daily/weekly 12 px tall, 1 px wide. Each tick has a hit area about 7 px wide.
- Faint vertical day marks with left-aligned labels: Aug 9 … Sep 20, Sep 27, Yesterday, Today.
- Selected point: 2 px line + 10 px dot in `--timeline-selected`, plus a light tint band from the selected point to Now.
- Keyboard: ← / → step one point. Esc closes the menu, dialog and Sheet. Handle the keys on the Browse root element, not on `window`.

## Labels

- Point labels:
  - Today and yesterday are relative: "Today, 2:00 PM", "Yesterday, 9:00 AM".
  - Older hourly points: "Sun, Sep 27, 4:00 PM".
  - Midnight (daily/weekly) points are labelled as the end of the previous day: "Sat, Sep 26, end of day".
- Date Modified follows Finder: "Today at 11:02 AM", "Sep 12, 2026 at 2:00 PM".
- Sizes: KB / MB (1 decimal) / GB (2 decimals), decimal units as in Finder. Folders show "--".

## Change indicators

Each row compares the item's state at the selected point with its state now.

- **Deleted:** existed then, gone now. Red row text, red row tint, solid red "Deleted" pill in the status column, red dot in the dot column.
- **Changed:** a different version now. Green row text, "Changed" outline pill in the status column, a green dot in the dot column if Date Modified differs, and one before Size if the size differs.
- **Unchanged:** an empty outlined pill, no dot.
- **Didn't exist yet:** not listed.
- Hover card on changed rows: a Then / Now grid for Modified and Size.
- Hover card on deleted rows: "Not in this folder now. Last seen as of <label>."
- The hover card opens above the row in the lower half of the list and below it in the upper half.
- Row `ContextMenu`: "Download this version" (or "Download as zip" for folders), and "Show all versions" for files.

## File history Sheet

- Header: icon, name, kind, close button.
- A note line with a badge, either "Different now than as of <label>." or "Not in this folder now. Last seen as of <label>."
- "N earlier copies", then one card per distinct version:
  - "Now, in your folder" (size · modified), with no download button.
  - Each earlier version: its modified date, size, "As of <first point> → <last point>", "Saves as <file name>", and a Download button.
  - The version shown at the selected time is outlined in `--timeline-selected` with a "Viewing" badge.
- Versions that exist in no available point are left out.

## Date picker snapping

- Days outside the available range are disabled.
- Picking a day selects the last point whose label day is that day. Toast: "Showing <label>" / "The last version from <day>."
- If that day has no point, the picker selects the point nearest the end of that day. Toast: "No version from <day>" / "Showing <label>, the closest one."

## Downloads

- File name: `Name (YYYY-MM-DD HH.MM).ext` using the version's modified time, e.g. `Logo_final (2026-09-12 14.00).psd`.
- A folder zip uses the selected point's time: `Retouched (2026-09-28 09.00).zip`.
- Any folder opens a confirm `AlertDialog` first: "Download “X” as a zip?", with the size and a note that preparing it can take several minutes. Button: "Download 6.4 GB".
- Folders over 20 GB are refused with an `AlertDialog`: "“X” is too large to download at once", with the size, the limit and "ask IT". The 20 GB limit is an assumption; make it configurable.
- Zip progress: a `Toast` with `Progress`, "a of b · a few minutes left", and a Cancel button.
- File downloads: a toast "Downloaded / Saved to your Mac as <name>".

## Folder state copy

| State | Title | Body | Action |
|---|---|---|---|
| Didn't exist | This folder didn’t exist yet | As of <label>, there was no <folder> folder here. The earliest version that has it is <label>. | Go to <label> |
| Empty then | This folder was empty then | As of <label>, <folder> existed but had nothing in it. Try a later time. | Step forward in time |
| No permission | This folder isn’t available to you | Your account can’t open it. If you need something from it, ask IT. | none |
| Empty share | Nothing here | <share> had no files as of <label>. (At Now: "<share> has no files right now.") | none |
| Loading | skeleton rows under the table header | | |

## Not designed yet

- The "no shares available" `Empty` state on the share picker.
- Image thumbnails (the brief marks these as later, not v1).
