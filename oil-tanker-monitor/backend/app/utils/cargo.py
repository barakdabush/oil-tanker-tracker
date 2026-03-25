"""Cargo volume estimation utilities."""

from app.config import settings


def estimate_cargo_volume(length: float, beam: float, draft_change: float) -> float:
    """Estimate cargo volume in barrels using vessel dimensions and draft change.

    Formula: LBP × Beam × Δdraft × block_coefficient × density_factor
    1 cubic meter ≈ 6.29 barrels
    """
    if not length or not beam or not draft_change:
        return 0.0

    volume_m3 = abs(length) * abs(beam) * abs(draft_change) * settings.BLOCK_COEFFICIENT
    volume_tonnes = volume_m3 * settings.SEAWATER_DENSITY
    barrels = volume_m3 * 6.29  # 1 m³ ≈ 6.29 barrels of oil
    return round(barrels, 0)
