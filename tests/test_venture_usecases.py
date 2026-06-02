"""
Regression tests locking the 3 worked venture use cases to doc-expected
semantics. Implementation: dash/tools/venture_tools.py

Use-case docs (under /tmp/venture_scenarios/usecases/):
  - saas_series_a.md
  - retail_acquisition.md
  - jv_fuel_distribution.md
"""
from __future__ import annotations

import pytest

from dash.tools.venture_tools import (
    dcf,
    irr_moic,
    sensitivity_grid,
    unit_economics,
    partner_fit_score,
)


# ------------------------------------------------------------------ SAAS --

class TestSaaSSeriesA:
    """Use Case 1 — VentureDesk SaaS Series A, $8M check."""

    CASHFLOWS = [-8.0, -3.0, -1.0, 2.0, 6.0, 114.0]
    WACC = 0.20
    G = 0.03

    # Doc-stated expectations
    DOC_NPV = 38.67
    DOC_TV = 84.94
    DOC_IRR = 0.6636
    DOC_MOIC = 15.25
    DOC_LTV_CAC = 5.0
    TERMINAL_CF = 14.0  # normalized Yr5 FCF (strips $100M exit)
    INVESTED = 8.0       # Yr0 ask only

    def test_dcf_irr_moic(self):
        # IRR — exact match to doc (within ±0.005)
        rr = irr_moic(self.CASHFLOWS, invested=self.INVESTED)
        assert rr["ok"] is True
        assert rr["irr"] == pytest.approx(self.DOC_IRR, abs=0.005), (
            f"IRR drift: doc={self.DOC_IRR}, actual={rr['irr']}"
        )

        # MOIC = 122 / 8 = 15.25 with explicit invested kwarg
        assert rr["moic"] == pytest.approx(self.DOC_MOIC, rel=0.01), (
            f"MOIC: got {rr['moic']}, expected {self.DOC_MOIC}"
        )
        assert rr["total_invested"] == pytest.approx(self.INVESTED, rel=0.01)

        # DCF with terminal_cashflow=14 → doc NPV 38.67, TV 84.94
        d = dcf(self.CASHFLOWS, self.WACC, self.G,
                terminal_cashflow=self.TERMINAL_CF)
        assert d["ok"] is True
        assert d["npv"] == pytest.approx(self.DOC_NPV, rel=0.02), (
            f"NPV: got {d['npv']}, expected {self.DOC_NPV}"
        )
        assert d["terminal_value"] == pytest.approx(self.DOC_TV, rel=0.02), (
            f"TV: got {d['terminal_value']}, expected {self.DOC_TV}"
        )

    def test_unit_economics(self):
        # Doc says ltv_cac=5.0 raw (60k/12k). Effective applies gross_margin.
        r = unit_economics(cac=12000, ltv=60000, gross_margin=0.78,
                           payback_months=14)
        assert r["ok"] is True
        # Raw ratio matches doc
        assert r["ltv_cac"] == pytest.approx(self.DOC_LTV_CAC, rel=0.01), (
            f"raw ltv_cac: got {r['ltv_cac']}, expected {self.DOC_LTV_CAC}"
        )
        # Effective with margin: 60000 * 0.78 / 12000 = 3.9
        assert r["effective_ltv_cac"] == pytest.approx(3.9, rel=0.01)
        assert r["flag"] == "healthy"  # effective 3.9 >= 3.0


def test_saas_series_a_dcf_irr_moic():
    """Top-level wrapper requested by the task brief."""
    t = TestSaaSSeriesA()
    t.test_dcf_irr_moic()
    t.test_unit_economics()


# ------------------------------------------------------------- RETAIL ----

