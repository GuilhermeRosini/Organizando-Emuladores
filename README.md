# My Games — portable emulation hub

A lobby for emulators that runs from any folder or USB drive. You copy the
folder, the other person **double-clicks "Abrir My Games"** and starts
playing, with a controller or keyboard. Nothing needs to be installed.

## Structure

```
My Games/
├── Abrir My Games          ← the only file to click
├── Emuladores/
│   ├── PlayStation 2/
│   │   ├── Adicionar Jogos/   ← put PS2 games here
│   │   ├── bios/              ← PS2 BIOS
│   │   └── (PCSX2 + dados)    ← do not change
│   ├── Super Nintendo/  Adicionar Jogos/   (Snes9x)
│   ├── Game Boy Advance/ Adicionar Jogos/  (mGBA)
│   ├── GameCube/        Adicionar Jogos/   (Dolphin)
│   ├── Nintendo 64/     Adicionar Jogos/   (RMG)
│   └── Neo Geo/         Adicionar Jogos/ + bios/   (RetroArch)
└── Sistema/                ← hub program (do not change)
```

- **Games:** put each game in its console's `Adicionar Jogos` folder.
  Subfolders are fine. Nothing else needs to be done; the game shows up in
  the hub.
- **Saves and memory cards** are kept in `Emuladores/<Console>/dados/` and
  travel with the USB drive.

## Controller

Connect it over USB or Bluetooth before opening the game. The hub detects
the controller (Xbox, PlayStation, 8BitDo, generic) and configures the
emulator by itself each time a game opens.

| On the controller | Action |
|---|---|
| D-pad / left stick | navigate the hub |
| A / ✕ | open the console / start the game |
| B / ○ | go back |
| **Hold SELECT + START** | **exit the game and return to the hub** |

On the keyboard: ← → to navigate, Enter to open, Esc to go back, typing
searches the game list.

## Neo Geo (The King of Fighters '98 and others)

Uses **RetroArch** with two cores, and the hub picks the right one on its own:

- **`.neo` games** (also inside a `.zip`, as they usually come) → *Geolith*
  core. The file name doesn't matter.
- **MAME-standard romsets** (e.g. `kof98.zip` with `242-p1.p1`… files inside)
  → *FBNeo* core. These **cannot be renamed**.
- **A BIOS is required** in `Emuladores/Neo Geo/bios/`, without unzipping:
  `aes.zip` (home console mode) or `neogeo.zip` (arcade mode). The hub
  warns you if it is missing.
- In-game: **SELECT inserts a coin**, **START** starts, **L3 + R3** opens
  the RetroArch menu.

## PlayStation 2

PCSX2 needs the BIOS from your PS2 in `Emuladores/PlayStation 2/bios/`. The
hub selects it automatically.

## USB drive tips

- Format as **exFAT**: FAT32 does not accept files larger than 4 GB (common
  for PS2) and loses the "executable" permission of `Abrir My Games`.
- Requirements on the other computer: Linux with Python 3 and GTK 4
  (default on Ubuntu, Mint, Fedora). If missing:
  `sudo apt install python3-gi gir1.2-gtk-4.0`.
- If an emulator does not open, the hub shows the error and the full log is
  in `Sistema/logs/<console>.log`.

## Maintenance

- Install or update an emulator: ⚙ → *Atualizar* (or
  `python3 Sistema/app/main.py --baixar todos`).
- Add another games folder (e.g. external HDD): ⚙ → *Adicionar pasta de jogos*.
- Rebuild the executable: `sh Sistema/launcher/compilar.sh`.
- Custom arguments per console in `Sistema/config.json`
  (`"argumentos": ["-x", "{jogo}"]`).

Image credits: `Sistema/app/assets/CREDITOS_IMAGENS.txt`.
