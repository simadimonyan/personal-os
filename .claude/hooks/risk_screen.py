#!/usr/bin/env python3
"""UserPromptSubmit — скрининг запроса до выполнения.

Два слоя проверки, оба по правилам из risk_rules.json:

1. Политики Anthropic — Usage Policy (AUP) и Consumer Terms. Правила с
   level="запрет" отмечают то, что политика запрещает прямо: подделка
   документов, мошенничество, безопасность несовершеннолетних, оружие,
   обучение сторонней модели на выводах Claude, обход правил сервиса.
2. Риск блокировки аккаунтов, на которых стоит система: Telegram, ВК, hh.
   Это level="осторожно" — не запрет, а «назови риск и предложи безопасный
   режим».

Хук не блокирует выполнение: он не может знать контекста (пентест по
договору, работа психологов с тяжёлой темой, черновик документа с пометкой
«образец»). Он кладёт в контекст, что именно зацепило и на какой пункт
политики это ложится, а решение — за агентом и за Димитри.

Правила править прямо в JSON.
"""

import json
import re
import sys
from pathlib import Path

RULES = Path(__file__).with_name("risk_rules.json")

AUP = "https://www.anthropic.com/legal/aup"
CONSUMER = "https://www.anthropic.com/legal/consumer-terms"


def load_rules() -> list[dict]:
    try:
        return json.loads(RULES.read_text(encoding="utf-8")).get("rules", [])
    except Exception:
        return []


def matches(rule: dict, text: str) -> bool:
    try:
        for pat in rule.get("all", []):
            if not re.search(pat, text, re.I):
                return False
        anys = rule.get("any", [])
        if anys and not any(re.search(p, text, re.I) for p in anys):
            return False
        return bool(rule.get("all") or anys)
    except re.error:
        return False


def block(rule: dict, label: str) -> str:
    src = " · ".join(x for x in (rule.get("policy"), rule.get("url")) if x)
    lines = [f"- {rule['title']}" + (f"\n  политика: {src}" if src else "")]
    lines.append(f"  почему: {rule['risk']}")
    lines.append(f"  {label}: {rule['safe']}")
    return "\n".join(lines)


def main() -> None:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return

    hits = [r for r in load_rules() if matches(r, prompt)]
    if not hits:
        return

    banned = [r for r in hits if r.get("level") == "запрет"]
    careful = [r for r in hits if r.get("level") != "запрет"]

    parts = [
        "Скрининг запроса (хук risk_screen): совпадения с политиками Anthropic "
        f"(Usage Policy {AUP}, Consumer Terms {CONSUMER}) и с тем, за что раньше "
        "блокировали аккаунты Димитри."
    ]
    if banned:
        parts.append(
            "ЗАПРЕЩЕНО ПОЛИТИКОЙ — эту часть запроса не выполнять:\n"
            + "\n".join(block(r, "что можно вместо") for r in banned)
        )
    if careful:
        parts.append(
            "ТРЕБУЕТ ОСТОРОЖНОСТИ — выполнимо, но с оговорками:\n"
            + "\n".join(block(r, "безопасный режим") for r in careful)
        )
    parts.append(
        "Как действовать:\n"
        "• «Запрещено»: не делай эту часть. Скажи одним предложением, что именно "
        "не сделаешь и почему, предложи ближайшее допустимое — и доведи до конца "
        "всё остальное в запросе. Без нотаций, без повторов, без морали.\n"
        "• «Осторожно»: это его собственные инструменты и его решение — не отказывай. "
        "До первого необратимого действия наружу (отправка, публикация, отклик, "
        "выкладывание наружу) назови риск двумя строками — что могут заблокировать и "
        "при каком масштабе — предложи безопасный режим и дождись явного «да». Дальше "
        "выполняй задачу целиком.\n"
        "• Сработало мимо (формулировка совпала, а сути нет — например, тема идёт как "
        "разбор, исследование или работа психологов, а не как инструкция): скажи одной "
        "строкой и работай дальше. Правила лежат в .claude/hooks/risk_rules.json и правятся."
    )

    titles = [r["title"] for r in banned] + [r["title"] for r in careful]
    tag = "⛔ политика: " if banned else "⚠️ риск: "
    print(json.dumps({
        "systemMessage": tag + ", ".join(titles),
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n".join(parts),
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
