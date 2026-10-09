"""Position and hedge arithmetic for one selection.

Profit or loss on a selection is two numbers: what happens if it wins and if it loses. Matched backs at
price P and stake S add S(P-1) if it wins and -S if it loses; matched lays do the opposite. Greening up
places one more bet on the same selection so both numbers are equal:

  long  (win > lose): lay  x = (win - lose) / lay_price  at the best lay price on offer
  short (win < lose): back x = (lose - win) / back_price at the best back price on offer

Commission is ignored in the equalisation, as on the exchange's own cash-out (it only applies to net
winnings on the market). A hedge always moves the worst case up, never down.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass
class Exposure:
    win: float = 0.0          # P/L if the selection wins
    lose: float = 0.0         # P/L if it loses
    backed: float = 0.0       # matched back stake
    laid: float = 0.0         # matched lay stake (backer's stake)
    unmatched: float = 0.0    # money still waiting in unmatched orders on this selection

    @property
    def worst(self) -> float:
        return min(self.win, self.lose)

    @property
    def flat(self) -> bool:
        return abs(self.win - self.lose) < 0.01 and self.backed + self.laid > 0

    def to_dict(self) -> dict:
        return {"win": round(self.win, 2), "lose": round(self.lose, 2), "backed": round(self.backed, 2), "laid": round(self.laid, 2),
                "unmatched": round(self.unmatched, 2), "worst": round(self.worst, 2)}


def add_bet(e: Exposure, side: str, price: float, size: float) -> Exposure:
    """Exposure after a matched bet of `size` (backer's stake) at `price`."""
    if side.lower() == "back":
        return Exposure(e.win + size * (price - 1), e.lose - size, e.backed + size, e.laid, e.unmatched)
    return Exposure(e.win - size * (price - 1), e.lose + size, e.backed, e.laid + size, e.unmatched)


def exposure(orders: Iterable[dict], selection_id, handicap: float = 0.0) -> Exposure:
    """From Betfair current orders (sizeMatched, averagePriceMatched, side, sizeRemaining)."""
    e = Exposure()
    for o in orders:
        if o.get("selectionId") != selection_id or abs(float(o.get("handicap") or 0.0) - float(handicap or 0.0)) > 1e-9:
            continue
        m = float(o.get("sizeMatched") or 0.0)
        if m > 0:
            px = float(o.get("averagePriceMatched") or (o.get("priceSize") or {}).get("price") or 0.0)
            e = add_bet(e, o.get("side", "BACK"), px, m)
        e.unmatched += float(o.get("sizeRemaining") or 0.0)
    return e


@dataclass
class Hedge:
    side: str            # back | lay
    price: float
    size: float          # backer's stake to place
    after: Exposure

    def to_dict(self) -> dict:
        return {"side": self.side, "price": self.price, "size": self.size, "after": self.after.to_dict()}


def green_up(e: Exposure, best_back: Optional[float], best_lay: Optional[float]) -> Optional[Hedge]:
    """The single bet that makes the result the same whatever happens, at the prices on offer."""
    diff = e.win - e.lose
    if abs(diff) < 0.01:
        return None
    if diff > 0:
        if not best_lay or best_lay <= 1.0:
            return None
        size = round(diff / best_lay, 2)
        return Hedge("lay", best_lay, size, add_bet(e, "lay", best_lay, size))
    if not best_back or best_back <= 1.0:
        return None
    size = round(-diff / best_back, 2)
    return Hedge("back", best_back, size, add_bet(e, "back", best_back, size))


def free_bet(e: Exposure, best_lay: Optional[float]) -> Optional[Hedge]:
    """Lay off the matched back stake: losing case goes to zero, winning case keeps the price difference."""
    need = round(e.backed - e.laid, 2)
    if need < 0.01 or not best_lay:
        return None
    return Hedge("lay", best_lay, need, add_bet(e, "lay", best_lay, need))
