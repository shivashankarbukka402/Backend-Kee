"""
Master Data Generator - Command Line Interface

The CLI entry point for the KeeMeds master data foundation (Phase 1).

Responsibilities
----------------
- Parse command line arguments
- Build the centralized configuration and dependencies (dependency injection)
- Delegate all business logic to :class:`~master_data.master_loader.MasterDataLoader`

This module contains no business logic itself; it only wires configuration,
readers and the loader together and prints results.

Usage
-----
python generate_master_data.py
python generate_master_data.py --validate
python generate_master_data.py --summary
python generate_master_data.py --verify
python generate_master_data.py --images
python generate_master_data.py --generate-images
python generate_master_data.py --import
python generate_master_data.py --all --import
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

if __package__ is None or __package__ == "":
    # Support running this file directly (python generate_master_data.py) by
    # making the enclosing ``master_data`` package importable. Using append()
    # instead of insert(0, ...) avoids shadowing the ``keemeds_commerce`` app
    # package, which lives in the same parent directory and is required by the
    # Frappe runtime during import execution.
    PACKAGE_PARENT = Path(__file__).resolve().parent.parent
    if str(PACKAGE_PARENT) not in sys.path:
        sys.path.append(str(PACKAGE_PARENT))
    from master_data.ai_image_generator import (
        build_ai_image_generator,
    )
    from master_data.bench_runtime import (
        BenchRuntimeError,
        bootstrap_site,
        enter_bench_for_import,
    )
    from master_data.config import (
        MasterDataConfig,
    )
    from master_data.exporters import (
        export_generation_result,
        export_records,
    )
    from master_data.generators import (
        build_enrichment_generators,
        build_generators,
    )
    from master_data.image_generator import (
        build_image_generator,
    )
    from master_data.image_manifest import (
        build_image_manifest_writer,
    )
    from master_data.image_prompt_generator import (
        build_image_prompt_generator,
    )
    from master_data.import_manager import (
        build_importers,
    )
    from master_data.import_pipeline import (
        MasterDataImportPipeline,
    )
    from master_data.logging_setup import (
        setup_logging,
    )
    from master_data.master_generator import (
        MasterGenerator,
    )
    from master_data.master_loader import (
        MasterDataLoader,
    )
    from master_data.pipeline import (
        MasterDataPipeline,
    )
    from master_data.readers import (
        build_default_registry,
    )
    from master_data.validators import (
        ValidationReport,
    )
    from master_data.verification import (
        build_verification_report_writer,
        build_verifier,
    )
else:
    from .ai_image_generator import build_ai_image_generator
    from .bench_runtime import BenchRuntimeError, bootstrap_site, enter_bench_for_import
    from .config import MasterDataConfig
    from .exporters import export_generation_result, export_records
    from .generators import build_enrichment_generators, build_generators
    from .image_generator import build_image_generator
    from .image_manifest import build_image_manifest_writer
    from .image_prompt_generator import build_image_prompt_generator
    from .import_manager import build_importers
    from .import_pipeline import MasterDataImportPipeline
    from .logging_setup import setup_logging
    from .master_generator import MasterGenerator
    from .master_loader import MasterDataLoader
    from .pipeline import MasterDataPipeline
    from .readers import build_default_registry
    from .validators import ValidationReport
    from .verification import build_verification_report_writer, build_verifier


def build_parser() -> argparse.ArgumentParser:
    """
    Build the argument parser for the CLI.
    """

    parser = argparse.ArgumentParser(
        prog="generate_master_data.py",
        description="KeeMeds Master Data Generator (Foundation + Medicine + Enrichment)",
    )
    parser.add_argument(
        "--medicines",
        action="store_true",
        help="Generate medicine items from the loaded master data.",
    )
    parser.add_argument(
        "--masters",
        action="store_true",
        help="Generate and export the ERPNext master workbooks (UOM, Item "
        "Group, Brand, Manufacturer, Item Attribute, Item Attribute Value) "
        "from the loaded master data.",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export generated medicine items to ERPNext Item Import Excel files.",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Generate catalog-enrichment records (prices, opening stock, "
        "image mappings) for the medicines and export them.",
    )
    parser.add_argument(
        "--images",
        action="store_true",
        help="Generate the placeholder medicine image and all required item "
        "image files for the current catalog (copies or symbolic links).",
    )
    parser.add_argument(
        "--generate-images",
        action="store_true",
        help="Generate the Phase 9.5 AI product-image artifacts: per-medicine "
        "image prompts under output/image_prompts, an optimized three-image "
        "gallery per medicine under output/item_images (front, 45-degree, "
        "side/back), mirrored optimized copies served by ERPNext under "
        "output/site files, and the reusable image_manifest.json.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run the full pipeline (validate, load, generate all catalog "
        "data, export all ERPNext import files and generate execution "
        "reports) in a single command.",
    )
    parser.add_argument(
        "--import",
        dest="import_after",
        action="store_true",
        help="Import the generated catalog files into ERPNext through the "
        "standard Frappe Data Import API in dependency order "
        "(Item Master, Item Prices, Opening Stock, Image Mapping). "
        "Combining with --all generates and then imports.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the master source files and report findings only.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify the generated catalog (files, columns, uniqueness, "
        "master references, item references, images) and produce a "
        "verification report under output/reports.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a summary of the configured master data sources.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=MasterDataConfig.source_dir,
        help="Directory containing the master source files "
        "(default: master_data/source).",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=MasterDataConfig.logging.level,
        help="Logging verbosity (default: INFO).",
    )
    return parser


def build_config(args: argparse.Namespace) -> MasterDataConfig:
    """
    Build a centralized configuration from CLI arguments and defaults.
    """

    return MasterDataConfig(
        source_dir=args.source_dir,
        logging=MasterDataConfig.logging.__class__(
            level=args.log_level,
            format=MasterDataConfig.logging.format,
            style=MasterDataConfig.logging.style,
            date_format=MasterDataConfig.logging.date_format,
        ),
    )


def run_summary(
    config: MasterDataConfig,
    logger: logging.Logger,
    stream: TextIO,
) -> int:
    """
    Print a summary of the configured master data sources and exit.
    """

    source_dir = config.source_dir.resolve()
    print(f"Master data source directory: {source_dir}", file=stream)
    print(f"Configured entities: {len(config.entities)}", file=stream)
    print(file=stream)
    for entity in config.entities:
        path = source_dir / entity.filename
        status = "present" if path.is_file() else "missing"
        print(
            f"- {entity.key}: {entity.name} [{entity.file_type.value}] "
            f"(file: {entity.filename}, status: {status}, "
            f"columns: {', '.join(entity.required_columns)})",
            file=stream,
        )
    logger.info("Summary generated")
    return 0


def run_validate(loader: MasterDataLoader, stream: TextIO) -> int:
    """
    Validate the master source files and print the report.
    """

    report = loader.validate()
    _print_report(report, stream)
    return 0 if report.is_valid else 1


def run_medicines(
    config: MasterDataConfig,
    loader: MasterDataLoader,
    stream: TextIO,
    *,
    export: bool = False,
) -> int:
    """
    Generate medicine items from loaded master data and report the outcome.

    Loads the master reference sets, builds the enabled generators and runs the
    Medicine generator. When ``export`` is set, the generated records are also
    exported to ERPNext Item Import Excel files and the export report printed.
    """

    master_data = loader.load()
    if not master_data.entities:
        print("No master data could be loaded.", file=stream)
        return 1

    generators = build_generators(config=config, master_data=master_data)
    medicine = generators["medicine"]
    result = medicine.generate()

    if result.count == 0:
        print("No medicines were generated.", file=stream)
        return 1

    print(f"Generated medicines: {result.count}", file=stream)
    for batch in result.batches:
        print(f"  {batch.label}: {len(batch.items)} item(s)", file=stream)

    codes = [item.item_code for item in result.items]
    names = [item.item_name for item in result.items]
    print(file=stream)
    print(f"Unique Item Codes: {len(set(codes)) == result.count}", file=stream)
    print(f"Unique Item Names: {len(set(names)) == result.count}", file=stream)

    if export:
        report = export_generation_result(
            result=result,
            config=config.export,
        )
        print(file=stream)
        _print_export_report(report, stream)

    return 0


def run_masters(
    config: MasterDataConfig,
    loader: MasterDataLoader,
    stream: TextIO,
) -> int:
    """
    Generate and export the ERPNext master workbooks.

    Loads the master reference sets, builds the Phase 7 master generator and
    exports each master entity (UOM, Item Group, Brand, Manufacturer, Item
    Attribute, Item Attribute Value) to its ERPNext import workbook. Returns
    ``0`` on success.
    """

    master_data = loader.load()
    if not master_data.entities:
        print("No master data could be loaded.", file=stream)
        return 1

    generator = MasterGenerator(config=config, master_data=master_data)
    records_by_key = generator.generate()

    print("Master exports:", file=stream)
    for spec in config.master_exports():
        records = records_by_key.get(spec.key, [])
        report = export_records(
            records=records,
            spec=spec,
            config=config.export,
        )
        print(f"  {spec.key}: {report.exported} record(s) exported", file=stream)
        _print_export_report(report, stream)
        print(file=stream)

    return 0


def run_enrich(
    config: MasterDataConfig,
    loader: MasterDataLoader,
    stream: TextIO,
) -> int:
    """
    Generate the medicines then enrich them with prices, opening stock and
    image mappings, exporting each enrichment set.

    The enrichment generators consume the generated medicine items so every
    price/stock/image record references an existing item code. Returns ``0`` on
    success and ``1`` if no medicines could be generated.
    """

    master_data = loader.load()
    if not master_data.entities:
        print("No master data could be loaded.", file=stream)
        return 1

    items = build_generators(config=config, master_data=master_data)["medicine"].generate()
    if items.count == 0:
        print("No medicines were generated.", file=stream)
        return 1

    print(f"Enriching {items.count} medicine item(s)", file=stream)
    generators = build_enrichment_generators(
        config=config, items=items.items
    )

    enrichment = {
        generators["price"].name: (
            generators["price"].generate(),
            config.price_export,
        ),
        generators["stock"].name: (
            generators["stock"].generate(),
            config.stock_export,
        ),
        generators["image_mapping"].name: (
            generators["image_mapping"].generate(),
            config.image_mapping_export,
        ),
    }

    for name, (records, spec) in enrichment.items():
        report = export_records(records=records, spec=spec, config=config.export)
        print(f"  {name}: {report.exported} record(s) exported", file=stream)
        _print_export_report(report, stream)
        print(file=stream)

    return 0


def run_images(
    config: MasterDataConfig,
    logger: logging.Logger,
    stream: TextIO,
) -> int:
    """
    Generate the placeholder medicine image and all required item images.

    A single professional placeholder is created (SVG, plus a webp raster when
    Pillow is available) and each item image expected by the catalog is produced
    as a symbolic link - or a file copy when symlinks are unsupported - of that
    placeholder. Files that already exist (real product images) are skipped.
    Returns ``0`` on success.
    """

    generator = build_image_generator(
        config=config.export,
        image_config=config.image_generator,
        logger=logger,
    )
    outcome = generator.run()

    print("Image generation complete", file=stream)
    print(f"  Placeholder:   {outcome.placeholder}", file=stream)
    print(f"  Generated:     {outcome.generated}", file=stream)
    print(f"  Linked:        {outcome.linked}", file=stream)
    print(f"  Copied:        {outcome.copied}", file=stream)
    print(f"  Skipped:       {outcome.skipped}", file=stream)
    print(f"  Total images:  {outcome.total}", file=stream)

    return 0


def run_generate_images(
    config: MasterDataConfig,
    loader: MasterDataLoader,
    logger: logging.Logger,
    stream: TextIO,
) -> int:
    """
    Generate the Phase 9.5 AI product-image artifacts for every medicine.

    Emits one deterministic AI image prompt per medicine (``output/image_prompts``),
    renders an optimized three-image gallery per medicine (front packshot,
    45-degree perspective and side/back view) into the canonical
    ``output/item_images`` directory, mirrors optimized copies into the ERPNext
    public files directory so the existing ``/files/item_images`` mapping serves
    them at runtime, and writes the reusable ``output/image_manifest.json``.
    Returns ``0`` on success and ``1`` when no medicines could be produced.
    """

    master_data = loader.load()
    if not master_data.entities:
        print("No master data could be loaded.", file=stream)
        return 1

    result = build_generators(config=config, master_data=master_data)[
        "medicine"
    ].generate()
    if result.count == 0:
        print("No medicines were generated.", file=stream)
        return 1

    ai_config = config.ai_images
    print(
        f"Generating AI product images for {result.count} medicine item(s)",
        file=stream,
    )
    print(
        f"  Images per item:  {ai_config.images_per_item}",
        file=stream,
    )
    print(
        f"  Target size:      {ai_config.width}x{ai_config.height} WebP "
        f"(quality={ai_config.webp_quality})",
        file=stream,
    )
    print(file=stream)

    try:
        import PIL
    except ImportError:
        print(
            "Pillow is required to render product images but is not available "
            "in this interpreter. Install it or run via the Bench virtualenv "
            "(e.g. `<bench>/env/bin/python generate_master_data.py "
            "--generate-images`).",
            file=stream,
        )
        return 1

    prompt_generator = build_image_prompt_generator(ai_config, logger=logger)
    image_generator = build_ai_image_generator(ai_config, logger=logger)
    manifest_writer = build_image_manifest_writer(
        ai_config, config.export.output_dir, logger=logger
    )
    items = result.items

    prompt_outcome = prompt_generator.run(items)
    image_outcome = image_generator.run(items)
    manifest_outcome = manifest_writer.write(items)

    print("AI image generation complete", file=stream)
    print(f"  Prompts generated:     {prompt_outcome.generated}", file=stream)
    print(f"  Images generated:      {image_outcome.images_generated}", file=stream)
    print(f"  Images optimized:      {image_outcome.images_optimized}", file=stream)
    print(f"  Images skipped:        {image_outcome.images_skipped}", file=stream)
    print(f"  Manifest entries:      {manifest_outcome.entries}", file=stream)
    print(file=stream)
    if image_outcome.failed:
        print("  Failed generations:", file=stream)
        for failure in image_outcome.failed:
            print(f"    - {failure}", file=stream)
        print(file=stream)
    print("Artifacts:", file=stream)
    print(f"  Prompts:    {ai_config.prompts_output_dir}", file=stream)
    print(f"  Gallery:    {ai_config.item_images_output_dir}", file=stream)
    print(f"  ERPNext:    {ai_config.optimize_output_dir}", file=stream)
    print(f"  Manifest:   {manifest_outcome.path}", file=stream)

    return 0 if not image_outcome.failed else 1


def run_all(
    config: MasterDataConfig,
    loader: MasterDataLoader,
    logger: logging.Logger,
    stream: TextIO,
) -> int:
    """
    Run the full one-command master data pipeline.

    Validates, loads, generates every catalog record, exports every ERPNext
    import workbook and generates the execution reports. Returns ``0`` on
    success and ``1`` when the pipeline could not produce the catalog.
    """

    pipeline = MasterDataPipeline(config=config, loader=loader, logger=logger)
    report = pipeline.run()

    print("Pipeline complete", file=stream)
    print(f"  Generated records: {report.records_generated}", file=stream)
    print(f"  Exported records:  {report.records_exported}", file=stream)
    print(f"  Skipped records:   {report.records_skipped}", file=stream)
    print(file=stream)
    print("Phase 9.5 AI product images:", file=stream)
    print(f"  Prompts generated: {report.ai_prompts_generated}", file=stream)
    print(f"  Images generated:  {report.ai_images_generated}", file=stream)
    print(f"  Images optimized:  {report.ai_images_optimized}", file=stream)
    print(f"  Images skipped:    {report.ai_images_skipped}", file=stream)
    print(f"  Manifest entries:  {report.ai_manifest_entries}", file=stream)
    print(file=stream)
    for error in report.validation_errors:
        print(f"  [error] {error}", file=stream)
    for warning in report.validation_warnings:
        print(f"  [warning] {warning}", file=stream)
    for note in report.notes:
        print(f"  [note] {note}", file=stream)
    print(file=stream)
    print("Generated report files:", file=stream)
    for path in report.files_generated:
        print(f"  - {path}", file=stream)

    return 0 if report.records_exported > 0 else 1


def _teardown_frappe() -> None:
    """
    Release the connected Frappe site after an import completes.

    Safe to call when no connection was ever established; matching the connect
    in :func:`run_import` and preventing a leaked database connection from
    outliving the import process.
    """
    try:
        import frappe

        if getattr(frappe, "db", None) or getattr(frappe, "local", None):
            frappe.destroy()
    except Exception:
        pass


def run_import(
    config: MasterDataConfig,
    logger: logging.Logger,
    stream: TextIO,
    *,
    argv: Sequence[str] | None = None,
) -> int:
    """
    Import the generated catalog files into ERPNext in dependency order.

    Builds the import registry and pipeline, runs the pre-flight (missing
    required files) detection and imports each generated workbook through the
    standard Frappe Data Import API. Returns ``0`` on a clean import and ``1``
    when a required source file is missing or any record failed.

    The import runs inside the active ERPNext Bench runtime: when the current
    interpreter cannot import ``frappe`` the command is transparently
    re-executed under the Bench virtualenv Python, and the configured site is
    initialised and connected so the standard Data Import executor has a live
    ``frappe.db``. Missing or invalid Bench/site configuration is reported
    clearly and returns ``1``.
    """

    try:
        import_argv = list(sys.argv if argv is None else argv)
        if argv is None:
            import_argv = import_argv[1:]
        enter_bench_for_import(import_argv)
        site = bootstrap_site(site_override=config.bench.site)
    except BenchRuntimeError as exc:
        print(f"[fatal] {exc}", file=stream)
        logger.error("Bench runtime error: %s", exc)
        return 1

    print(f"Importing into ERPNext site: {site}", file=stream)

    try:
        manager = build_importers(config=config, logger=logger)
        report, _ = MasterDataImportPipeline(config=config, logger=logger).run(
            manager=manager
        )
    finally:
        _teardown_frappe()

    print("Import complete", file=stream)
    print(f"  Imported records: {report.imported}", file=stream)
    print(f"  Updated records:  {report.updated}", file=stream)
    print(f"  Skipped records:  {report.skipped}", file=stream)
    print(f"  Failed records:   {report.failed}", file=stream)
    print(file=stream)
    for error in report.fatal_errors:
        print(f"  [fatal] {error}", file=stream)
    for missing in report.missing_files:
        print(f"  [missing] {missing}", file=stream)
    for note in report.notes:
        print(f"  [note] {note}", file=stream)
    print(file=stream)
    print("Imported files:", file=stream)
    for file_report in report.files:
        print(
            f"  - {file_report.path} [{file_report.import_type}] "
            f"imported={file_report.imported} updated={file_report.updated} "
            f"skipped={file_report.skipped} failed={file_report.failed}",
            file=stream,
        )

    return 0 if report.ok else 1


def run_verify(
    config: MasterDataConfig,
    logger: logging.Logger,
    stream: TextIO,
) -> int:
    """
    Verify the generated catalog and write a verification report.

    Checks that every expected output file exists, that each workbook contains
    the required ERPNext columns, that generated item codes/names are unique,
    that item master references (Brands, Manufacturers, Item Groups, UOMs) and
    price/stock/image item references resolve, and that image mappings point at
    present image files. The report is written under ``output/reports``.
    Returns ``0`` when the catalog is consistent, ``1`` otherwise.
    """

    verifier = build_verifier(config=config, logger=logger)
    report = verifier.verify()
    writer = build_verification_report_writer(config=config, logger=logger)
    json_path, log_path = writer.write(report)

    print("Verification complete", file=stream)
    print(f"  Status:            {report.status}", file=stream)
    print(f"  Expected records:  {report.expected_records}", file=stream)
    print(f"  Generated records: {report.generated_records}", file=stream)
    print(f"  Expected files:    {report.expected_files}", file=stream)
    print(f"  Found files:       {report.found_files}", file=stream)
    print(f"  AI manifest items: {report.ai_manifest_items}", file=stream)
    print(file=stream)
    for file_path in report.missing_files:
        print(f"  [missing file] {file_path}", file=stream)
    for entry in report.missing_masters:
        print(f"  [missing master] {entry}", file=stream)
    for entry in report.invalid_references:
        print(f"  [invalid reference] {entry}", file=stream)
    for entry in report.duplicate_records:
        print(f"  [duplicate] {entry}", file=stream)
    for entry in report.missing_images:
        print(f"  [missing image] {entry}", file=stream)
    for entry in report.missing_prices:
        print(f"  [missing price] {entry}", file=stream)
    for entry in report.duplicate_prices:
        print(f"  [duplicate price] {entry}", file=stream)
    for entry in report.missing_stock:
        print(f"  [missing stock] {entry}", file=stream)
    for entry in report.duplicate_stock:
        print(f"  [duplicate stock] {entry}", file=stream)
    for entry in report.invalid_stock_quantities:
        print(f"  [invalid stock quantity] {entry}", file=stream)
    for entry in report.missing_warehouses:
        print(f"  [missing warehouse] {entry}", file=stream)
    for entry in report.missing_prompts:
        print(f"  [missing AI prompt] {entry}", file=stream)
    for entry in report.missing_ai_images:
        print(f"  [missing AI image] {entry}", file=stream)
    for entry in report.duplicate_ai_filenames:
        print(f"  [duplicate AI filename] {entry}", file=stream)
    for entry in report.invalid_ai_image_dimensions:
        print(f"  [invalid AI image dimension] {entry}", file=stream)
    for note in report.notes:
        print(f"  [note] {note}", file=stream)
    print(file=stream)
    print("Verification report files:", file=stream)
    print(f"  - {json_path}", file=stream)
    print(f"  - {log_path}", file=stream)

    return 0 if report.is_valid else 1


def _print_export_report(report, stream: TextIO) -> None:
    """
    Print a human-readable export summary.
    """

    print("Export summary:", file=stream)
    print(f"  Total generated records: {report.total_generated}", file=stream)
    print(f"  Exported records: {report.exported}", file=stream)
    print(f"  Skipped records: {report.skipped}", file=stream)
    print("  Generated files:", file=stream)
    for path in report.files_generated:
        print(f"    - {path}", file=stream)
    for error in report.errors:
        print(f"  Skipped: {error.item_code} ({error.reason})", file=stream)


def run_load(loader: MasterDataLoader, stream: TextIO) -> int:
    """
    Validate and load the master data, printing the normalized result.
    """

    result = loader.load()
    _print_report(loader.validate(), stream)
    print(file=stream)

    if not result.entities:
        print("No master data could be loaded.", file=stream)
        return 1

    for key in sorted(result.entities):
        entity = result.entities[key]
        print(f"{entity.name} ({entity.key}): {len(entity.values)} value(s)", file=stream)
        for warning in entity.warnings:
            print(f"  - {warning}", file=stream)

    if result.errored:
        print(file=stream)
        print(
            f"Errored entities: {', '.join(sorted(result.errored))}",
            file=stream,
        )
    return 0


def _print_report(report: ValidationReport, stream: TextIO) -> None:
    """
    Print a human-readable representation of a validation report.
    """

    if report.is_valid:
        print("Validation: PASSED", file=stream)
    else:
        print("Validation: FAILED", file=stream)

    for issue in report.issues:
        print(f"  [{issue.level.value}] {issue.entity_key}: {issue.message}", file=stream)

    if not report.issues:
        print("  (no issues found)", file=stream)


def main(argv: Sequence[str] | None = None, stream: TextIO = sys.stdout) -> int:
    """
    Execute the CLI.
    """

    raw_argv = list(sys.argv if argv is None else argv)
    if argv is None:
        # ``sys.argv`` includes the script path as its first element; strip it
        # so the re-executed command carries only the actual CLI arguments.
        raw_argv = raw_argv[1:]

    # When an import is requested, ensure the whole command runs inside the
    # Bench virtualenv so `import frappe` succeeds. This re-executes the exact
    # same invocation under `<bench>/env/bin/python` from the Bench `sites`
    # directory when the current interpreter has no Frappe on its path.
    if "--import" in raw_argv:
        try:
            enter_bench_for_import(raw_argv)
        except BenchRuntimeError as exc:
            print(f"[fatal] {exc}", file=stream)
            logging.getLogger("keemeds.master_data").error(
                "Bench runtime error: %s", exc
            )
            return 1

    parser = build_parser()
    args = parser.parse_args(argv)

    config = build_config(args)
    logger = setup_logging(config.logging, stream=stream)
    readers = build_default_registry()
    loader = MasterDataLoader(config=config, readers=readers, logger=logger)

    if args.summary:
        return run_summary(config, logger, stream)

    if args.validate:
        return run_validate(loader, stream)

    if args.medicines:
        return run_medicines(config, loader, stream, export=args.export)

    if args.masters:
        return run_masters(config, loader, stream)

    if args.enrich:
        return run_enrich(config, loader, stream)

    if args.images:
        return run_images(config, logger, stream)

    if args.generate_images:
        return run_generate_images(config, loader, logger, stream)

    if args.verify:
        return run_verify(config, logger, stream)

    if args.all:
        status = run_all(config, loader, logger, stream)
        if args.import_after:
            import_status = run_import(config, logger, stream, argv=argv)
            return status if status != 0 else import_status
        return status

    if args.import_after:
        return run_import(config, logger, stream, argv=argv)

    return run_load(loader, stream)


if __name__ == "__main__":
    sys.exit(main())
