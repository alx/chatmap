# Exporting your Signal group chat history

`chatmap` needs the chat as a file. Signal has no "export" button, so we use
[**signal-export**](https://github.com/carderne/signal-export) (`sigexport`),
which reads the **Signal Desktop** database and writes each conversation to
markdown/HTML with attachments.

> **Why Signal Desktop?** Your phone keeps its history in an encrypted database
> that only the phone's key can open. Signal Desktop keeps its own encrypted
> copy, and its key sits in the OS keyring — which we can read on the machine
> that owns it. So: link Desktop once, let it sync, then export from the desktop
> database.

**Time:** about 10 minutes, most of it waiting for the desktop app to sync.

---

## 0. Before you start

- You need **Signal Desktop** linked to the account that's in the group.
  [Install it](https://signal.org/download/) if you haven't.
- On the phone: **Settings → Linked devices → Link new device**.
- Let it finish syncing. This is the part that matters: Desktop only receives
  history for the period since it was linked, plus what the phone hands over on
  first link. **Messages from years ago may not be present.** If you need very
  old history, link the desktop app and leave it running for a while.

Verify the group is there before going further.

## 1. Install `sigexport`

Pick one:

```bash
# zero-install, run it straight from PyPI
uvx --from signal-export sigexport --help

# or install it permanently
pipx install signal-export
```

`sigexport` pulls in `sqlcipher3` (the encrypted SQLite driver) and a few crypto
packages. If the install fails on your platform, the
[upstream README](https://github.com/carderne/signal-export) has
Windows/WSL and NixOS notes.

## 2. Close Signal Desktop

**Quit Signal completely** — not just the window. If the app still holds the
database open you'll get an `I/O disk` error, because SQLite makes a
read-only file for a live reader.

```bash
# Linux
pkill -f signal-desktop

# macOS
osascript -e 'quit app "Signal"'

# Windows: right-click the tray icon → Quit
```

## 3. Find your Signal directory

`sigexport` auto-detects the common locations, but you can be explicit:

| OS | Directory |
|---|---|
| Linux | `~/.config/Signal` |
| Linux (Flatpak) | `~/.var/app/org.signal.Signal/config/Signal` |
| macOS | `~/Library/Application Support/Signal` |
| Windows | `%APPDATA%\Signal` |

It must contain a `sql/db.sqlite`. Check:

```bash
ls ~/.config/Signal/sql/db.sqlite
```

## 4. Provide the decryption key

Signal Desktop encrypts `db.sqlite` with a random key, and protects *that* key
with your OS keyring. `sigexport` retrieves it for you — this is the step where
most people get stuck, so here's what happens on each OS.

### Linux (GNOME / libsecret) — most common

`sigexport` shells out to `secret-tool`:

```bash
sudo apt install libsecret-tools      # Debian/Ubuntu
sudo dnf install libsecret            # Fedora
```

Then it should just work. If it prints
`Your Signal data key is encrypted, and requires a password`, fetch the secret
yourself and pass it in:

```bash
sigexport --password="$(secret-tool lookup application Signal)" ~/signal-export
```

> **No `secret-tool`, or no DBus session?** Read the keyring directly. This is
> the same secret, fetched over DBus:
>
> ```bash
> pip install --user secretstorage      # or: uv run --with secretstorage python …
> python3 - <<'EOF'
> import secretstorage
> conn = secretstorage.dbus_init()
> for col in secretstorage.get_all_collections(conn):
>     for item in col.get_all_items():
>         if item.get_attributes().get("application") == "Signal":
>             print(item.get_secret().decode())
> EOF
> ```
>
> Use that output as `--password=…`. If several secrets come back, the one that
> works is the one labelled `Chromium Safe Storage` with `application: Signal`.

### Linux (KDE / KWallet)

```bash
sigexport --password="$(kwallet-query kdewallet -f 'Chromium Keys' -r 'Chromium Safe Storage')" ~/signal-export
```

### macOS

`sigexport` reads the login keychain automatically. If macOS prompts
*"security wants to access key Signal Safe Storage in your keychain"*, click
**Always Allow**.

Manual fallback:

```bash
security find-generic-password -ws "Signal Safe Storage"
sigexport --password="<that output>" ~/signal-export
```

### Windows

The key is protected with DPAPI under your Windows account, and `sigexport`
reads it from Signal's `Local State`. Run `sigexport` **as the same user** that
runs Signal Desktop. From WSL this generally does not work (different user
context) — run it from native Windows Python instead. See upstream
[`INSTALLATION.md`](https://github.com/carderne/signal-export/blob/main/INSTALLATION.md).

### If it still fails

```
Failed to decrypt Signal password: Padding is incorrect.
```

This means the keyring secret is not the one Signal used — usually because
Signal was re-installed, the keyring was recreated, or you're reading another
user's keyring. Signal's `config.json` names the backend in
`safeStorageBackend`; if it says `gnome_libsecret`, the secret really is in
GNOME's Login collection.

## 5. List the chats

```bash
sigexport --password="$(secret-tool lookup application Signal)" --list-chats
```

You'll get a pipe-separated list of every conversation: contacts, groups,
and `Note to self`. Find your group's exact name — including emoji and
punctuation, which matter for the next step.

## 6. Export the group

```bash
sigexport --password="$(secret-tool lookup application Signal)" \
  --chats "Japan 2028 ;)" \
  ~/signal-export
```

Notes on `--chats`:

- The value is the **exact** chat name. If it doesn't match, you'll see
  `No chats to export, didn't do anything!` — re-check against `--list-chats`.
- Pass several names, comma-separated, to export more than one:
  `--chats="Trip A,Trip B"`.
- Omit `--chats` entirely to export **everything** (slow, and heavy on
  attachments).
- Complain about a name containing `,`? Export everything and delete the
  folders you don't need.

Useful extras:

```bash
# limit to a date range
sigexport --chats "Japan 2028 ;)" --start 2026-01-01T00:00:00+01:00 ~/signal-export

# merge into a previous export without overwriting anything
sigexport --old ~/signal-export-old ~/signal-export
```

## 7. Check the output

```
~/signal-export/
└── Japan2028/                 ← punctuation stripped from the group name
    ├── chat.md                ← what chatmap reads
    ├── data.json
    ├── index.html             ← styled chat view, open in a browser
    └── media/                 ← attachments
```

```bash
head -20 ~/signal-export/Japan2028/chat.md
```

You should see lines like:

```
[2026-11-02 22:20:31] Marie: https://app.mokomoko.com/property/0f3a1c2d-...
```

That's exactly the format `chatmap` expects. Sanity-check the link count:

```bash
chatmap links ~/signal-export/Japan2028/chat.md
```

## 8. Build the map

```bash
chatmap run ~/signal-export/Japan2028/chat.md \
  -o japan-2028 --hint Japan --noindex -t "Japan 2028"

python3 -m http.server -d japan-2028 8000
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `I/O disk` or `database is locked` | Signal is still running — quit it fully and retry |
| `No local users found` | You're pointing at the wrong directory; check for `sql/db.sqlite` |
| `No chats to export` | Name mismatch. Copy it from `--list-chats` exactly |
| `Padding is incorrect` | Wrong keyring secret — see step 4 |
| `secret-tool: command not found` | `apt install libsecret-tools`, or use the Python fallback |
| Empty or very short `chat.md` | Desktop hasn't synced that history — see step 0 |
| Attachments missing | `sigexport` copies from `attachments.noindex`; deleted media is gone |

## Privacy

The export contains the **entire conversation** — every message, every
attachment, every phone number in the group — in plaintext on disk.
`chatmap` only sends requests for link metadata and geocoding, but
`sigexport`'s output is a full copy of the chat.

- Keep `~/signal-export` out of git. `chatmap` ships a `.gitignore` for its
  output; add your export directory too.
- Delete it when you're done: `rm -rf ~/signal-export`.
- Use `--offline` if you don't want *any* network requests beyond the URLs
  already in your chat.
