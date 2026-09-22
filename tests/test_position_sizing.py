from __future__ import annotations

import pytest

from app.mt5.market_data import SymbolSpec
from app.risk.position_sizing import (
    PositionSizingError,
    _volume_decimals,
    calculate_position_size,
    loss_per_lot,
    normalize_volume,
)


def eurusd_like_spec(**overrides) -> SymbolSpec:
    defaults = dict(
        name="EURUSD",
        digits=5,
        point=0.00001,
        spread=10,
        spread_float=True,
        trade_contract_size=100_000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_tick_value=1.0,  # value of a 1-tick (0.00001) move for 1.0 lot
        trade_tick_size=0.00001,
        currency_base="EUR",
        currency_profit="USD",
        currency_margin="EUR",
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


class TestNaivePositionSizingFormulaIsNotUsed:
    def test_lot_size_depends_on_stop_distance_not_just_balance_and_risk(self):
        """The naive (and wrong) formula `lot = balance * risk` ignores
        stop-loss distance entirely. Two trades with the same equity/risk
        but different stop distances must get different lot sizes."""
        spec = eurusd_like_spec(trade_tick_value=10.0, trade_tick_size=0.0001)  # $10/pip/lot
        tight = calculate_position_size(10_000, 0.01, entry_price=1.1000, stop_loss_price=1.0990, spec=spec)
        wide = calculate_position_size(10_000, 0.01, entry_price=1.1000, stop_loss_price=1.0950, spec=spec)
        assert tight.volume > wide.volume


class TestCalculatePositionSize:
    def test_known_values_produce_expected_lot_size(self):
        # $10 tick value per 0.0001 ("pip") move per standard lot.
        spec = eurusd_like_spec(trade_tick_value=10.0, trade_tick_size=0.0001, volume_step=0.01)
        result = calculate_position_size(
            equity=10_000, risk_per_trade=0.01, entry_price=1.1000, stop_loss_price=1.0950, spec=spec
        )
        # 50 pips stop * $10/pip = $500 loss per lot. Risk = 1% * 10000 = $100.
        # 100 / 500 = 0.2 lots exactly.
        assert result.volume == pytest.approx(0.2)
        assert result.risk_amount_actual == pytest.approx(100.0)
        assert not result.exceeds_target_risk

    def test_volume_rounds_down_to_step(self):
        spec = eurusd_like_spec(trade_tick_value=10.0, trade_tick_size=0.0001, volume_step=0.1)
        result = calculate_position_size(
            equity=10_000, risk_per_trade=0.01, entry_price=1.1000, stop_loss_price=1.0950, spec=spec
        )
        # Raw 0.2 lots is already on-step here; force a case that isn't.
        spec2 = eurusd_like_spec(trade_tick_value=13.0, trade_tick_size=0.0001, volume_step=0.1)
        result2 = calculate_position_size(
            equity=10_000, risk_per_trade=0.01, entry_price=1.1000, stop_loss_price=1.0950, spec=spec2
        )
        # 100 / (50*13) = 0.1538... -> rounds down to 0.1
        assert result2.volume == pytest.approx(0.1)
        assert result.volume == pytest.approx(0.2)

    def test_tiny_equity_flags_exceeds_target_risk_instead_of_oversizing(self):
        spec = eurusd_like_spec(trade_tick_value=10.0, trade_tick_size=0.0001, volume_min=0.01)
        result = calculate_position_size(
            equity=50, risk_per_trade=0.01, entry_price=1.1000, stop_loss_price=1.0950, spec=spec
        )
        assert result.volume == 0.0
        assert result.exceeds_target_risk is True

    def test_volume_respects_min_and_max(self):
        spec = eurusd_like_spec(volume_min=0.5, volume_max=1.0, trade_tick_value=0.01, trade_tick_size=0.00001)
        result = calculate_position_size(
            equity=1_000_000, risk_per_trade=0.01, entry_price=1.1000, stop_loss_price=1.0990, spec=spec
        )
        assert result.volume <= 1.0

    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(equity=0, risk_per_trade=0.01, entry_price=1.1, stop_loss_price=1.09),
            dict(equity=10_000, risk_per_trade=0, entry_price=1.1, stop_loss_price=1.09),
            dict(equity=10_000, risk_per_trade=1.5, entry_price=1.1, stop_loss_price=1.09),
            dict(equity=10_000, risk_per_trade=0.01, entry_price=1.1, stop_loss_price=1.1),
        ],
    )
    def test_invalid_inputs_raise(self, kwargs):
        with pytest.raises(PositionSizingError):
            calculate_position_size(spec=eurusd_like_spec(), **kwargs)


class TestNormalizeVolume:
    def test_rounds_down_never_up(self):
        spec = eurusd_like_spec(volume_step=0.01, volume_min=0.01, volume_max=10)
        assert normalize_volume(0.239, spec) == pytest.approx(0.23)

    def test_clamped_to_min(self):
        spec = eurusd_like_spec(volume_step=0.01, volume_min=0.05, volume_max=10)
        assert normalize_volume(0.001, spec) == pytest.approx(0.05)

    def test_clamped_to_max(self):
        spec = eurusd_like_spec(volume_step=0.01, volume_min=0.01, volume_max=1.0)
        assert normalize_volume(50, spec) == pytest.approx(1.0)

    def test_invalid_volume_step_raises(self):
        spec = eurusd_like_spec(volume_step=0)
        with pytest.raises(PositionSizingError):
            normalize_volume(0.1, spec)


class TestVolumeDecimals:
    def test_non_positive_step_defaults_to_two_decimals(self):
        assert _volume_decimals(0) == 2
        assert _volume_decimals(-1) == 2

    def test_whole_number_step_has_zero_decimals(self):
        assert _volume_decimals(1.0) == 0

    def test_fractional_step_counts_decimals(self):
        assert _volume_decimals(0.01) == 2


class TestLossPerLot:
    def test_zero_or_negative_price_distance_raises(self):
        spec = eurusd_like_spec()
        with pytest.raises(PositionSizingError):
            loss_per_lot(0, spec)
        with pytest.raises(PositionSizingError):
            loss_per_lot(-0.001, spec)

    def test_invalid_tick_size_raises(self):
        spec = eurusd_like_spec(trade_tick_size=0)
        with pytest.raises(PositionSizingError):
            loss_per_lot(0.001, spec)

    def test_normal_case(self):
        spec = eurusd_like_spec(trade_tick_value=10.0, trade_tick_size=0.0001)
        assert loss_per_lot(0.0050, spec) == pytest.approx(500.0)  # 50 pips * $10/pip


class TestNonPositiveLossPerLot:
    def test_zero_tick_value_raises_in_calculate_position_size(self):
        spec = eurusd_like_spec(trade_tick_value=0.0, trade_tick_size=0.0001)
        with pytest.raises(PositionSizingError):
            calculate_position_size(equity=10_000, risk_per_trade=0.01, entry_price=1.1000, stop_loss_price=1.0950, spec=spec)
