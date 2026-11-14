# What a complete `left4dead/` looks like, and which depot it comes from

Status: **the depot split is the thing that bites people.** L4D1 is not "one app
folder"; it is four depots plus the language ones, and they are easy to download
one at a time without noticing. Three separate downloads in this project came out
short for this reason, so the mapping is written down here.

## The depots of app 500

| Depot | Name | Size | What lands in `left4dead/` |
| --- | --- | --- | --- |
| 501 | Left 4 Dead binaries | ~289 MiB | `bin/`, the Windows/SDL executables and DLLs (not used by this port, which ships its own) |
| 502 | Left 4 Dead **base** | ~3.58 GiB | `pak01_dir.vpk` + `pak01_000..059.vpk`, `maps/`, `materials/`, `models/`, `particles/`, `scenes/`, `media/`, `missions/`, `reslists/`, `gfx/`, and — on a normal install — the loose `resource/`, `scripts/`, `cfg/` trees |
| 503 | Left 4 Dead client binary | — | the client executable |
| 504 | Left 4 Dead **sound** | — | `sound/`: footsteps, weapons, infected, ambience |
| 505… | Left 4 Dead **localisations** (505 french, …) | — | the per-language `resource/*_<lang>.txt` tables |

Consequences, in the order they surprise people:

* **`sound/` is its own depot.** A "base only" download has every texture, model
  and map and no audio at all, and the engine log says nothing about it until a
  sound is played. (`modelsounds.cache` can sit in either; it is not a reliable
  signal of which depots you actually got.)
* **`resource/`, `scripts/` and `cfg/` are not a depot of their own.** They ship
  with the base depot, so their absence alongside a present `pak01_*.vpk` is an
  incomplete download, not a depot-split — the two look identical in an engine log
  and need opposite fixes.
* **Language tables are separate depots.** `resource/valve_english.txt` comes with
  the base depot for the default language, but a build whose `gameinfo.txt` asks
  for a non-default language needs the matching depot or the UI has no strings.
* **`pak01.vpk` vs `pak01_*.vpk`**: the base depot ships 60 parts plus the tree.
  A hole in that numbering (e.g. no `pak01_048.vpk`) is a broken download, and
  `l4d1ctl vpk info` reports it by comparing the numbering rather than only the
  chunks the tree references.

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
