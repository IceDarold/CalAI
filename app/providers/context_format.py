"""Shared context formatting for LLM orchestrators.

Used by both YandexGPT and GigaChat — single place to change how
context is presented to the LLM.
"""

import datetime as dt


def format_context_for_llm(context: dict | None, user_message: str) -> str:
    """Format context dict into a text block the LLM receives as user message."""
    parts = [f"Сообщение пользователя: {user_message}", ""]

    if not context:
        parts.append("Контекст: это первое сообщение, истории пока нет.")
        parts.append("")
    else:
        if context.get("all_meals"):
            parts.append("Все приёмы пищи (последние 7 дней):")
            current_date = None
            for m in context["all_meals"]:
                if m["date"] != current_date:
                    current_date = m["date"]
                    parts.append(f"  📅 {current_date}:")
                items = ", ".join(f"{it['name']} ({it['grams']})" for it in m.get("items", []))
                idx_str = f"#{m['today_idx']} " if m.get("today_idx") else f"id={m['id']} "
                parts.append(f"    [{m['time']}] {idx_str}{m['meal_type']}: {items} — {m.get('calories', '?')}")
            parts.append("")

        if context.get("profile"):
            p = context["profile"]
            profile_parts = []
            if p.get("height_cm"): profile_parts.append(f"рост {p['height_cm']} см")
            if p.get("weight_kg"): profile_parts.append(f"вес {p['weight_kg']} кг")
            if p.get("age"): profile_parts.append(f"возраст {p['age']}")
            if p.get("gender"): profile_parts.append(f"пол {p['gender']}")
            if p.get("goal"): profile_parts.append(f"цель {p['goal']}")
            if profile_parts:
                parts.append(f"Профиль пользователя: {', '.join(profile_parts)}")
                targets = []
                if p.get("target_kcal"): targets.append(f"{p['target_kcal']} ккал")
                if p.get("target_protein_g"): targets.append(f"Б:{p['target_protein_g']}г")
                if p.get("target_fat_g"): targets.append(f"Ж:{p['target_fat_g']}г")
                if p.get("target_carbs_g"): targets.append(f"У:{p['target_carbs_g']}г")
                if targets:
                    parts.append(f"  Дневная цель: {', '.join(targets)}")
                parts.append("")

        if context.get("totals_today"):
            t = context["totals_today"]
            parts.append(f"Итого сегодня: {t.get('calories', '?')}, белок {t.get('protein', '?')}")
            parts.append("")

        if context.get("remaining"):
            r = context["remaining"]
            parts.append(f"Осталось до цели: {r['kcal']} ккал, Б:{r['protein_g']:.0f}г Ж:{r['fat_g']:.0f}г У:{r['carbs_g']:.0f}г")
            parts.append("")

        if context.get("history"):
            parts.append("История переписки:")
            for msg in context["history"]:
                parts.append(f"  пользователь: {msg['text']}")
            parts.append("")

        if context.get("reply_to"):
            parts.append(f"Пользователь ОТВЕТИЛ на это сообщение бота: \"{context['reply_to'][:300]}\"")
            parts.append("Это значит что его текущее сообщение относится именно к этому сообщению.")
            parts.append("")

    parts.append(f"Текущее время (UTC): {dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M')}")
    parts.append("")

    return "\n".join(parts)
