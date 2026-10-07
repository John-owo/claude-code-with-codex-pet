# claude-code-with-codex-pet

[繁體中文](README.md) · **English**

A Claude Code (CC) mod that lets Claude Code and Codex share one desktop pet.

It uses Codex's own pets (`~/.codex/pets`) and puts an always-on-top pet on your desktop that follows what every CC session and Codex thread is doing, modelled on the pet in the Codex app.

> A personal project, not affiliated with OpenAI or Anthropic. Claude Code's mod interface (function hooks) is early access and may change in later releases.

## Screenshots

<table>
  <tr>
    <td align="center"><img src="docs/screenshots/desktop-pet.png" alt="The pet collapsed: the pet, its badge and the most urgent card" width="320"><br>Collapsed: the pet, its badge and the most urgent card</td>
    <td align="center"><img src="docs/screenshots/activity-list.png" alt="The activity list, expanded by hovering the pet" width="320"><br>Hovered: every CC session and Codex thread</td>
  </tr>
</table>

<p align="center"><img src="docs/screenshots/states.png" alt="The pet's animation for each state: idle, working, needs you, done (unread), blocked, just finished" width="720"></p>

Left to right in the last picture: idle, working, needs you, done (unread), blocked, just finished. The screenshots are drawn by the pet's own rendering code with example cards, and the interface text is in Traditional Chinese. The pet is Debug Duck from the [codex-pet-share](https://github.com/portons/codex-pet-share) project (MIT).

## Works with Codex community pets

The mod has no pet format of its own: it reads Codex's pet folder, `${CODEX_HOME:-~/.codex}/pets/<id>/` (`pet.json` + `spritesheet.webp`). So:

- **Any pet Codex can use works here**: the one you picked in Codex, ones hatched with hatch-pet, ones you made yourself.
- **Both v1 and v2 sheets work**: v1 is 1536×1872 (9 rows), v2 is 1536×2288 (9 rows plus two rows of 16 look directions).
- **Community pets from [codex-pets.net](https://codex-pets.net/) work as they are.** Every pet there has an install command; once installed it shows up in `/pet list`:

  ```bash
  npx codex-pets add <pet id>
  ```

  The pet id is the last part of its page's address, for example `nino` in `https://codex-pets.net/#/pets/nino`. The command installs into `~/.codex/pets/<id>/`, the folder both Codex and this mod read, so one install serves both. Switch to it with `/pet <id>`, or pick it in Codex.

## Features

- **One pet for both**: reads Codex's pet folder, so pets hatched with hatch-pet show up in CC too. It follows the pet selected in Codex by default; when Codex has a cloud pet selected, it finds the local copy through Codex's migration records.
- **Activity list**: one card per CC session and Codex thread, with its title, status (needs you / blocked / done / working), what it is doing and how long ago. Ordered as in the original: needs you → blocked → done → working.
- **Click a card to open the conversation**: `claude://code/continue?session=…` for CC, `codex://threads/…` for Codex. A done card is cleared once opened.
- **The pet's animation** follows the most urgent card; it jumps when something has just finished and runs left or right while you drag it.
- **A badge** counts what needs you; hovering the pet expands the list.
- **Right-click menu**: size (small / medium / large), keep the list open, clear done cards, wave, reload, hide, close.
- **`Win+Alt+O`** shows or hides the pet (Codex uses `Win+Alt+P`).
- Shows still frames when Windows animation effects are turned off.

## Requirements

- Windows 10 / 11
- Claude Code (the desktop app's Code tab or the CLI), a version with function-hook mods
- Python 3 with [Pillow](https://pypi.org/project/pillow/) (`py -3 -m pip install pillow`); the pet window is drawn with tkinter
- The Codex app, and at least one pet in `~/.codex/pets/<name>/` (`pet.json` + `spritesheet.webp`)

## Install

1. Clone this repository to a fixed place, for example:

   ```bash
   git clone https://github.com/John-owo/claude-code-with-codex-pet.git "%USERPROFILE%\.claude\mods\codex-pet"
   ```

2. Add the folder to `env` in `~/.claude/settings.json`, so every CC session loads it:

   ```json
   {
     "env": {
       "CLAUDE_CODE_PLUGIN_DIRS": "C:\\Users\\<your user>\\.claude\\mods\\codex-pet"
     }
   }
   ```

   Separate it from other folders already there with `;`.

3. Start a new CC session; the pet appears on its own.

## Commands

In the CC prompt:

| Command | What it does |
|---|---|
| `/pet` or `/pet list` | Lists the pets in `~/.codex/pets` |
| `/pet <id>` | Switches to that pet |
| `/pet codex` | Follows the pet selected in Codex again |
| `/pet-on` | Opens the desktop pet (also `/pet overlay on`) |
| `/pet-off` | Closes the desktop pet (also `/pet overlay off`); the next new session opens it again |
| `/pet show` / `/pet hide` | Shows / hides the small pet above the CC prompt (hidden by default while the desktop pet is on) |
| `/pet reload` | Reloads the pet's sprites |

In the Claude desktop app's command list, `/pet-on` and `/pet-off` appear as `/codex-pet:pet-on` and `/codex-pet:pet-off`. Both spellings do the same thing and are handled by the mod itself, with no model turn.

## Options

The mod's options (`userConfig`):

| Option | Default | What it does |
|---|---|---|
| `python` | empty | A Python with Pillow; when empty it tries `py -3`, `python3`, `python` in turn |
| `pet` | empty | A pet folder to use; when empty it follows the pet selected in Codex |
| `overlay` | `true` | Whether a session opens the desktop pet when it starts |

## How it works

```
CC session ──(the mod writes)──> ~/.codex-pet/sessions/cc-<id>.json ─┐
                                                                      ├─> desktop pet (overlay/pet_overlay.py)
Codex ──(Codex's own conversation logs)── ~/.codex/sessions/... ─────┘
```

- `hooks/register.tsx`: the CC mod. It updates its session's status file on CC's events (a turn starting, tool calls, waiting for your approval, a turn ending), sends a heartbeat every 30 seconds, and starts the desktop pet.
- `overlay/pet_overlay.py`: the desktop pet itself (a Win32 layered window whose whole picture is drawn with Pillow). Only one runs at a time; a newer version takes over from an older one.
- `overlay/sources.py`: reads the CC status files, the Claude desktop app's session records (for titles and the ids its links take), and Codex's `session_index.jsonl` and conversation logs.
- `bake/bake_pet.py`: finds the pet to use and cuts its spritesheet into frames.

Everything is read and written on your own computer; nothing is sent anywhere. The pet's position, size and other preferences live in `~/.codex-pet/overlay.json`, and errors are logged to `~/.codex-pet/overlay-error.log`.

## Known limitations

- Codex's conversation logs have no event for waiting on your approval, so Codex cards never show "needs you".
- Whether you have read a finished Codex thread is known only inside the Codex app, so the pet can't see it; click the card or its × to clear it, and it clears itself after 3 hours.
- Looking toward the pointer needs a v2 pet (with the two rows of 16 directions) and is not implemented yet.
- A cloud pet that exists only on the Codex side, with no local copy, can't be read.

## Development

```bash
claude plugin validate .
claude plugin test .
```

## License

[MIT](LICENSE)