class TestRetailAcquisition:
    """Use Case 2 — Citymart × FreshMart Mandalay $25M acquisition."""

    BASE = [-25, 3.5, 5.2, 6.0, 6.8, 7.5, 8.2]
    WACC_RANGE = [0.08, 0.10, 0.12, 0.14]
    GROWTH_RANGE = [0.02, 0.025, 0.03, 0.035]

    def test_sensitivity_grid_shape_and_corners(self):
        r = sensitivity_grid(self.BASE, self.WACC_RANGE, self.GROWTH_RANGE)
        assert r["ok"] is True
        grid = r["grid"]

        # Shape: 4 wacc rows × 4 growth cols
        assert len(grid) == 4
        for row in grid:
            assert len(row) == 4

        assert r["wacc_axis"] == self.WACC_RANGE
        assert r["growth_axis"] == self.GROWTH_RANGE

        # Doc-stated corner values
        assert grid[0][0] == pytest.approx(90.6, rel=0.05), (
            f"[0][0]={grid[0][0]} (doc 90.6)"
        )
        assert grid[3][3] == pytest.approx(34.6, rel=0.05), (
            f"[3][3]={grid[3][3]} (doc 34.6)"
        )
        assert grid[2][1] == pytest.approx(44.1, rel=0.05), (
            f"[2][1]={grid[2][1]} (doc 44.1)"
        )

        # Monotonicity: NPV decreases as WACC rises (fixed g column)
        for col in range(4):
            col_vals = [grid[r_][col] for r_ in range(4)]
            assert col_vals == sorted(col_vals, reverse=True), (
                f"WACC monotonicity broken in col {col}: {col_vals}"
            )

        # Monotonicity: NPV increases as growth rises (fixed wacc row)
        for row_idx in range(4):
            row_vals = grid[row_idx]
            assert row_vals == sorted(row_vals), (
                f"Growth monotonicity broken in row {row_idx}: {row_vals}"
            )


def test_retail_acquisition_sensitivity():
    """Top-level wrapper requested by the task brief."""
    TestRetailAcquisition().test_sensitivity_grid_shape_and_corners()


# ----------------------------------------------------------------- JV ----

class TestJVFuelDistribution:
    """Use Case 3 — Citymart × FuelCo 60/40 last-mile JV."""

    # Dict-of-capability-to-score (doc shape)
    SELF_CAPS = {
        "retail_demand": 0.9,
        "route_density": 0.7,
        "brand": 0.8,
        "fleet_ops": 0.2,
        "fuel_procurement": 0.1,
        "depot_network": 0.1,
    }
    PARTNER_CAPS = {
        "retail_demand": 0.1,
        "route_density": 0.6,
        "brand": 0.3,
        "fleet_ops": 0.9,
        "fuel_procurement": 0.95,
        "depot_network": 0.85,
    }

    # Plain list version for legacy path
    SELF_LIST = ["retail_demand", "route_density", "brand"]
    PARTNER_LIST = ["fleet_ops", "fuel_procurement", "depot_network", "route_density"]

    def test_partner_fit_dict_overload(self):
        r = partner_fit_score(self.SELF_CAPS, self.PARTNER_CAPS)
        assert r["ok"] is True

        # Dict mode returns 0..1 `score`
        assert 0.0 <= r["score"] <= 1.0
        assert r["score"] > 0.3, (
            f"expected meaningful complement score, got {r['score']}"
        )

        # Overlap = caps present in both
        assert set(r["overlap"]) == {
            "retail_demand", "route_density", "brand",
            "fleet_ops", "fuel_procurement", "depot_network",
        }

        # Gaps = self caps where partner is weaker
        assert "retail_demand" in r["gaps"]
        assert "brand" in r["gaps"]
        # fleet_ops: self=0.2, partner=0.9 → not a gap
        assert "fleet_ops" not in r["gaps"]

    def test_partner_fit_list_legacy(self):
        # List path still returns 0..100 fit_score
        r = partner_fit_score(self.SELF_LIST, self.PARTNER_LIST)
        assert r["ok"] is True
        assert "fit_score" in r
        assert 0 <= r["fit_score"] <= 100
        assert r["overlap"] == ["route_density"]
        assert sorted(r["complement"]) == sorted(
            ["fleet_ops", "fuel_procurement", "depot_network"]
        )

    def test_partner_fit_validates_empty_inputs(self):
        assert partner_fit_score([], ["x"])["ok"] is False
        assert partner_fit_score(["x"], [])["ok"] is False
        assert partner_fit_score({}, {"x": 0.5})["ok"] is False

    def test_jv_dcf_irr_moic_consistency(self):
        # Bonus: lock the cashflow math from the JV doc
        cf = [-5.0, -0.6, 1.8, 3.0, 3.9, 22.5]
        d = dcf(cf, wacc=0.15, terminal_growth=0.02)
        assert d["ok"] is True
        assert d["npv"] > 0
        assert d["terminal_value"] > 0

        rr = irr_moic(cf)
        assert rr["ok"] is True
        # Doc IRR ≈ 0.5015
        assert rr["irr"] == pytest.approx(0.5015, abs=0.01)


def test_jv_fuel_partner_fit():
    """Top-level wrapper requested by the task brief."""
    t = TestJVFuelDistribution()
    t.test_partner_fit_dict_overload()
    t.test_partner_fit_list_legacy()
    t.test_partner_fit_validates_empty_inputs()
    t.test_jv_dcf_irr_moic_consistency()
