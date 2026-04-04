"""
Position sizing via fractional Kelly criterion.
"""
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SizeResult:
    contracts: float   # number of shares/contracts to trade
    kelly_fraction: float
    bankroll_used: float
    edge: float
    prob: float
    odds: float        # decimal odds = 1 / prob


class KellySizer:
    """
    Fractional Kelly position sizer.

    Kelly formula for binary outcome with edge:
        f* = edge / (odds - 1)   [simplified for prob markets where odds = 1/prob]
        f* = (edge * prob) / (1 - prob)  ... but we use the general form.

    For a prediction-market contract priced at `p` paying $1 if YES:
        b = (1 - p) / p   (net odds)
        f* = (b * q - (1 - q)) / b   where q = estimated true probability

    Parameters
    ----------
    kelly_fraction : float
        Multiplier applied to full Kelly (e.g. 0.25 = quarter Kelly).
    max_position_pct : float
        Hard cap: maximum fraction of bankroll in any single trade.
    min_contracts : float
        Minimum trade size (set to 0 to disable).
    """

    def __init__(
        self,
        kelly_fraction: float = 0.25,
        max_position_pct: float = 0.05,
        min_contracts: float = 1.0,
    ):
        if not 0 < kelly_fraction <= 1:
            raise ValueError("kelly_fraction must be in (0, 1]")
        if not 0 < max_position_pct <= 1:
            raise ValueError("max_position_pct must be in (0, 1]")
        self.kelly_fraction = kelly_fraction
        self.max_position_pct = max_position_pct
        self.min_contracts = min_contracts

    def size(self, edge: float, poly_mid: float, bankroll: float) -> SizeResult:
        """
        Compute position size.

        Parameters
        ----------
        edge : float
            Estimated probability advantage (true_prob - market_prob).
        poly_mid : float
            Current Polymarket mid price (market implied probability).
        bankroll : float
            Current available bankroll in USD.

        Returns
        -------
        SizeResult with zero contracts if trade is not warranted.
        """
        if edge <= 0 or poly_mid <= 0 or poly_mid >= 1:
            return SizeResult(0, 0.0, 0.0, edge, poly_mid, 0.0)

        true_prob = poly_mid + edge
        true_prob = min(true_prob, 0.999)

        # Net odds per unit wagered on a $1-pays-$1 binary contract
        # Market price p => payout per dollar risked = (1-p)/p
        b = (1 - poly_mid) / poly_mid
        q = 1 - true_prob

        full_kelly = (b * true_prob - q) / b
        full_kelly = max(0.0, full_kelly)

        frac_kelly = full_kelly * self.kelly_fraction
        capped = min(frac_kelly, self.max_position_pct)

        bankroll_used = capped * bankroll
        contracts = bankroll_used / poly_mid  # each contract costs poly_mid dollars

        if contracts < self.min_contracts:
            logger.debug(
                "Size below minimum: %.2f contracts (edge=%.4f, mid=%.4f)",
                contracts, edge, poly_mid,
            )
            return SizeResult(0, capped, bankroll_used, edge, poly_mid, 1 / poly_mid)

        result = SizeResult(
            contracts=round(contracts, 2),
            kelly_fraction=capped,
            bankroll_used=round(bankroll_used, 4),
            edge=edge,
            prob=true_prob,
            odds=round(1 / poly_mid, 4),
        )
        logger.info(
            "Size: %.2f contracts | kelly=%.4f | bankroll_used=$%.2f | edge=%.4f",
            result.contracts, capped, result.bankroll_used, edge,
        )
        return result
