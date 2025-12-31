#!/usr/bin/env python3
"""Estimate joint federal taxes and suggest spouse-specific withholding targets."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class PaystubInput:
    spouse: str
    pay_frequency: str
    ytd_pay_periods: int
    ytd_gross: float
    ytd_pretax_deductions: float
    ytd_posttax_deductions: float
    ytd_federal_withheld: float


@dataclass
class TaxResult:
    spouse: str
    annualized_taxable_income: float
    annualized_gross: float
    annualized_pretax: float
    total_tax_share: float
    target_withholding_per_period: float
    target_withholding_rate: float
    remaining_periods: int


FREQUENCY_PERIODS = {
    "weekly": 52,
    "biweekly": 26,
    "semi-monthly": 24,
    "monthly": 12,
}


@dataclass(frozen=True)
class FederalTaxProfile:
    year: int
    standard_deduction_mfj: float
    brackets_mfj: tuple[tuple[float, float], ...]


TAX_PROFILES = {
    2024: FederalTaxProfile(
        year=2024,
        standard_deduction_mfj=29200.0,
        brackets_mfj=(
            (22000.0, 0.10),
            (89450.0, 0.12),
            (190750.0, 0.22),
            (364200.0, 0.24),
            (462500.0, 0.32),
            (693750.0, 0.35),
            (float("inf"), 0.37),
        ),
    ),
}


TEMPLATE_HEADERS = [
    "spouse",
    "pay_frequency",
    "ytd_pay_periods",
    "ytd_gross",
    "ytd_pretax_deductions",
    "ytd_posttax_deductions",
    "ytd_federal_withheld",
]


def create_template(path: Path) -> None:
    rows = [
        {
            "spouse": "spouse_a",
            "pay_frequency": "weekly",
            "ytd_pay_periods": "0",
            "ytd_gross": "0",
            "ytd_pretax_deductions": "0",
            "ytd_posttax_deductions": "0",
            "ytd_federal_withheld": "0",
        },
        {
            "spouse": "spouse_b",
            "pay_frequency": "biweekly",
            "ytd_pay_periods": "0",
            "ytd_gross": "0",
            "ytd_pretax_deductions": "0",
            "ytd_posttax_deductions": "0",
            "ytd_federal_withheld": "0",
        },
    ]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TEMPLATE_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def load_inputs(path: Path) -> list[PaystubInput]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [h for h in TEMPLATE_HEADERS if h not in reader.fieldnames]
        if missing:
            raise ValueError(f"Missing columns in CSV: {', '.join(missing)}")

        inputs: list[PaystubInput] = []
        for row in reader:
            spouse = row["spouse"].strip()
            if not spouse:
                continue
            pay_frequency = row["pay_frequency"].strip().lower()
            if pay_frequency not in FREQUENCY_PERIODS:
                raise ValueError(
                    f"Unsupported pay_frequency '{pay_frequency}' for {spouse}."
                    f" Use one of {', '.join(FREQUENCY_PERIODS)}."
                )

            inputs.append(
                PaystubInput(
                    spouse=spouse,
                    pay_frequency=pay_frequency,
                    ytd_pay_periods=int(row["ytd_pay_periods"]),
                    ytd_gross=float(row["ytd_gross"]),
                    ytd_pretax_deductions=float(row["ytd_pretax_deductions"]),
                    ytd_posttax_deductions=float(row["ytd_posttax_deductions"]),
                    ytd_federal_withheld=float(row["ytd_federal_withheld"]),
                )
            )

    if len(inputs) != 2:
        raise ValueError("Provide exactly two spouse rows in the CSV.")

    return inputs


def annualize(amount: float, ytd_periods: int, periods_per_year: int) -> float:
    if ytd_periods <= 0:
        raise ValueError("ytd_pay_periods must be greater than zero.")
    return amount / ytd_periods * periods_per_year


def compute_taxable_income(inputs: Iterable[PaystubInput]) -> dict[str, float]:
    taxable = {}
    for entry in inputs:
        periods_per_year = FREQUENCY_PERIODS[entry.pay_frequency]
        annual_gross = annualize(entry.ytd_gross, entry.ytd_pay_periods, periods_per_year)
        annual_pretax = annualize(
            entry.ytd_pretax_deductions, entry.ytd_pay_periods, periods_per_year
        )
        taxable_income = max(0.0, annual_gross - annual_pretax)
        taxable[entry.spouse] = taxable_income
    return taxable


def compute_federal_tax(taxable_income: float, profile: FederalTaxProfile) -> float:
    taxable_income = max(0.0, taxable_income - profile.standard_deduction_mfj)
    tax = 0.0
    lower_limit = 0.0
    for upper_limit, rate in profile.brackets_mfj:
        if taxable_income <= 0:
            break
        taxable_at_rate = min(upper_limit - lower_limit, taxable_income)
        tax += taxable_at_rate * rate
        taxable_income -= taxable_at_rate
        lower_limit = upper_limit
    return tax


def compute_results(inputs: list[PaystubInput], profile: FederalTaxProfile) -> list[TaxResult]:
    taxable_by_spouse = compute_taxable_income(inputs)
    total_taxable = sum(taxable_by_spouse.values())
    total_tax = compute_federal_tax(total_taxable, profile)

    results: list[TaxResult] = []
    for entry in inputs:
        periods_per_year = FREQUENCY_PERIODS[entry.pay_frequency]
        annual_gross = annualize(entry.ytd_gross, entry.ytd_pay_periods, periods_per_year)
        annual_pretax = annualize(
            entry.ytd_pretax_deductions, entry.ytd_pay_periods, periods_per_year
        )
        spouse_taxable = taxable_by_spouse[entry.spouse]
        share = spouse_taxable / total_taxable if total_taxable > 0 else 0.0
        target_tax = total_tax * share
        remaining_periods = max(0, periods_per_year - entry.ytd_pay_periods)
        remaining_tax = target_tax - entry.ytd_federal_withheld
        if remaining_periods > 0:
            per_period_target = remaining_tax / remaining_periods
        else:
            per_period_target = 0.0
        gross_per_period = annual_gross / periods_per_year if periods_per_year else 0.0
        withholding_rate = per_period_target / gross_per_period if gross_per_period else 0.0

        results.append(
            TaxResult(
                spouse=entry.spouse,
                annualized_taxable_income=spouse_taxable,
                annualized_gross=annual_gross,
                annualized_pretax=annual_pretax,
                total_tax_share=target_tax,
                target_withholding_per_period=per_period_target,
                target_withholding_rate=withholding_rate,
                remaining_periods=remaining_periods,
            )
        )

    return results


def format_currency(value: float) -> str:
    return f"${value:,.2f}"


def print_summary(
    inputs: list[PaystubInput],
    results: list[TaxResult],
    profile: FederalTaxProfile,
    last_year_tax: float | None,
) -> None:
    total_taxable = sum(r.annualized_taxable_income for r in results)
    total_tax = compute_federal_tax(total_taxable, profile)

    print("\nJoint Federal Tax Estimate")
    print("=" * 32)
    print(f"Tax year: {profile.year}")
    print(f"Standard deduction (MFJ): {format_currency(profile.standard_deduction_mfj)}")
    print(f"Annualized taxable income: {format_currency(total_taxable)}")
    print(f"Estimated federal tax: {format_currency(total_tax)}")
    if last_year_tax is not None:
        print(f"Last year total tax (reference): {format_currency(last_year_tax)}")

    print("\nSpouse Targets")
    print("=" * 32)
    for entry, result in zip(inputs, results):
        print(f"\n{result.spouse}")
        print(f"  Annualized gross: {format_currency(result.annualized_gross)}")
        print(f"  Annualized pretax: {format_currency(result.annualized_pretax)}")
        print(f"  Annualized taxable: {format_currency(result.annualized_taxable_income)}")
        print(f"  Tax share: {format_currency(result.total_tax_share)}")
        print(f"  Remaining pay periods: {result.remaining_periods}")
        print(
            f"  Suggested withholding per period: {format_currency(result.target_withholding_per_period)}"
        )
        print(
            "  Suggested withholding rate per period: "
            f"{result.target_withholding_rate * 100:.2f}%"
        )
        print(f"  YTD federal withheld: {format_currency(entry.ytd_federal_withheld)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate joint federal taxes and target spouse withholdings."
    )
    parser.add_argument(
        "--create-template",
        metavar="PATH",
        type=Path,
        help="Create a CSV template at PATH.",
    )
    parser.add_argument(
        "--input",
        metavar="PATH",
        type=Path,
        help="CSV input with YTD paystub data for both spouses.",
    )
    parser.add_argument(
        "--tax-year",
        type=int,
        default=2024,
        help="Federal tax year to use for brackets (default: 2024).",
    )
    parser.add_argument(
        "--last-year-tax",
        type=float,
        default=None,
        help="Optional last year total tax for reference.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.create_template:
        create_template(args.create_template)
        print(f"Template created at {args.create_template}")
        return

    if not args.input:
        parser.error("--input is required unless --create-template is used.")

    if args.tax_year not in TAX_PROFILES:
        raise ValueError(
            f"Unsupported tax year {args.tax_year}. Available: {', '.join(map(str, TAX_PROFILES))}."
        )

    inputs = load_inputs(args.input)
    profile = TAX_PROFILES[args.tax_year]
    results = compute_results(inputs, profile)
    print_summary(inputs, results, profile, args.last_year_tax)


if __name__ == "__main__":
    main()
