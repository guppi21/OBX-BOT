"""Standardized OBX typography and visual hierarchy system for Discord."""

from typing import Optional, List
import re

# Standard Dividers
DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
SHORT_DIVIDER = "──────────────"

# Section Labels (Bold Uppercase)
LABEL_MISSION = "**MISSION**"
LABEL_REWARD = "**REWARD**"
LABEL_STATUS = "**STATUS**"
LABEL_TIME_REMAINING = "**TIME REMAINING**"
LABEL_YOUR_POSITION = "**YOUR POSITION**"
LABEL_RESULT = "**RESULT**"
LABEL_OBJECTIVES = "**MISSION OBJECTIVES**"

# Curated Celebration Quotes
CELEBRATION_QUOTES = [
    "OBX acquired. Keep stacking.",
    "Another mission conquered.",
    "Your stack just got stronger.",
    "Reward secured successfully.",
    "One mission down. More OBX ahead.",
]


def format_h1(text: str) -> str:
    """Formats a major header (Level 1 Markdown)."""
    return f"# **{text.upper()}**"


def format_h2(text: str) -> str:
    """Formats a sub-header (Level 2 Markdown)."""
    return f"## **{text.upper()}**"


def format_h3(text: str) -> str:
    """Formats a tertiary header (Level 3 Markdown)."""
    return f"### **{text.upper()}**"


def format_section_label(text: str) -> str:
    """Formats a small uppercase section label."""
    clean = text.strip().upper()
    return f"**{clean}**"


def format_reward_amount(amount: int, prefix_plus: bool = True) -> str:
    """Formats a reward amount with diamond icon."""
    sign = "+" if prefix_plus and amount > 0 else ""
    return f"💎 **{sign}{amount:,} OBX**"


def format_spots(available_spots: int) -> str:
    """Formats available capacity or spots."""
    return f"👥 **{available_spots} SPOTS**"


def format_time_remaining(time_str: str) -> str:
    """Formats time remaining with hourglass."""
    return f"⏳ **{time_str.upper()}**"


def format_discord_blockquote(text: str, add_quotes: bool = True) -> str:
    """Formats multiline text into Discord native blockquotes."""
    if not text:
        return ""
    clean = text.strip().strip('"').strip("'").strip("“").strip("”")
    lines = clean.split("\n")
    quoted_lines = []
    for line in lines:
        s = line.strip()
        if s:
            quoted_lines.append(f"> *{s}*")
        else:
            quoted_lines.append(">")

    if add_quotes and quoted_lines:
        first_i = next((i for i, l in enumerate(quoted_lines) if l.startswith("> *")), None)
        last_i = next((i for i in reversed(range(len(quote_lines))) if quote_lines[i].startswith("> *")), None)
        if first_i is not None:
            quoted_lines[first_i] = quote_lines[first_i].replace("> *", "> *“", 1)
        if last_i is not None:
            if quote_lines[last_i].endswith("*"):
                quoted_lines[last_i] = quote_lines[last_i][:-1] + "”*"
            else:
                quoted_lines[last_i] += "”"

    return "\n".join(quoted_lines)
