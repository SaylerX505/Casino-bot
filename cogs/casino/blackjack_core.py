"""Blackjack card model, scoring, and outcome rules."""

import secrets

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
DEALER_STANDS_AT = 17
NATURAL_PAYOUT = 250
REGULAR_PAYOUT = 200
PAYOUT_DENOMINATOR = 100
RECOVERY_DELAYS = (2, 5, 15, 30, 60)


def _new_shuffled_deck():
    deck = [(rank, suit) for suit in SUITS for rank in RANKS]
    secrets.SystemRandom().shuffle(deck)
    return deck


def _hand_value(cards):
    total = 0
    aces = 0
    for rank, _suit in cards:
        if rank == "A":
            total += 11
            aces += 1
        elif rank in ("J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _is_natural_blackjack(cards):
    return len(cards) == 2 and _hand_value(cards) == 21


def _initial_outcome(player_cards, dealer_cards):
    player_natural = _is_natural_blackjack(player_cards)
    dealer_natural = _is_natural_blackjack(dealer_cards)
    if player_natural and dealer_natural:
        return "push"
    if player_natural:
        return "natural"
    if dealer_natural:
        return "loss"
    return None


def _final_outcome(player_cards, dealer_cards):
    player = _hand_value(player_cards)
    dealer = _hand_value(dealer_cards)
    if player > 21:
        return "loss"
    if dealer > 21:
        return "win"
    if player > dealer:
        return "win"
    if player < dealer:
        return "loss"
    return "push"



def _cards_text(cards):
    return "  ".join(f"[{rank}{suit}]" for rank, suit in cards) or "—"
