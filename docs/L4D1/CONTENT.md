# What a complete `left4dead/` looks like, and which depot it comes from

Status: **the depot split is the thing that bites people.** L4D1 is not "one app
folder"; it is four depots plus the language ones, and they are easy to download
one at a time without noticing. Three separate downloads in this project came out
short for this reason, so the mapping is written down here.

And a warning that cost one of those downloads: **the depot names are historical
labels, not tables of contents.** Depot 501 is called "Left 4 Dead binaries" and is
where the loose `resource/`, `scripts/`, `cfg/` client-side trees actually live —
the ones whose absence was killing the boot in `Resource\GameMenu.res`. Whoever
decided in 2008 that the VGUI layouts belonged with the client binary did not name
it that way on SteamDB. Read the depot contents, not the label.

## The depots of app 500

| Depot | Name | Size | What lands in `left4dead/` |
| --- | --- | --- | --- |
| 501 | Left 4 Dead binaries | ~289 MiB | `bin/` and the **loose client-side trees**: `resource/` (`GameMenu.res`, `*UIPanel.res`, `valve_english.txt`), `scripts/`, `cfg/`, and what ships beside them. Confirmed by extracting it after three downloads omitted it on the strength of its name. The Windows DLLs in here are *not* used — this port ships its own |
| 502 | Left 4 Dead **base** | ~3.58 GiB | `pak01_dir.vpk` + `pak01_000..059.vpk`, `maps/`, `materials/`, `models/`, `particles/`, `scenes/`, `media/`, `missions/`, `reslists/`, `gfx/` — the assets, not the UI definitions |
| 503 | Left 4 Dead client binary | — | the client executable |
| 504 | Left 4 Dead **sound** | — | `sound/`: footsteps, weapons, infected, ambience |
| 505… | Left 4 Dead **localisations** (505 french, …) | — | the per-language `resource/*_<lang>.txt` tables |

Consequences, in the order they surprise people:

* **`sound/` is its own depot.** A "base only" download has every texture, model
  and map and no audio at all, and the engine log says nothing about it until a
  sound is played. (`modelsounds.cache` can sit in either; it is not a reliable
  signal of which depots you actually got.)
* **`resource/`, `scripts/` and `cfg/` come from 501, not 502.** A base-only
  download therefore has every texture, model and map in the game and still dies at
  `Resource/GameMenu.res`, because the UI definitions, the surface scripts and the
  engine's cfg never arrived. This is the single most misleading split in the app:
  the largest depot is the one that looks complete.
* **Language tables are separate depots.** The default-language `resource/*_english.txt`
  ships with 501, but a build whose `gameinfo.txt` asks for another language needs
  the matching depot or the UI has no strings.
* **`pak01.vpk` vs `pak01_*.vpk`**: the base depot ships 60 parts plus the tree.
  A hole in that numbering (e.g. no `pak01_048.vpk`) is a broken download, and
  `l4d1ctl vpk info` reports it by comparing the numbering rather than only the
  chunks the tree references.

## The sibling folder: `left4dead_dlc3` is The Sacrifice

L4D1's install is two content folders side by side inside `Left 4 Dead/`:

```
left4dead/          the base game — everything on this page so far
left4dead_dlc3/     The Sacrifice (October 2010), with its own pak01.vpk
```

and the **stock** `gameinfo.txt` names both, in this order:

```
SearchPaths
{
    Game    left4dead_dlc3
    Game    |gameinfo_path|.
    Game    hl2
}
```

That order is why a boot log lists `left4dead_dlc3/` *before* `left4dead/`: the DLC
overrides the base game, which is how a DLC ships a changed map without editing the
base depot. Nothing else mounts it — no code path adds it, no content scan finds it.
If the folder is not there, the entry is a dead search path and the engine silently
searches a hole, which is invisible in the log because the path is still printed.

So when copying content, `left4dead_dlc3/` goes **beside** `left4dead/`, not inside
it, and its absence is not fatal: the base menu loads, the campaign list just has no
Sacrifice.

Not to be confused with: **The Passing is L4D2's content** (`left4dead2_dlc1`). It
was never part of L4D1 — fan ports of it exist, which is what makes the mix-up easy.
A `left4dead_dlc3/` that does not contain The Passing is behaving correctly.

## The check, before blaming the engine

```sh
python3 -m l4d1ctl content --trees --root /storage/emulated/0/srceng
python3 -m l4d1ctl vpk info /storage/emulated/0/srceng/left4dead
```

The first says which trees exist loose or packed (`resource/`, `scripts/`,
`shaders/`, `sound/`, `cfg/`, …). The second says whether the pack is whole.

A tree the engine asks for by name, that is absent from both the loose directories
and every pack, cannot be produced by any engine change — the file has to arrive
from the depot. Which is why the answer to "the boot log is full of missing files"
is almost always this page, not a patch.

## Downloading it on a machine without SteamCMD

SteamCMD has no ARM64 build, so ARM64 hosts use a DepotDownloader-family tool.
Two flags matter more than the rest, and both default to something unhelpful:

* **do not filter depots.** Passing `-depot` selects exactly one; the app needs
  all of 501–504 plus a language depot.
* **do not let the host OS/arch narrow the set.** On an ARM64 host the default
  `-osarch` is `arm64`, and L4D1 has no such depots. Pass the target explicitly
  (`-os windows -osarch 32`) or whatever the tool's "all platforms / all archs"
  switch is called.

And re-run with checksum verification (DepotDownloader: `-validate`; other tools
name it `--validate` / `--verify`). Without it, files that exist on disk are
assumed correct and skipped, so a truncated first attempt stays truncated forever.

Sources: SteamDB depot pages for app 500 (`steamdb.info/app/500/depots/`), app 510
(the dedicated server, which shares 501/502) and app 513 (the authoring tools,
which shares 502–504).
