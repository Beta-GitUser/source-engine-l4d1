# l4d1ctl

Launcher tooling for the Left 4 Dead 1 port: content fetching, mod management,
offline identity and launch planning. Standard-library Python only (3.8+), so it
runs on a desktop, in Termux on the phone, and in CI with no install step.

```sh
python3 -m l4d1ctl doctor
python3 -m l4d1ctl import --from-steam
python3 -m l4d1ctl init
python3 -m l4d1ctl mod add ./some_mod
python3 -m l4d1ctl identity --avatar
python3 -m l4d1ctl launch --print
```

Full command reference: [`../docs/L4D1/LAUNCHER.md`](../docs/L4D1/LAUNCHER.md).
Motivation and limits (what this can and cannot do about Steam, and why):
[`../docs/L4D1/FEASIBILITY.md`](../docs/L4D1/FEASIBILITY.md).

## Modules

| Module | Responsibility |
| --- | --- |
| `kv.py` | Valve KeyValues text parser/writer (VDF, `appmanifest_*.acf`, cfg files) |
| `steamid.py` | SteamID64 ↔ `STEAM_X:Y:Z` ↔ `[U:1:n]` conversions |
| `steamlib.py` | Steam roots, libraries, installed app, local accounts, avatar download |
| `depot.py` | content import, `steamcmd`/`DepotDownloader` drivers, archives, verification |
| `mods.py` | mod manifests, enable/disable, ordering, staging into `mods/enabled` |
| `vpk.py` | read-only VPK (v1/v2) parser: listing, extraction, CRC and part checks |
| `gameinfo.py` | generated `gameinfo.txt` with the mods search path |
| `launch.py` | engine argv, env, generated identity cfg |
| `doctor.py` | read-only environment checks |
| `cli.py` | argparse front end |

## Tests

```sh
# from the repository root
python3 -m unittest discover -s l4d1ctl/tests -t . -v
# or both suites (python + the C++ launcher core)
scripts/test-tooling.sh
```

## Design rules

* Nothing shells out to anything with credentials on its own: the `fetch`
  command prints, confirms, then hands the terminal to `steamcmd`/`DepotDownloader`.
* No network access except the Steam Web API call in `identity --avatar`, which
  uses the user's own key.
* Game content is never modified in place; the tool writes only `gameinfo.txt`,
  `mods/`, `cfg/l4d1_identity.cfg` and `l4d1_port/`.
* Everything is idempotent: running a command twice must not stage twice, delete
  user files, or overwrite a hand-written `gameinfo.txt`.
