"""Left 4 Dead (appid 500) launcher tooling for the source-engine port.

The package is deliberately standard-library-only so that it runs on a phone,
a desktop and CI without a network install step.

Public entry point: ``python3 -m l4d1ctl <command>``.
"""

__version__ = "0.1.0"

__all__ = [
    "depot",
    "doctor",
    "gameinfo",
    "gamemod",
    "kv",
    "launch",
    "mods",
    "paths",
    "preflight",
    "selftest",
    "steamid",
    "steamlib",
    "vpk",
]
