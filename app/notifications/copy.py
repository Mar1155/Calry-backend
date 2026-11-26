"""Localized push copy.

Pushes are rendered on the server, so their text follows ``users.locale``
(the language the app last reported). Tone follows the product: calm, never
guilt ("you didn't log"), never a streak. Every key exists in every locale.
"""

from typing import Any

SUPPORTED_LOCALES = ("en", "it", "es", "de", "fr", "zh", "ja", "ar")

_COPY: dict[str, dict[str, str]] = {
    "en": {
        "first_meal.lunch.title": "What's for lunch?",
        "first_meal.dinner.title": "What's for dinner?",
        "first_meal.body": "A photo is enough. I'll take care of the numbers.",
        "meal_reminder.lunch.title": "Lunch done?",
        "meal_reminder.dinner.title": "Dinner done?",
        "meal_reminder.body": "Tell me in a few words, or snap a photo.",
        "analysis_ready.title": "Your meal is ready",
        "analysis_ready.body": "{meal}: about {kcal} kcal. Tap to check it.",
        "meal.breakfast": "Breakfast",
        "meal.lunch": "Lunch",
        "meal.dinner": "Dinner",
        "meal.snack": "Snack",
    },
    "it": {
        "first_meal.lunch.title": "Cosa c'è a pranzo?",
        "first_meal.dinner.title": "Cosa c'è a cena?",
        "first_meal.body": "Basta una foto, ai numeri penso io.",
        "meal_reminder.lunch.title": "Pranzo fatto?",
        "meal_reminder.dinner.title": "Cena fatta?",
        "meal_reminder.body": "Dimmelo in due parole, o con una foto.",
        "analysis_ready.title": "Il tuo pasto è pronto",
        "analysis_ready.body": "{meal}: circa {kcal} kcal. Tocca per controllare.",
        "meal.breakfast": "Colazione",
        "meal.lunch": "Pranzo",
        "meal.dinner": "Cena",
        "meal.snack": "Spuntino",
    },
    "es": {
        "first_meal.lunch.title": "¿Qué hay para comer?",
        "first_meal.dinner.title": "¿Qué hay para cenar?",
        "first_meal.body": "Basta una foto, yo me encargo de los números.",
        "meal_reminder.lunch.title": "¿Ya comiste?",
        "meal_reminder.dinner.title": "¿Ya cenaste?",
        "meal_reminder.body": "Cuéntamelo en pocas palabras o con una foto.",
        "analysis_ready.title": "Tu comida está lista",
        "analysis_ready.body": "{meal}: unas {kcal} kcal. Toca para revisarla.",
        "meal.breakfast": "Desayuno",
        "meal.lunch": "Almuerzo",
        "meal.dinner": "Cena",
        "meal.snack": "Tentempié",
    },
    "de": {
        "first_meal.lunch.title": "Was gibt's zum Mittagessen?",
        "first_meal.dinner.title": "Was gibt's zum Abendessen?",
        "first_meal.body": "Ein Foto reicht, um die Zahlen kümmere ich mich.",
        "meal_reminder.lunch.title": "Schon zu Mittag gegessen?",
        "meal_reminder.dinner.title": "Schon zu Abend gegessen?",
        "meal_reminder.body": "Erzähl's mir in ein paar Worten oder mit einem Foto.",
        "analysis_ready.title": "Deine Mahlzeit ist fertig",
        "analysis_ready.body": "{meal}: etwa {kcal} kcal. Tippe zum Prüfen.",
        "meal.breakfast": "Frühstück",
        "meal.lunch": "Mittagessen",
        "meal.dinner": "Abendessen",
        "meal.snack": "Snack",
    },
    "fr": {
        "first_meal.lunch.title": "Qu'est-ce qu'on mange ce midi ?",
        "first_meal.dinner.title": "Qu'est-ce qu'on mange ce soir ?",
        "first_meal.body": "Une photo suffit, je m'occupe des chiffres.",
        "meal_reminder.lunch.title": "Déjeuner terminé ?",
        "meal_reminder.dinner.title": "Dîner terminé ?",
        "meal_reminder.body": "Dis-le-moi en quelques mots, ou avec une photo.",
        "analysis_ready.title": "Ton repas est prêt",
        "analysis_ready.body": "{meal} : environ {kcal} kcal. Touche pour vérifier.",
        "meal.breakfast": "Petit-déjeuner",
        "meal.lunch": "Déjeuner",
        "meal.dinner": "Dîner",
        "meal.snack": "En-cas",
    },
    "zh": {
        "first_meal.lunch.title": "午饭吃什么？",
        "first_meal.dinner.title": "晚饭吃什么？",
        "first_meal.body": "拍张照片就行，数字交给我。",
        "meal_reminder.lunch.title": "午饭吃过了吗？",
        "meal_reminder.dinner.title": "晚饭吃过了吗？",
        "meal_reminder.body": "简单说几句或拍张照片告诉我。",
        "analysis_ready.title": "你的餐食分析好了",
        "analysis_ready.body": "{meal}：约 {kcal} 千卡。点按查看。",
        "meal.breakfast": "早餐",
        "meal.lunch": "午餐",
        "meal.dinner": "晚餐",
        "meal.snack": "加餐",
    },
    "ja": {
        "first_meal.lunch.title": "お昼は何を食べますか？",
        "first_meal.dinner.title": "夕食は何を食べますか？",
        "first_meal.body": "写真1枚で大丈夫。数字は私に任せてください。",
        "meal_reminder.lunch.title": "お昼は済みましたか？",
        "meal_reminder.dinner.title": "夕食は済みましたか？",
        "meal_reminder.body": "ひとことでも、写真でも教えてください。",
        "analysis_ready.title": "食事の分析ができました",
        "analysis_ready.body": "{meal}：約{kcal} kcal。タップして確認。",
        "meal.breakfast": "朝食",
        "meal.lunch": "昼食",
        "meal.dinner": "夕食",
        "meal.snack": "間食",
    },
    "ar": {
        "first_meal.lunch.title": "ماذا على الغداء؟",
        "first_meal.dinner.title": "ماذا على العشاء؟",
        "first_meal.body": "تكفي صورة واحدة، وأنا أتولى الأرقام.",
        "meal_reminder.lunch.title": "هل تناولت الغداء؟",
        "meal_reminder.dinner.title": "هل تناولت العشاء؟",
        "meal_reminder.body": "أخبرني ببضع كلمات أو بصورة.",
        "analysis_ready.title": "وجبتك جاهزة",
        "analysis_ready.body": "{meal}: حوالي {kcal} سعرة حرارية. اضغط للمراجعة.",
        "meal.breakfast": "الفطور",
        "meal.lunch": "الغداء",
        "meal.dinner": "العشاء",
        "meal.snack": "وجبة خفيفة",
    },
}


def resolve_locale(locale: str | None) -> str:
    primary = (locale or "en").split(",")[0].split("-")[0].split("_")[0].strip().lower()
    return primary if primary in _COPY else "en"


def push_text(locale: str | None, key: str, **values: Any) -> str:
    template = _COPY[resolve_locale(locale)].get(key) or _COPY["en"][key]
    return template.format(**values) if values else template
