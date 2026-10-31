"""Card drawing and table artwork for Blackjack."""

import io

from PIL import Image, ImageDraw, ImageFont


BLACKJACK_ART_FILENAME = "blackjack-table.png"
BLACKJACK_BRAND = "CKR"
_ART_WIDTH = 1200
_ART_HEIGHT = 620
_CARD_WIDTH = 124
_CARD_HEIGHT = 172
_CARD_STEP = 101


def _art_font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _draw_card(draw, x: int, y: int, card, *, hidden: bool = False):
    """Draw a compact, readable card face or a patterned card back."""
    x2, y2 = x + _CARD_WIDTH, y + _CARD_HEIGHT
    if hidden or card is None:
        draw.rounded_rectangle(
            (x, y, x2, y2),
            radius=12,
            fill=(25, 43, 74),
            outline=(225, 190, 105),
            width=4,
        )
        draw.rounded_rectangle(
            (x + 9, y + 9, x2 - 9, y2 - 9),
            radius=8,
            outline=(144, 171, 210),
            width=2,
        )
        for offset in range(-_CARD_HEIGHT, _CARD_WIDTH, 18):
            draw.line(
                (x + max(10, offset), y + 12, min(x2 - 10, x + offset + _CARD_HEIGHT), y2 - 12),
                fill=(64, 91, 130),
                width=2,
            )
        draw.text(
            (x + _CARD_WIDTH // 2, y + _CARD_HEIGHT // 2),
            BLACKJACK_BRAND,
            fill=(242, 213, 150),
            font=_art_font(14),
            anchor="mm",
        )
        return

    rank, suit = card
    is_red = suit in ("♥", "♦")
    ink = (165, 45, 52) if is_red else (28, 35, 40)
    suit_letter = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}.get(suit, "?")
    draw.rounded_rectangle(
        (x, y, x2, y2),
        radius=12,
        fill=(243, 239, 225),
        outline=(210, 202, 181),
        width=3,
    )
    draw.text((x + 10, y + 7), str(rank), fill=ink, font=_art_font(26))
    draw.text((x + 12, y + 38), suit_letter, fill=ink, font=_art_font(17))
    draw.text(
        (x + _CARD_WIDTH // 2, y + _CARD_HEIGHT // 2 + 3),
        str(rank),
        fill=ink,
        font=_art_font(38),
        anchor="mm",
    )
    draw.rounded_rectangle(
        (x + 7, y + 7, x2 - 7, y2 - 7),
        radius=8,
        outline=(218, 211, 192),
        width=1,
    )


def _dealer_display_cards(dealer_cards, phase: str):
    """Hide every dealer card except the up-card while the player can still act."""
    cards = list(dealer_cards or [])
    if phase == "playing" and len(cards) > 1:
        return [(cards[0], False)] + [(None, True)] * (len(cards) - 1)
    return [(card, False) for card in cards] or [(None, True), (None, True)]


def render_blackjack_table(player_cards, dealer_cards, *, phase: str, amount: int) -> bytes:
    """Render a self-contained dark-felt blackjack table for the live V2 panel."""
    image = Image.new("RGB", (_ART_WIDTH, _ART_HEIGHT), (13, 24, 23))
    draw = ImageDraw.Draw(image)

    # Use felt and gold styling.
    draw.rounded_rectangle(
        (20, 18, _ART_WIDTH - 20, _ART_HEIGHT - 18),
        radius=30,
        fill=(22, 64, 53),
        outline=(187, 148, 77),
        width=4,
    )
    draw.rounded_rectangle(
        (34, 32, _ART_WIDTH - 34, _ART_HEIGHT - 32),
        radius=24,
        outline=(59, 119, 96),
        width=2,
    )
    draw.text(
        (_ART_WIDTH // 2, 54),
        f"{BLACKJACK_BRAND} CASINO  /  BLACKJACK",
        fill=(245, 211, 145),
        font=_art_font(24),
        anchor="mm",
    )
    draw.text(
        (_ART_WIDTH // 2, 87),
        f"STAKE  {max(0, int(amount)):,} COINS",
        fill=(215, 229, 216),
        font=_art_font(17),
        anchor="mm",
    )

    phase_labels = {
        "waiting": "READY TO DEAL",
        "starting": "SHUFFLING / DEALING",
        "playing": "YOUR TURN",
        "settling": "REVEALING RESULT",
        "pending": "SETTLEMENT IN PROGRESS",
        "finished": "HAND COMPLETE",
    }
    draw.text(
        (_ART_WIDTH // 2, 119),
        phase_labels.get(phase, "BLACKJACK"),
        fill=(185, 205, 191),
        font=_art_font(15),
        anchor="mm",
    )

    # Hide the dealer's hole card.
    dealer_row = _dealer_display_cards(dealer_cards, phase)

    player_row = [(card, False) for card in (player_cards or [])]
    if not player_row:
        player_row = [(None, True), (None, True)]

    def draw_row(cards, y, label):
        draw.text((60, y - 23), label, fill=(225, 234, 224), font=_art_font(16))
        count = len(cards)
        row_width = _CARD_WIDTH + _CARD_STEP * max(0, count - 1)
        start_x = max(50, (_ART_WIDTH - row_width) // 2)
        for index, (card, hidden) in enumerate(cards):
            _draw_card(draw, start_x + index * _CARD_STEP, y, card, hidden=hidden)

    draw_row(dealer_row, 145, "DEALER")
    draw.line((70, 339, _ART_WIDTH - 70, 339), fill=(102, 145, 112), width=2)
    draw_row(player_row, 370, "PLAYER")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=1)
    return buffer.getvalue()
