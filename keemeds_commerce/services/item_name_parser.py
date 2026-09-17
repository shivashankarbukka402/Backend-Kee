"""
Item Name Parser

Derives the Salt Composition, Strength and Dosage Form from an ERPNext Item's
``item_name``.

The medicine catalog encodes these three facets inside a single display name in
the form ``<salt> <strength> <form>`` (e.g. ``Paracetamol 650 mg Tablet``,
``Amoxicillin + Clavulanic Acid 625 mg Tablet``). Because the strength always
contains a digit and the dosage form never does, the name can be decomposed
deterministically:

- **Salt Composition** is everything before the first token containing a digit.
- **Dosage Form** is the longest trailing run of tokens that matches a known
  dosage-form vocabulary entry (single- or multi-word).
- **Strength** is the tokens between the two.

This is a reusable domain service independent of the generator: extending the
dosage-form vocabulary supports future OTC, Wellness, Devices and Surgical
catalogs without changing the API or service layers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DIGIT = re.compile(r"\d")

#: Known dosage forms (single- and multi-word) that terminate a product name.
#: The longest entries are matched first so multi-word forms win over single
#: words (e.g. ``Pre-Filled Pen`` over ``Pen``).
_DOSAGE_FORMS_TUPLE = (
    "Oro-Dispersible Tablet",
    "Dispersible Tablet",
    "Chewable Tablet",
    "Sublingual Tablet",
    "Release Tablet",
    "Release Capsule",
    "Transdermal Patch",
    "Pre-Filled Pen",
    "Nasal Ointment",
    "Oral Drops",
    "Eye Drops",
    "Ear Drops",
    "Dry Syrup",
    "Cold Syrup",
    "SR Tablet",
    "XR Tablet",
    "ER Tablet",
    "Oral Gel",
    "Tablet",
    "Capsule",
    "Syrup",
    "Drops",
    "Injection",
    "Infusion",
    "Suspension",
    "Suppository",
    "Gel",
    "Inhaler",
    "Rotacaps",
    "Cream",
    "Lotion",
    "Pessary",
    "Powder",
    "Solution",
    "Ointment",
    "Patch",
    "Pen",
    "Spray",
    "Shampoo",
)

#: Longest-first ordering so multi-word forms are matched before single words.
_DOSAGE_FORMS: tuple[str, ...] = tuple(
    sorted(_DOSAGE_FORMS_TUPLE, key=len, reverse=True)
)


@dataclass(frozen=True)
class ParsedName:
    """
    Result of parsing an item name.

    Attributes
    ----------
    salt_composition:
        The salt/active-ingredient name, e.g. ``Paracetamol``.
    strength:
        The strength expression, e.g. ``650 mg``.
    dosage_form:
        The dosage form, e.g. ``Tablet``.
    """

    salt_composition: str = ""
    strength: str = ""
    dosage_form: str = ""

    def is_parseable(self) -> bool:
        return bool(self.salt_composition and self.strength and self.dosage_form)


class ItemNameParser:
    """
    Decomposes an ERPNext ``item_name`` into salt, strength and dosage form.
    """

    def parse(self, item_name: str) -> ParsedName:
        tokens = item_name.strip().split()
        if not tokens:
            return ParsedName()
        digit_index = self._first_digit_index(tokens)
        if digit_index is None:
            return ParsedName(salt_composition=" ".join(tokens))

        salt = " ".join(tokens[:digit_index])
        form_start = self._find_form_start(tokens, digit_index)
        strength = " ".join(tokens[digit_index:form_start])
        dosage_form = " ".join(tokens[form_start:])
        return ParsedName(
            salt_composition=salt,
            strength=strength,
            dosage_form=dosage_form,
        )

    @staticmethod
    def _first_digit_index(tokens: list[str]) -> int | None:
        for index, token in enumerate(tokens):
            if _DIGIT.search(token):
                return index
        return None

    @staticmethod
    def _find_form_start(tokens: list[str], digit_index: int) -> int:
        """Index where the dosage form run begins (never inside strength)."""
        length = len(tokens)
        for start in range(digit_index + 1, length + 1):
            if _starts_with_dosage_form(tokens, start):
                return start
        return length


def _starts_with_dosage_form(tokens: list[str], start: int) -> bool:
    """Whether ``tokens[start:]`` begins with a known dosage form."""
    remaining = tokens[start:]
    for word in _DOSAGE_FORMS:
        width = word.split()
        if len(remaining) < len(width):
            continue
        if " ".join(remaining[: len(width)]) == word:
            return True
    return False
