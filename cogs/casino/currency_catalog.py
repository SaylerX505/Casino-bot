"""Static economy item catalog and display formatting shared by commands and views."""

from cogs.casino import bank

COIN_EMOJI = "<:ckrcoin:1513743032124575784>"

DAILY_REWARD_MIN = 40
DAILY_REWARD_MAX = 60
CONSUMABLE_REWARDS = {
    "luck_ticket": (50, 70, 90, 110, 140),
    "loot_box": (60, 90, 120, 180, 250, 320, 400),
}

INVENTORY_ITEMS = (
    ("double_shot", "طلقة مزدوجة", "tools", 100, "تسمح باستهداف لاعب إضافي في الروليت.", "🔫"),
    ("shield", "درع الحفرة", "tools", 275, "يصد الاستهداف في الروليت ويردّه على المهاجم.", "🛡️"),
    ("mafia_scan", "كشف مافيا", "tools", 150, "يوفر تلميحًا عن أحد أفراد المافيا أثناء اللعبة.", "🔍"),
    ("veto", "فيتو", "tools", 200, "يلغي صوتًا ضدك أثناء تصويت المافيا.", "⛔"),
    ("roulette_ins", "تأمين روليت", "tools", 300, "يمنح فرصة نجاة إضافية في الروليت.", "💉"),
    ("luck_ticket", "تذكرة حظ", "consumables", 120, "استخدمها لتحصل على مكافأة عشوائية حتى 140.", "🎟️"),
    ("loot_box", "صندوق حظ", "consumables", 250, "افتحه لتحصل على مكافأة عشوائية حتى 400.", "🎁"),
)


def fmt(value) -> str:
    return bank.fmt(value)
