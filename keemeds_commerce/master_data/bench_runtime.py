"""
Bench Runtime Integration for Master Data Import

The Phase 8 glue that lets the Phase 6 import pipeline execute inside the
active ERPNext Bench runtime instead of a standalone Python interpreter.

Why this module exists
----------------------
The existing import framework (:class:`~master_data.importers.base_importer.FrappeImportExecutor`)
delegates the actual insert/update to the standard Frappe Data Import API, which
requires ``frappe`` to be importable *and* an active site database to be
connected. A standalone ``python3`` (the interpreter used to run the CLI) does
not have ``frappe`` on its path, so the executor correctly reports every import
as "Frappe unavailable" and nothing is written.

This module is the minimal seam that resolves that without redesigning the
pipeline:

1. :func:`enter_bench_for_import` detects whether ``frappe`` is importable in
   the current interpreter. When it is not, it re-executes the exact same CLI
   invocation under the Bench's own virtualenv Python (``<bench>/env/bin/python``)
   running from the Bench ``sites`` directory, so ``import frappe`` succeeds and
   the standard logging layout resolves to the Bench ``logs`` directory.

2. :func:`bootstrap_site` finishes the job in the (re-)launched process by
   calling ``frappe.init`` + ``frappe.connect`` for the active site, so the
   existing :class:`~master_data.importers.base_importer.FrappeImportExecutor`
   sees a live ``frappe.db`` and runs the real Data Import workflow.

The architecture stays dependency-injected: this module only locates and boots
the Bench environment. It does not change the generator, exporter, verification,
reporting or import pipeline logic, and it never bypasses ERPNext validation
(no direct SQL, no ORM inserts).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import PACKAGE_DIR

logger = logging.getLogger("keemeds.master_data.bench_runtime")


@dataclass(frozen=True)
class BenchRuntimeError(RuntimeError):
    """
    Raised when the Bench environment cannot be detected or initialised.

    Carries a clear, actionable message so the CLI can report the failure
    gracefully instead of raising an opaque stack trace.
    """

    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class Bench:
    """
    Description of a detected Bench workspace.

    Attributes
    ----------
    root:
        The Bench root (the directory containing ``sites`` and ``env``).
    sites_path:
        The ``sites`` directory that holds the site configurations.
    venv_python:
        The Bench virtualenv's Python executable.
    sites:
        The list of installed sites (directory names under ``sites``).
    """

    root: Path
    sites_path: Path
    venv_python: Path
    sites: tuple[str, ...] = ()

    def default_site(self) -> str | None:
        """
        Return the active default site name, or ``None`` if it cannot be known.

        The default is read from ``sites/common_site_config.json`` (the
        ``default_site`` key). When that is absent, a single installed site is
        used as a sensible fallback. Ambiguous multiple-site layouts without a
        configured default return ``None`` so the caller can error clearly.
        """
        default = _read_common_site_config(self.sites_path).get("default_site")
        if default:
            return str(default)
        if len(self.sites) == 1:
            return self.sites[0]
        return None


#: Environment variable guarding against infinite re-execution.
_REEXEC_GUARD = "KEEMEDS_BENCH_REEXEC"

#: The interpreter that performs the import must be launched from this cwd so
#: ``frappe`` logging resolves to ``<bench>/logs`` instead of a parent path.
_SITES_DIR_NAME = "sites"


def detect_bench() -> Bench:
    """
    Locate the active Bench workspace by walking up from the package directory.

    The package lives at ``<bench>/apps/<app>/<app>/master_data``, so the Bench
    root is a fixed number of parents up. The candidate is validated rather than
    trusted: it must contain a ``sites`` directory and a virtualenv Python.

    Raises
    ------
    BenchRuntimeError:
        When no valid Bench workspace can be found.
    """
    root = PACKAGE_DIR.parents[3]
    sites_path = root / _SITES_DIR_NAME
    venv_python = root / "env" / "bin" / "python"
    if not sites_path.is_dir() or not venv_python.is_file():
        raise BenchRuntimeError(
            f"Could not locate an active Bench workspace under {root}. "
            "Expected '{sites_path}' and '{venv_python}' to exist. "
            "Set KEEMEDS_BENCH_ROOT to point at the Bench root."
        )
    sites = tuple(
        sorted(p.name for p in sites_path.iterdir() if p.is_dir() and (p / "site_config.json").is_file())
    )
    return Bench(root=root, sites_path=sites_path, venv_python=venv_python, sites=sites)


def is_frappe_importable() -> bool:
    """
    Return whether ``frappe`` is importable in this interpreter.

    A standalone interpreter (default CLI Python) has no ``frappe`` on its path
    and reports ``False``, which triggers the Bench re-execution.
    """
    return importlib.util.find_spec("frappe") is not None


def enter_bench_for_import(argv: list[str]) -> bool:
    """
    Ensure the current Interpreter can ``import frappe``, re-executing into the
    Bench virtualenv when it cannot.

    This is a no-op (returns ``True``) when ``frappe`` is already importable.
    Otherwise it spawns the Bench virtualenv Python to run this same script with
    the same arguments from the Bench ``sites`` directory, forwards the child's
    exit code and exits the current process. A guard environment variable
    prevents recursive re-execution if the venv Python somehow still cannot
    import ``frappe``.

    Returns
    -------
    bool:
        ``True`` in this process when no re-execution was needed. When a
        re-execution happened, this function never returns.

    Raises
    ------
    BenchRuntimeError:
        When the Bench cannot be detected or re-execution fails.
    """
    if is_frappe_importable():
        return True

    if os.environ.get(_REEXEC_GUARD):
        raise BenchRuntimeError(
            "Frappe is still unavailable after re-executing into the Bench "
            "virtualenv. Confirm the Bench environment is healthy."
        )

    bench = detect_bench()
    script = Path(__file__).resolve()
    command = [str(bench.venv_python), str(script.parent / "generate_master_data.py"), *argv]

    logger.info(
        "Re-executing into Bench runtime: %s (site-aware cwd: %s)",
        command[0],
        bench.sites_path,
    )
    env = dict(os.environ)
    env[_REEXEC_GUARD] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=str(bench.sites_path),
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BenchRuntimeError(
            f"Could not execute Bench python at {bench.venv_python}: {exc}"
        ) from exc
    os._exit(completed.returncode or 0)


def locate_site(site_override: str = "") -> str:
    """
    Return the active Bench site name, raising a clear error when missing.

    Precedence: an explicit ``site_override``, then the ``KEEMEDS_SITE``
    environment variable, then the Bench ``default_site`` (from
    ``common_site_config.json``) falling back to a single installed site.

    Raises
    ------
    BenchRuntimeError:
        When no site can be determined.
    """
    if site_override:
        return site_override
    configured = os.environ.get("KEEMEDS_SITE")
    if configured:
        return configured

    bench = detect_bench()
    site = bench.default_site()
    if not site:
        raise BenchRuntimeError(
            "Could not determine the active ERPNext site. A default_site is "
            "not configured in sites/common_site_config.json and multiple "
            "sites are installed. Set KEEMEDS_SITE to the target site."
        )
    return site


def bootstrap_site(site_override: str = "") -> str:
    """
    Initialise and connect the active ERPNext site so ``frappe.db`` is live.

    This must run inside the Bench virtualenv (after :func:`enter_bench_for_import`).
    After a successful connect, the existing
    :class:`~master_data.importers.base_importer.FrappeImportExecutor` finds an
    active ``frappe.db`` and executes the standard Data Import workflow.

    Parameters
    ----------
    site_override:
        Optional explicit site name from configuration; overrides discovery.

    Returns
    -------
    str:
        The name of the connected site.

    Raises
    ------
    BenchRuntimeError:
        When ``frappe`` or the configured site cannot be initialised.
    """
    try:
        import frappe
    except ImportError as exc:
        raise BenchRuntimeError(
            f"Frappe is not importable, so the site cannot be connected: {exc}"
        ) from exc

    site = locate_site(site_override)

    current = getattr(getattr(frappe, "local", None), "site", None)
    if getattr(frappe, "db", None) and current == site:
        return site

    try:
        frappe.init(site=site, force=True)
        frappe.connect()
    except Exception as exc:
        try:
            frappe.destroy()
        except Exception:
            pass
        raise BenchRuntimeError(
            f"Could not initialise the ERPNext site '{site}': {exc}"
        ) from exc

    logger.info("Connected to ERPNext site: %s", site)
    return site


def _read_common_site_config(sites_path: Path) -> dict:
    """Load and return the Bench ``common_site_config.json`` dictionary."""
    path = sites_path / "common_site_config.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return {}


def site_directory(site: str) -> Path:
    """
    Return the site directory path for ``site`` under the Bench ``sites`` dir.

    Raises
    ------
    BenchRuntimeError:
        When the site directory is missing.
    """
    bench = detect_bench()
    site_path = bench.sites_path / site
    if not (site_path / "site_config.json").is_file():
        raise BenchRuntimeError(
            f"The site '{site}' is not installed under {bench.sites_path}."
        )
    return site_path
