"""Packs the dashboard's text files when the package is built.

aiohttp sends `app.js.gz` for `app.js` by itself, so the Pi spends no CPU on this. Building them
here keeps each file with its source: installing, updating and rolling back replace both.
"""

import gzip
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

PACKED = {".css", ".html", ".js", ".svg", ".webmanifest"}


class BuildWithPackedStatic(build_py):
    def run(self) -> None:
        super().run()
        for path in (Path(self.build_lib) / "pi_guard" / "web" / "static").rglob("*"):
            if path.suffix in PACKED:
                packed = gzip.compress(path.read_bytes(), 9, mtime=0)
                path.with_name(path.name + ".gz").write_bytes(packed)


setup(cmdclass={"build_py": BuildWithPackedStatic})
