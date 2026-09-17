"""
Medicine Generator

Produces ``target_count`` (default 300) production-ready medicine records as
Python models using only the master data loaded by the
:class:`~master_data.master_loader.MasterDataLoader` (Item Groups, Brands,
Manufacturers and UOMs).

Design
------
- **Reuses the foundation** by consuming a :class:`~master_data.models.MasterDataResult`
  and rules from :class:`~master_data.config.MedicineConfig` (dependency
  injection).
- **Deterministic** - output depends only on the loaded master data and the
  configured rules; the same input always yields the same 300 records.
- **Unique** - every generated Item Code and Item Name is unique.
- **Split internally** - the 300 records are split into three labelled batches
  (Medicines 001-100, 101-200, 201-300).

Only the master-data fields (Item Group, Brand, Default UOM,
Default Item Manufacturer) are sourced from the loaded reference sets; they are
clamped to available values so the generator never invents reference data.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..base_generator import BaseGenerator
from ..item_models import GenerationResult, Item


@dataclass(frozen=True)
class MedicineVariant:
    """
    A single strength/form combination that yields one medicine name.
    """

    ingredient: str
    strength: str
    form: str


def _build_catalog() -> tuple[MedicineVariant, ...]:
    """
    Build the flat, ordered catalog of unique medicine variants.

    Each ingredient expands to ``strength x form`` variants so that the total
    comfortably exceeds the configured target count; the generator trims to the
    exact target in a deterministic way.
    """

    # Each entry: (ingredient, ((strength, form), ...))
    specs: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
        (
            "Paracetamol",
            (
                ("650 mg", "Tablet"),
                ("500 mg", "Tablet"),
                ("250 mg", "Tablet"),
                ("120 mg/5ml", "Syrup"),
                ("250 mg/5ml", "Syrup"),
                ("100 mg/ml", "Drops"),
                ("1 g", "Injection"),
                ("500 mg", "Dispersible Tablet"),
            ),
        ),
        (
            "Amoxicillin",
            (
                ("500 mg", "Capsule"),
                ("250 mg", "Capsule"),
                ("125 mg/5ml", "Dry Syrup"),
                ("250 mg/5ml", "Dry Syrup"),
                ("500 mg", "Dispersible Tablet"),
                ("125 mg", "Dispersible Tablet"),
                ("250 mg", "Injection"),
                ("1 g", "Injection"),
            ),
        ),
        (
            "Amoxicillin + Clavulanic Acid",
            (
                ("625 mg", "Tablet"),
                ("500 mg", "Tablet"),
                ("228.5 mg/5ml", "Dry Syrup"),
                ("457 mg/5ml", "Dry Syrup"),
                ("375 mg", "Tablet"),
            ),
        ),
        (
            "Ciprofloxacin",
            (
                ("500 mg", "Tablet"),
                ("250 mg", "Tablet"),
                ("750 mg", "Tablet"),
                ("100 mg/ml", "Eye Drops"),
                ("200 mg/100ml", "Infusion"),
                ("3 mg/ml", "Ear Drops"),
                ("500 mg", "Capsule"),
            ),
        ),
        (
            "Azithromycin",
            (
                ("500 mg", "Tablet"),
                ("250 mg", "Tablet"),
                ("200 mg/5ml", "Suspension"),
                ("500 mg", "Capsule"),
                ("250 mg", "Capsule"),
                ("100 mg/5ml", "Suspension"),
            ),
        ),
        (
            "Cetirizine",
            (
                ("10 mg", "Tablet"),
                ("5 mg", "Tablet"),
                ("1 mg/ml", "Syrup"),
                ("5 mg/5ml", "Syrup"),
                ("10 mg/5ml", "Syrup"),
                ("5 mg/5ml", "Eye Drops"),
            ),
        ),
        (
            "Pantoprazole",
            (
                ("40 mg", "Tablet"),
                ("20 mg", "Tablet"),
                ("40 mg", "Injection"),
                ("40 mg", "Capsule"),
                ("10 mg", "Tablet"),
                ("40 mg/5ml", "Suspension"),
            ),
        ),
        (
            "Omeprazole",
            (
                ("20 mg", "Capsule"),
                ("40 mg", "Capsule"),
                ("10 mg", "Capsule"),
                ("20 mg/5ml", "Suspension"),
                ("40 mg", "Injection"),
            ),
        ),
        (
            "Metformin",
            (
                ("500 mg", "Tablet"),
                ("850 mg", "Tablet"),
                ("1000 mg", "Tablet"),
                ("500 mg", "SR Tablet"),
                ("1000 mg", "XR Tablet"),
                ("250 mg/5ml", "Syrup"),
            ),
        ),
        (
            "Amlodipine",
            (
                ("5 mg", "Tablet"),
                ("10 mg", "Tablet"),
                ("2.5 mg", "Tablet"),
                ("5 mg", "Capsule"),
                ("10 mg", "Capsule"),
                ("0.5 mg/ml", "Syrup"),
            ),
        ),
        (
            "Atenolol",
            (
                ("50 mg", "Tablet"),
                ("25 mg", "Tablet"),
                ("100 mg", "Tablet"),
                ("50 mg/5ml", "Syrup"),
                ("25 mg/5ml", "Suspension"),
            ),
        ),
        (
            "Atorvastatin",
            (
                ("10 mg", "Tablet"),
                ("20 mg", "Tablet"),
                ("40 mg", "Tablet"),
                ("80 mg", "Tablet"),
                ("5 mg", "Tablet"),
            ),
        ),
        (
            "Domperidone",
            (
                ("10 mg", "Tablet"),
                ("5 mg", "Tablet"),
                ("1 mg/ml", "Syrup"),
                ("5 mg/5ml", "Suspension"),
                ("10 mg", "Suppository"),
                ("2 mg/ml", "Injection"),
            ),
        ),
        (
            "Aceclofenac",
            (
                ("100 mg", "Tablet"),
                ("50 mg", "Tablet"),
                ("200 mg", "Sustained Release Tablet"),
                ("100 mg", "Gel"),
                ("30 mg/ml", "Injection"),
                ("10 mg/5ml", "Suspension"),
            ),
        ),
        (
            "Diclofenac",
            (
                ("50 mg", "Tablet"),
                ("75 mg", "Tablet"),
                ("100 mg", "Sustained Release Tablet"),
                ("25 mg/ml", "Injection"),
                ("1%", "Gel"),
                ("10 mg", "Suppository"),
                ("50 mg/ml", "Eye Drops"),
            ),
        ),
        (
            "Ibuprofen",
            (
                ("400 mg", "Tablet"),
                ("200 mg", "Tablet"),
                ("600 mg", "Tablet"),
                ("100 mg/5ml", "Syrup"),
                ("400 mg", "Suspension"),
                ("100 mg", "Suppository"),
                ("50 mg/ml", "Oral Drops"),
            ),
        ),
        (
            "Levocetirizine",
            (
                ("5 mg", "Tablet"),
                ("2.5 mg", "Tablet"),
                ("0.5 mg/ml", "Syrup"),
                ("5 mg", "Oro-Dispersible Tablet"),
                ("2.5 mg/5ml", "Syrup"),
            ),
        ),
        (
            "Montelukast",
            (
                ("10 mg", "Tablet"),
                ("5 mg", "Chewable Tablet"),
                ("4 mg", "Chewable Tablet"),
                ("10 mg", "Oro-Dispersible Tablet"),
                ("4 mg/5ml", "Suspension"),
                ("10 mg/5ml", "Suspension"),
            ),
        ),
        (
            "Salbutamol",
            (
                ("2 mg", "Tablet"),
                ("4 mg", "Tablet"),
                ("2 mg/5ml", "Syrup"),
                ("1 mg/ml", "Oral Drops"),
                ("100 mcg/dose", "Inhaler"),
                ("Respiro 100 mcg", "Rotacaps"),
            ),
        ),
        (
            "Metoprolol",
            (
                ("50 mg", "Tablet"),
                ("25 mg", "Tablet"),
                ("100 mg", "Tablet"),
                ("50 mg", "ER Tablet"),
                ("50 mg/5ml", "Syrup"),
            ),
        ),
        (
            "Losartan",
            (
                ("50 mg", "Tablet"),
                ("25 mg", "Tablet"),
                ("100 mg", "Tablet"),
                ("50 mg", "Capsule"),
                ("100 mg/5ml", "Suspension"),
                ("50 mg/5ml", "Syrup"),
            ),
        ),
        (
            "Telmisartan",
            (
                ("40 mg", "Tablet"),
                ("20 mg", "Tablet"),
                ("80 mg", "Tablet"),
                ("40 mg/5ml", "Suspension"),
                ("80 mg/5ml", "Suspension"),
            ),
        ),
        (
            "Rosuvastatin",
            (
                ("10 mg", "Tablet"),
                ("20 mg", "Tablet"),
                ("5 mg", "Tablet"),
                ("40 mg", "Tablet"),
                ("10 mg/5ml", "Suspension"),
            ),
        ),
        (
            "Fluoxetine",
            (
                ("20 mg", "Capsule"),
                ("10 mg", "Capsule"),
                ("40 mg", "Capsule"),
                ("20 mg/5ml", "Syrup"),
                ("60 mg", "Tablet"),
            ),
        ),
        (
            "Escitalopram",
            (
                ("10 mg", "Tablet"),
                ("5 mg", "Tablet"),
                ("20 mg", "Tablet"),
                ("10 mg/5ml", "Syrup"),
                ("20 mg/ml", "Drops"),
            ),
        ),
        (
            "Cefixime",
            (
                ("200 mg", "Tablet"),
                ("100 mg", "Tablet"),
                ("50 mg/5ml", "Dry Syrup"),
                ("100 mg/5ml", "Dry Syrup"),
                ("200 mg", "Capsule"),
                ("400 mg", "Tablet"),
            ),
        ),
        (
            "Cefuroxime",
            (
                ("250 mg", "Tablet"),
                ("500 mg", "Tablet"),
                ("125 mg/5ml", "Dry Syrup"),
                ("250 mg/5ml", "Dry Syrup"),
                ("750 mg", "Injection"),
            ),
        ),
        (
            "Ofloxacin",
            (
                ("200 mg", "Tablet"),
                ("400 mg", "Tablet"),
                ("100 mg/5ml", "Syrup"),
                ("0.3%", "Eye Drops"),
                ("0.3%", "Ear Drops"),
                ("200 mg/100ml", "Infusion"),
            ),
        ),
        (
            "Clotrimazole",
            (
                ("1%", "Cream"),
                ("2%", "Cream"),
                ("1%", "Lotion"),
                ("200 mg", "Pessary"),
                ("5 mg/g", "Powder"),
                ("10%", "Solution"),
            ),
        ),
        (
            "Miconazole",
            (
                ("2%", "Cream"),
                ("2%", "Ointment"),
                ("2%", "Powder"),
                ("100 mg", "Pessary"),
                ("2%", "Gel"),
            ),
        ),
        (
            "Doxycycline",
            (
                ("100 mg", "Capsule"),
                ("100 mg", "Tablet"),
                ("50 mg", "Tablet"),
                ("100 mg/5ml", "Suspension"),
                ("40 mg", "Capsule"),
            ),
        ),
        (
            "Clindamycin",
            (
                ("300 mg", "Capsule"),
                ("150 mg", "Capsule"),
                ("1%", "Gel"),
                ("1%", "Lotion"),
                ("600 mg/4ml", "Injection"),
            ),
        ),
        (
            "Acyclovir",
            (
                ("200 mg", "Tablet"),
                ("400 mg", "Tablet"),
                ("800 mg", "Tablet"),
                ("5%", "Cream"),
                ("50 mg/ml", "Injection"),
            ),
        ),
        (
            "Ondansetron",
            (
                ("4 mg", "Tablet"),
                ("8 mg", "Tablet"),
                ("2 mg/5ml", "Syrup"),
                ("4 mg/2ml", "Injection"),
                ("4 mg", "Sublingual Tablet"),
                ("2 mg/ml", "Oral Drops"),
            ),
        ),
        (
            "Metoclopramide",
            (
                ("10 mg", "Tablet"),
                ("5 mg", "Tablet"),
                ("5 mg/5ml", "Syrup"),
                ("5 mg/ml", "Injection"),
                ("15 mg", "Sustained Release Tablet"),
            ),
        ),
        (
            "Dextromethorphan",
            (
                ("15 mg/5ml", "Syrup"),
                ("30 mg/5ml", "Syrup"),
                ("10 mg", "Tablet"),
                ("20 mg", "Tablet"),
                ("30 mg/5ml", "Cold Syrup"),
            ),
        ),
        (
            "Ambroxol",
            (
                ("30 mg", "Tablet"),
                ("75 mg", "Sustained Release Capsule"),
                ("15 mg/5ml", "Syrup"),
                ("30 mg/5ml", "Syrup"),
                ("15 mg/2ml", "Injection"),
                ("6 mg/ml", "Oral Drops"),
            ),
        ),
        (
            "Guaifenesin",
            (
                ("100 mg/5ml", "Syrup"),
                ("200 mg/5ml", "Syrup"),
                ("100 mg", "Tablet"),
                ("200 mg", "Tablet"),
                ("600 mg", "Extended Release Tablet"),
            ),
        ),
        (
            "Metronidazole",
            (
                ("400 mg", "Tablet"),
                ("200 mg", "Tablet"),
                ("100 mg/5ml", "Suspension"),
                ("500 mg/100ml", "Infusion"),
                ("200 mg/5ml", "Oral Gel"),
            ),
        ),
        (
            "Tramadol",
            (
                ("50 mg", "Capsule"),
                ("50 mg", "Tablet"),
                ("100 mg", "Sustained Release Tablet"),
                ("50 mg/ml", "Injection"),
            ),
        ),
        (
            "Pregabalin",
            (
                ("75 mg", "Capsule"),
                ("150 mg", "Capsule"),
                ("50 mg", "Capsule"),
                ("300 mg", "Capsule"),
            ),
        ),
        (
            "Gabapentin",
            (
                ("300 mg", "Capsule"),
                ("100 mg", "Capsule"),
                ("400 mg", "Capsule"),
                ("300 mg", "Tablet"),
            ),
        ),
        (
            "Hydrochlorothiazide",
            (
                ("12.5 mg", "Tablet"),
                ("25 mg", "Tablet"),
                ("50 mg", "Tablet"),
                ("25 mg/5ml", "Syrup"),
            ),
        ),
        (
            "Furosemide",
            (
                ("40 mg", "Tablet"),
                ("20 mg", "Tablet"),
                ("10 mg/ml", "Injection"),
                ("250 mg", "Tablet"),
            ),
        ),
        (
            "Nitroglycerin",
            (
                ("0.5 mg", "Sublingual Tablet"),
                ("2.5 mg", "Sustained Release Capsule"),
                ("2.6 mg", "Sustained Release Tablet"),
                ("5 mg/10ml", "Transdermal Patch"),
            ),
        ),
        (
            "Insulin Human",
            (
                ("40 IU/ml", "Injection"),
                ("100 IU/ml", "Injection"),
                ("100 IU/ml", "Pre-Filled Pen"),
            ),
        ),
        (
            "Insulin Glargine",
            (
                ("100 IU/ml", "Injection"),
                ("100 IU/ml", "Pre-Filled Pen"),
            ),
        ),
        (
            "Glyburide",
            (
                ("5 mg", "Tablet"),
                ("2.5 mg", "Tablet"),
                ("5 mg", "SR Tablet"),
            ),
        ),
        (
            "Glimepiride",
            (
                ("1 mg", "Tablet"),
                ("2 mg", "Tablet"),
                ("3 mg", "Tablet"),
            ),
        ),
        (
            "Voglibose",
            (
                ("0.2 mg", "Tablet"),
                ("0.3 mg", "Tablet"),
                ("0.2 mg", "Oro-Dispersible Tablet"),
            ),
        ),
        (
            "Nimesulide",
            (
                ("100 mg", "Tablet"),
                ("50 mg", "Tablet"),
                ("100 mg", "Gel"),
                ("30 mg/ml", "Injection"),
                ("100 mg/5ml", "Suspension"),
            ),
        ),
        (
            "Sertraline",
            (
                ("50 mg", "Tablet"),
                ("25 mg", "Tablet"),
                ("100 mg", "Tablet"),
                ("50 mg/5ml", "Syrup"),
            ),
        ),
        (
            "Clonazepam",
            (
                ("0.5 mg", "Tablet"),
                ("1 mg", "Tablet"),
                ("2 mg", "Tablet"),
                ("0.25 mg", "Tablet"),
            ),
        ),
        (
            "Alprazolam",
            (
                ("0.25 mg", "Tablet"),
                ("0.5 mg", "Tablet"),
                ("1 mg", "Tablet"),
            ),
        ),
        (
            "Capsaicin",
            (
                ("0.025%", "Cream"),
                ("0.075%", "Cream"),
                ("0.05%", "Gel"),
            ),
        ),
        (
            "Hydrocortisone",
            (
                ("1%", "Cream"),
                ("0.5%", "Cream"),
                ("100 mg", "Suppository"),
                ("100 mg/ml", "Injection"),
                ("1%", "Lotion"),
            ),
        ),
        (
            "Betamethasone",
            (
                ("0.05%", "Cream"),
                ("0.1%", "Cream"),
                ("0.1%", "Lotion"),
                ("0.05%", "Ointment"),
            ),
        ),
        (
            "Mupirocin",
            (
                ("2%", "Ointment"),
                ("2%", "Cream"),
                ("10 mg/g", "Nasal Ointment"),
            ),
        ),
        (
            "Terbinafine",
            (
                ("1%", "Cream"),
                ("250 mg", "Tablet"),
                ("1%", "Spray"),
                ("1%", "Solution"),
            ),
        ),
        (
            "Ketoconazole",
            (
                ("2%", "Cream"),
                ("2%", "Shampoo"),
                ("200 mg", "Tablet"),
                ("2%", "Gel"),
            ),
        ),
        (
            "Diltiazem",
            (
                ("30 mg", "Tablet"),
                ("60 mg", "Tablet"),
                ("90 mg", "Capsule"),
                ("2.5 mg/ml", "Injection"),
            ),
        ),
        (
            "Verapamil",
            (
                ("40 mg", "Tablet"),
                ("80 mg", "Tablet"),
                ("120 mg", "Sustained Release Tablet"),
                ("2.5 mg/ml", "Injection"),
            ),
        ),
    )

    variants: list[MedicineVariant] = []
    for ingredient, pairs in specs:
        for strength, form in pairs:
            variants.append(
                MedicineVariant(ingredient=ingredient, strength=strength, form=form)
            )
    return tuple(variants)


#: The flat, ordered medicine catalog built from the recipe above.
MEDICINE_CATALOG: tuple[MedicineVariant, ...] = _build_catalog()


def _item_name(variant: MedicineVariant) -> str:
    """
    Render a unique, human-readable medicine name from a variant.
    """

    return f"{variant.ingredient} {variant.strength} {variant.form}"


class MedicineGenerator(BaseGenerator):
    """
    Generates ``target_count`` unique, production-ready medicine records.
    """

    key = "medicine"
    name = "Medicines"

    def generate(self) -> GenerationResult:
        """
        Build exactly ``target_count`` medicine records split into batches.
        """

        self._ensure_master_data()
        rules = self._config.medicine

        batch_size = rules.batch_size
        items: list[Item] = []

        for index in range(rules.target_count):
            variant = MEDICINE_CATALOG[index]
            items.append(self._build_item(index, variant))

        return GenerationResult(
            batch_size=batch_size,
            batches=self.split_into_batches(items, batch_size),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _build_item(self, index: int, variant: MedicineVariant) -> Item:
        """
        Build a single item using the master data and configured rules.
        """

        rules = self._config.medicine
        name = _item_name(variant)

        brand = self._value_at(self.brands, index)
        manufacturer = self._value_at(self.manufacturers, index)
        item_group = self._item_group_for(index)
        uom = self._uom_for(variant.form)
        shelf_life = self._shelf_life_for(index, rules)
        country = rules.countries_of_origin[index % len(rules.countries_of_origin)]

        description = (
            f"{name} ({variant.ingredient}). "
            f"Manufactured by {manufacturer}, branded as {brand}. "
            f"Packed in {uom}."
        )

        return Item(
            item_code=self._item_code(index, rules),
            item_name=name,
            item_group=item_group,
            default_uom=uom,
            brand=brand,
            description=description,
            maintain_stock=True,
            allow_sales=True,
            allow_purchase=True,
            has_batch_no=True,
            has_expiry_date=True,
            shelf_life_in_days=shelf_life,
            country_of_origin=country,
            default_item_manufacturer=manufacturer,
            has_variants=False,
        )

    def _item_code(self, index: int, rules) -> str:
        """
        Render a deterministic, zero-padded item code (e.g. ``MED-001``).
        """

        return f"{rules.item_code_prefix}-{index + 1:0{rules.item_code_width}d}"

    def _item_group_for(self, index: int) -> str:
        """
        Prefer a medicine-like group, otherwise cycle through available ones.
        """

        available = self.item_groups
        medicine_like = [
            group
            for group in available
            if any(token in group.casefold() for token in ("medic", "allopath", "generic"))
        ]
        pool = medicine_like or available
        return self._value_at(pool, index)

    def _uom_for(self, form: str) -> str:
        """
        Map a dosage form to an available UOM, falling back to the first one.
        """

        available = self.uoms
        if not available:
            return ""
        form_tokens = form.casefold().split()
        for uom in available:
            if any(token in uom.casefold() for token in form_tokens):
                return uom
        return available[0]

    def _shelf_life_for(self, index: int, rules) -> int:
        """
        Derive a deterministic shelf life within the configured range.
        """

        low = rules.shelf_life_min_days
        high = rules.shelf_life_max_days
        span = max(high - low + 1, 1)
        return low + (index % span)

    def _value_at(self, values: list[str], index: int) -> str:
        """
        Select a value from ``values`` deterministically, cycling if needed.
        """

        if not values:
            return ""
        return values[index % len(values)]

    def _ensure_master_data(self) -> None:
        """
        Raise a clear error if required reference master data is unavailable.
        """

        missing = []
        if not self.brands:
            missing.append("Brands")
        if not self.manufacturers:
            missing.append("Manufacturers")
        if not self.item_groups:
            missing.append("Item Groups")
        if not self.uoms:
            missing.append("UOMs")

        if missing:
            raise ValueError(
                "Cannot generate medicines: missing master data for "
                + ", ".join(missing)
                + ". Load master data first."
            )
