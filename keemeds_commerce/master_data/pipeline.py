"""
Master Data Pipeline

The Phase 5 one-command orchestrator. A single
:class:`~master_data.pipeline.MasterDataPipeline` drives the whole flow:

1. Load the master reference data.
2. Validate the master source files.
3. Generate the medicine items.
4. Generate the item prices, opening stock and image mappings.
5. Export every ERPNext import workbook (items, prices, stock, images).
6. Generate the execution reports (JSON, spreadsheet and log).

Dependencies are injected through the constructor so the pipeline stays a thin,
testable orchestrator that keeps business logic in the generators and exporters.

Recoverable vs fatal failures
-----------------------------
The pipeline continues across recoverable errors (a generator that yields no
records, an exporter that rejects some records) and records them as execution
notes. It stops - returning a non-zero outcome - only on a fatal validation
failure, i.e. when no master data or no medicines could be generated.

Extensibility
-------------
The pipeline consumes the generator *registries* returned by the injected
``item_generators_builder`` and ``enrichment_generators_builder`` factories and
iterates over them generically. A future OTC, Personal Care, Wellness, Baby
Care, Medical Devices, Surgical or Accessories generator requires only:

- registration in the corresponding builder, and
- (for enrichment) an ``EnrichmentExportConfig`` with a matching ``key`` in the
  centralized configuration.

No orchestration change is needed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .ai_image_generator import AIImageGenerator, build_ai_image_generator
from .config import MasterDataConfig
from .exporters import export_generation_result, export_records
from .exporters.excel_exporter import ExportReport
from .generators import (
    BaseEnrichmentGenerator,
    BaseGenerator,
    build_enrichment_generators,
    build_generators,
)
from .image_manifest import ImageManifestWriter, build_image_manifest_writer
from .image_prompt_generator import (
    ImagePromptGenerator,
    build_image_prompt_generator,
)
from .item_models import GenerationResult
from .master_generator import MasterGenerator
from .master_loader import MasterDataLoader
from .models import MasterDataResult
from .reporting import GenerationReport, ReportWriter, build_report_writer

#: Factory type for the item-generator registry.
ItemGeneratorsBuilder = Callable[..., dict[str, BaseGenerator]]
#: Factory type for the enrichment-generator registry.
EnrichmentGeneratorsBuilder = Callable[..., dict[str, BaseEnrichmentGenerator]]
#: Factory type for the Phase 9.5 AI image-pipeline bundle.
AIImageBundle = tuple[ImagePromptGenerator, AIImageGenerator, ImageManifestWriter]
AIImageBuilder = Callable[..., AIImageBundle]


class MasterDataPipeline:
    """
    Orchestrates validation, generation, export and reporting for the master
    data catalog in a single run.
    """

    def __init__(
        self,
        config: MasterDataConfig,
        loader: MasterDataLoader,
        *,
        item_generators_builder: ItemGeneratorsBuilder | None = None,
        enrichment_generators_builder: EnrichmentGeneratorsBuilder | None = None,
        ai_images_builder: AIImageBuilder | None = None,
        report_writer: ReportWriter | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._loader = loader
        self._item_generators_builder = item_generators_builder or build_generators
        self._enrichment_generators_builder = (
            enrichment_generators_builder or build_enrichment_generators
        )
        self._ai_images_builder = ai_images_builder or self._default_ai_images
        self._report_writer = report_writer or build_report_writer(config)
        self._logger = logger or logging.getLogger("keemeds.master_data.pipeline")

    def run(self, *, output_dir: Path | None = None) -> GenerationReport:
        """
        Execute the full pipeline in dependency order.

        Returns the populated :class:`~master_data.reporting.GenerationReport`,
        which the caller uses to inspect counts, files and failures.
        """
        report = GenerationReport()

        master_data = self._loader.load()
        self._report_writer.collect_validation_issues(report, self._loader.validate())

        fatal = self._fatal_failure(master_data, report)
        if fatal:
            report.add_note("Fatal validation failure: no master data to generate from.")
        else:
            self._generate_and_export_masters(master_data, report)
            items = self._generate_items(master_data, report)
            if items is None:
                report.add_note("Fatal validation failure: no medicines generated.")
            else:
                self._export_items(items, report)
                self._generate_and_export_enrichment(items, report)
                self._generate_ai_images(items, report)

        report.finished_at = datetime.now()
        report_paths = self._report_writer.file_paths(output_dir)
        report.files_generated.extend(str(path) for path in report_paths)
        self._report_writer.write_all(report, output_dir=output_dir)
        return report

    # ------------------------------------------------------------------ #
    # Orchestration steps
    # ------------------------------------------------------------------ #

    def _fatal_failure(
        self,
        master_data: MasterDataResult,
        report: GenerationReport,
    ) -> bool:
        """Stop when no master data could be loaded (fatal validation failure)."""
        if not master_data.entities:
            report.add_note(
                "Skipped generation because no master data could be loaded."
            )
            return True
        return False

    def _generate_and_export_masters(
        self,
        master_data: MasterDataResult,
        report: GenerationReport,
    ) -> None:
        """
        Generate and export every Phase 7 ERPNext master workbook.

        The master records are derived from the loaded source data and written
        under the centralized master export specifications, so the workbooks
        carry the masters the generated items reference.
        """
        generator = MasterGenerator(config=self._config, master_data=master_data)
        records_by_key = generator.generate()

        for spec in self._config.master_exports():
            records = records_by_key.get(spec.key, [])
            export_report = export_records(
                records=records,
                spec=spec,
                config=self._config.export,
                logger=self._logger,
            )
            self._record_export(report, export_report, spec.key)

    def _generate_items(
        self,
        master_data: MasterDataResult,
        report: GenerationReport,
    ) -> GenerationResult | None:
        """Run every registered item generator; return the first item result."""
        generators = self._item_generators_builder(
            config=self._config, master_data=master_data
        )
        for generator in generators.values():
            try:
                result = generator.generate()
            except Exception as exc:
                report.add_note(f"Generator {generator.key} failed: {exc}")
                self._logger.error("Generator %s failed: %s", generator.key, exc)
                continue
            if result.count == 0:
                report.add_note(f"Generator {generator.name} produced no records.")
                continue
            self._logger.info(
                "Generated %s: %d record(s)", generator.name, result.count
            )
            return result
        return None

    def _export_items(
        self,
        items: GenerationResult,
        report: GenerationReport,
    ) -> None:
        """Export the generated items to ERPNext Item Import workbooks."""
        export_report = export_generation_result(
            result=items,
            config=self._config.export,
            logger=self._logger,
        )
        self._record_export(report, export_report, "items")

    def _generate_and_export_enrichment(
        self,
        items: GenerationResult,
        report: GenerationReport,
    ) -> None:
        """Run and export every registered enrichment generator."""
        generators = self._enrichment_generators_builder(
            config=self._config, items=items.items
        )
        for generator in generators.values():
            try:
                records = generator.generate()
            except Exception as exc:
                report.add_note(f"Generator {generator.key} failed: {exc}")
                self._logger.error("Generator %s failed: %s", generator.key, exc)
                continue
            spec = self._config.enrichment_export_for(generator.key)
            if spec is None:
                report.add_note(
                    f"Generator {generator.name} has no export spec; "
                    f"{len(records)} record(s) generated but not exported."
                )
                report.records_generated += len(records)
                continue
            export_report = export_records(
                records=records,
                spec=spec,
                config=self._config.export,
                logger=self._logger,
            )
            self._record_export(report, export_report, generator.name)

    def _record_export(
        self,
        report: GenerationReport,
        export_report: ExportReport,
        label: str,
    ) -> None:
        """Fold an export report into the aggregate and log the outcome."""
        report.record_export(export_report)
        self._logger.info(
            "%s: %d exported, %d skipped",
            label,
            export_report.exported,
            export_report.skipped,
        )
        if export_report.skipped:
            report.add_note(
                f"{label}: {export_report.skipped} record(s) skipped during export."
            )

    # ------------------------------------------------------------------ #
    # Phase 9.5 AI product image generation
    # ------------------------------------------------------------------ #

    def _default_ai_images(
        self,
        config: MasterDataConfig,
        **_: object,
    ) -> AIImageBundle:
        """Build the default Phase 9.5 AI image-pipeline bundle."""
        ai_config = config.ai_images
        return (
            build_image_prompt_generator(ai_config, logger=self._logger),
            build_ai_image_generator(ai_config, logger=self._logger),
            build_image_manifest_writer(
                ai_config, config.export.output_dir, logger=self._logger
            ),
        )

    def _generate_ai_images(
        self,
        items: GenerationResult,
        report: GenerationReport,
    ) -> None:
        """Generate prompts, product images (optimized + mirrored) and manifest."""
        prompt_gen, image_gen, manifest_writer = self._ai_images_builder(
            config=self._config
        )
        try:
            prompt_outcome = prompt_gen.run(items.items)
        except Exception as exc:
            report.add_note(f"AI image prompts failed: {exc}")
            self._logger.error("AI image prompts failed: %s", exc)
            prompt_outcome = None
        try:
            image_outcome = image_gen.run(items.items)
        except Exception as exc:
            report.add_note(f"AI image generation failed: {exc}")
            self._logger.error("AI image generation failed: %s", exc)
            image_outcome = None
        try:
            manifest_outcome = manifest_writer.write(items.items)
        except Exception as exc:
            report.add_note(f"AI image manifest failed: {exc}")
            self._logger.error("AI image manifest failed: %s", exc)
            manifest_outcome = None

        report.ai_prompts_generated = (
            prompt_outcome.generated if prompt_outcome else 0
        )
        report.ai_images_generated = (
            image_outcome.images_generated if image_outcome else 0
        )
        report.ai_images_optimized = (
            image_outcome.images_optimized if image_outcome else 0
        )
        report.ai_images_skipped = image_outcome.images_skipped if image_outcome else 0
        report.ai_image_failures = list(image_outcome.failed) if image_outcome else []
        report.ai_manifest_entries = (
            manifest_outcome.entries if manifest_outcome else 0
        )
        if report.ai_images_generated or report.ai_images_optimized:
            self._logger.info(
                "Phase 9.5 AI images: %d generated, %d optimized, %d skipped, "
                "%d manifest entries",
                report.ai_images_generated,
                report.ai_images_optimized,
                report.ai_images_skipped,
                report.ai_manifest_entries,
            )
