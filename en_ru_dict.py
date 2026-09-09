"""
Offline English → Russian dictionary for game subtitle translation.
Used as fallback when Google Translate is unavailable (no internet).
Covers common game/UI/subtitle vocabulary.
"""
import os
import json
import logging

logger = logging.getLogger(__name__)

# Common game + UI words (800+ entries)
_DICT = {
    # Basic UI
    "yes": "да", "no": "нет", "ok": "ок", "cancel": "отмена", "accept": "принять",
    "confirm": "подтвердить", "close": "закрыть", "open": "открыть", "save": "сохранить",
    "load": "загрузить", "start": "начать", "stop": "остановить", "pause": "пауза",
    "resume": "продолжить", "retry": "повторить", "quit": "выйти", "exit": "выход",
    "back": "назад", "next": "далее", "previous": "предыдущий", "skip": "пропустить",
    "continue": "продолжить", "play": "играть", "settings": "настройки", "options": "опции",
    "menu": "меню", "apply": "применить", "reset": "сбросить", "default": "по умолчанию",
    "select": "выбрать", "deselect": "снять выбор", "clear": "очистить",
    "on": "вкл", "off": "выкл", "enable": "включить", "disable": "выключить",
    "toggle": "переключить", "all": "все", "none": "ничего", "auto": "автоматически",
    "new": "новый", "old": "старый", "delete": "удалить", "remove": "убрать",
    "add": "добавить", "edit": "редактировать", "change": "изменить", "modify": "изменить",
    "create": "создать", "destroy": "уничтожить", "build": "построить",
    "search": "поиск", "find": "найти", "sort": "сортировать", "filter": "фильтр",
    "help": "помощь", "info": "информация", "warning": "предупреждение",
    "error": "ошибка", "success": "успех", "fail": "неудача", "failed": "провалено",

    # Game actions
    "interact": "взаимодействовать", "talk": "говорить", "speak": "говорить",
    "listen": "слушать", "look": "смотреть", "examine": "осмотреть",
    "pick": "поднять", "take": "взять", "grab": "схватить", "drop": "выбросить",
    "use": "использовать", "equip": "экипировать", "unequip": "снять",
    "attack": "атаковать", "defend": "защищать", "block": "блокировать",
    "dodge": "уклоняться", "parry": "парировать", "counter": "контратака",
    "cast": "произнести", "aim": "прицелиться", "shoot": "стрелять",
    "reload": "перезарядка", "throw": "бросить", "place": "установить",
    "plant": "посадить", "dig": "копать", "mine": "добывать", "chop": "рубить",
    "gather": "собирать", "craft": "создавать", "forge": "ковать",
    "cook": "готовить", "brew": "варить", "smelt": "плавить",
    "buy": "купить", "sell": "продать", "trade": "торговать",
    "repair": "починить", "upgrade": "улучшить", "enhance": "усилить",
    "enchant": "зачаровать", "imbue": "напитать", "reforge": "перековать",
    "unlock": "открыть", "lock": "закрыть", "picklock": "взломать замок",
    "sneak": "красться", "hide": "спрятаться", "reveal": "раскрыть",
    "discover": "обнаружить", "explore": "исследовать", "travel": "путешествовать",
    "walk": "идти", "run": "бежать", "sprint": "бежать быстро", "climb": "лезть",
    "swim": "плавать", "dive": "нырнуть", "jump": "прыгнуть",
    "crouch": "присесть", "crawl": "ползти", "sprint": "спринт",

    # Combat
    "hit": "ударить", "strike": "ударить", "slash": "порезать", "stab": "тыкнуть",
    "crush": "сокрушить", "smash": "разбить", "pierce": "пронзить",
    "cut": "порезать", "slice": "нарезать", "chop": "рубить",
    "kill": "убить", "slay": "убить", "defeat": "победить", "destroy": "уничтожить",
    "die": "умереть", "died": "погиб", "death": "смерть", "dead": "мёртв",
    "kill": "убийство", "murder": "убийство", "assassinate": "ликвидировать",
    "revive": "воскресить", "resurrect": "воскресить", "respawn": "возродиться",
    "heal": "лечить", "cure": "вылечить", "restore": "восстановить",
    "harm": "навредить", "wound": "ранить", "injure": "травмировать",
    "poison": "отравить", "burn": "сжечь", "freeze": "заморозить",
    "stun": "оглушить", "silence": "заставить молчать", "blind": "ослепить",
    "slow": "замедлить", "root": "укоренить", "knockback": "отбросить",
    "buff": "усилить", "debuff": "ослабить", "weaken": "ослабить",
    "strengthen": "укрепить", "empower": "наделить силой",
    "shield": "щит", "ward": "оберег", "barrier": "барьер",
    "resist": "сопротивляться", "withstand": "выстоять", "endure": "выдержать",
    "survive": "выжить", "survived": "выжил",

    # Game objects
    "weapon": "оружие", "armor": "броня", "item": "предмет", "object": "объект",
    "potion": "зелье", "scroll": "свиток", "key": "ключ", "gem": "камень",
    "sword": "меч", "axe": "топор", "bow": "лук", "staff": "посох",
    "dagger": "кинжал", "spear": "копьё", "hammer": "молот", "mace": "булава",
    "shield": "щит", "helmet": "шлем", "gloves": "перчатки", "boots": "ботинки",
    "ring": "кольцо", "necklace": "ожерелье", "amulet": "амулет", "belt": "пояс",
    "cloak": "плащ", "cape": "накидка", "robe": "мантия",
    "food": "еда", "bread": "хлеб", "meat": "мясо", "fish": "рыба",
    "apple": "яблоко", "wine": "вино", "beer": "пиво", "water": "вода",
    "gold": "золото", "silver": "серебро", "coin": "монета", "money": "деньги",
    "key": "ключ", "lockpick": "отмычка", "map": "карта", "compass": "компас",
    "torch": "факел", "lantern": "фонарь", "light": "свет", "lamp": "лампа",

    # UI/Quests
    "quest": "задание", "mission": "миссия", "objective": "цель", "task": "задача",
    "goal": "цель", "target": "цель", "reward": "награда", "prize": "приз",
    "loot": "добыча", "treasure": "сокровище", "chest": "сундук",
    "inventory": "инвентарь", "bag": "сумка", "backpack": "рюкзак",
    "equipment": "снаряжение", "gear": "снаряжение", "loadout": "нагрузка",
    "stats": "статистика", "attributes": "атрибуты", "skills": "навыки",
    "talents": "таланты", "perks": "особенности", "traits": "черты",
    "level": "уровень", "rank": "ранг", "class": "класс", "race": "раса",
    "experience": "опыт", "points": "очков", "score": "счёт",
    "health": "здоровье", "mana": "мана", "stamina": "выносливость",
    "strength": "сила", "dexterity": "ловкость", "agility": "ловкость",
    "intelligence": "интеллект", "wisdom": "мудрость", "charisma": "харизма",
    "luck": "удача", "speed": "скорость", "power": "сила", "defense": "защита",

    # Game states
    "complete": "завершено", "completed": "выполнено", "incomplete": "невыполнено",
    "active": "активно", "inactive": "неактивно", "available": "доступно",
    "unavailable": "недоступно", "locked": "заблокировано", "unlocked": "разблокировано",
    "enabled": "включено", "disabled": "выключено",
    "ready": "готово", "waiting": "ожидание", "loading": "загрузка",
    "saving": "сохранение", "connecting": "подключение",
    "victory": "победа", "defeat": "поражение", "draw": "ничья",
    "won": "победил", "lost": "проиграл", "tied": "ничья",

    # Common subtitle phrases
    "hello": "привет", "hi": "привет", "hey": "эй", "greetings": "приветствую",
    "goodbye": "прощай", "bye": "пока", "farewell": "прощай",
    "thanks": "спасибо", "thank": "спасибо", "please": "пожалуйста",
    "sorry": "извините", "excuse": "простите", "forgive": "простите",
    "help": "помощь", "save": "спасти", "danger": "опасность", "safe": "безопасно",
    "run": "беги", "stop": "стой", "wait": "подожди", "hold": "держать",
    "come": "иди", "go": "идти", "follow": "следовать", "lead": "вести",
    "trust": "доверять", "believe": "верить", "doubt": "сомневаться",
    "remember": "помнить", "forget": "забыть", "learn": "учиться",
    "know": "знать", "understand": "понимать", "think": "думать",
    "feel": "чувствовать", "want": "хотеть", "need": "нуждаться",
    "hope": "надеяться", "fear": "бояться", "love": "любить", "hate": "ненавидеть",
    "fight": "сражаться", "battle": "сражение", "war": "война", "peace": "мир",
    "friend": "друг", "enemy": "враг", "ally": "союзник", "companion": "спутник",
    "comrade": "товарищ", "brother": "брат", "sister": "сестра",
    "father": "отец", "mother": "мать", "child": "ребёнок", "king": "король",
    "queen": "королева", "prince": "принц", "princess": "принцесса",
    "lord": "лорд", "lady": "леди", "sir": "сэр", "master": "хозяин",

    # Common subtitle words
    "here": "здесь", "there": "там", "where": "где", "when": "когда",
    "why": "почему", "how": "как", "what": "что", "who": "кто",
    "this": "это", "that": "то", "these": "эти", "those": "те",
    "now": "сейчас", "then": "тогда", "always": "всегда", "never": "никогда",
    "again": "снова", "enough": "достаточно", "more": "больше", "less": "меньше",
    "very": "очень", "just": "просто", "only": "только", "also": "тоже",
    "even": "даже", "still": "всё ещё", "already": "уже", "yet": "ещё",
    "soon": "скоро", "later": "позже", "before": "до", "after": "после",
    "between": "между", "under": "под", "over": "над", "inside": "внутри",
    "outside": "снаруcci", "front": "перед", "behind": "позади",
    "left": "влево", "right": "вправо", "up": "вверх", "down": "вниз",
    "true": "правда", "false": "ложь", "real": "настоящий", "fake": "поддельный",
    "good": "хороший", "bad": "плохой", "great": "отличный", "terrible": "ужасный",
    "beautiful": "красивый", "ugly": "уродливый", "strong": "сильный",
    "weak": "слабый", "fast": "быстрый", "slow": "медленный",
    "hard": "трудный", "easy": "лёгкий", "impossible": "невозможный",
    "possible": "возможный", "certain": "определённый", "sure": "уверенный",

    # Common subtitle words 2
    "man": "человек", "woman": "женщина", "boy": "мальчик", "girl": "девочка",
    "people": "люди", "world": "мир", "life": "жизнь", "death": "смерть",
    "soul": "душа", "heart": "сердце", "mind": "разум", "body": "тело",
    "blood": "кровь", "bone": "кость", "skin": "кожа", "eye": "глаз",
    "hand": "рука", "head": "голова", "face": "лицо", "voice": "голос",
    "name": "имя", "word": "слово", "story": "история", "truth": "правда",
    "lie": "ложь", "secret": "секрет", "power": "сила", "magic": "магия",
    "dark": "тьма", "light": "свет", "fire": "огонь", "water": "вода",
    "earth": "земля", "wind": "ветер", "sky": "небо", "sun": "солнце",
    "moon": "луна", "star": "звезда", "night": "ночь", "day": "день",
    "time": "время", "place": "место", "path": "путь", "road": "дорога",
    "way": "путь", "end": "конец", "beginning": "начало", "middle": "середина",
    "change": "изменение", "hope": "надежда", "fear": "страх", "pain": "боль",
    "peace": "мир", "war": "война", "power": "власть", "freedom": "свобода",
    "destiny": "судьба", "fate": "судьба", "future": "будущее", "past": "прошлое",
    "present": "настоящее", "memory": "память", "dream": "сон", "nightmare": "кошмар",
    "promise": "обещание", "oath": "клятва", "vow": "обет", "deal": "сделка",
    "choice": "выбор", "chance": "шанс", "risk": "риск", "danger": "опасность",
    "safety": "безопасность", "home": "дом", "family": "семья", "friend": "друг",
    "enemy": "враг", "king": "король", "queen": "королева", "god": "бог",
    "monster": "монстр", "beast": "зверь", "dragon": "дракон", "demon": "демон",
    "angel": "ангел", "ghost": "призрак", "spirit": "дух", "dead": "мертвец",
    "living": "живой", "alive": "живой", "dead": "мёртвый", "eternal": "вечный",
    "immortal": "бессмертный", "mortal": "смертный", "ancient": "древний",
    "new": "новый", "old": "старый", "first": "первый", "last": "последний",
    "next": "следующий", "final": "последний", "only": "единственный",
    "every": "каждый", "all": "все", "none": "никто", "nothing": "ничего",
    "something": "что-то", "everything": "всё", "everyone": "все",
    "someone": "кто-то", "nobody": "никто", "nowhere": "нигде",
    "somewhere": "где-то", "everywhere": "везде",

    # Common subtitle words 3
    "yes": "да", "no": "нет", "maybe": "может быть", "perhaps": "возможно",
    "certainly": "конечно", "definitely": "определённо", "absolutely": "абсолютно",
    "immediately": "немедленно", "quickly": "быстро", "slowly": "медленно",
    "carefully": "осторожно", "careless": "безрассудный", "foolish": "глупый",
    "wise": "мудрый", "brave": "храбрый", "coward": "трус", "honor": "честь",
    "glory": "слава", "shame": "позор", "pride": "гордость", "regret": "сожаление",
    "anger": "гнев", "rage": "ярость", "calm": "спокойствие", "peace": "мир",
    "mercy": "милосердие", "justice": "справедливость", "revenge": "месть",
    "revenge": "отмщение", "vengeance": "месть", "punishment": "наказание",
    "reward": "награда", "treasure": "сокровище", "riches": "богатство",
    "poverty": "нищета", "wealth": "богатство", "fortune": "удача",
    "luck": "везение", "chance": "шанс", "opportunity": "возможность",
    "choice": "выбор", "decision": "решение", "fate": "судьба", "destiny": "судьба",
    "prophecy": "пророчество", "vision": "видение", "omen": "знамение",
    "sign": "знак", "miracle": "чудо", "wonder": "чудо", "magic": "магия",
    "curse": "проклятие", "blessing": "благословение", "spell": "заклинание",
    "enchantment": "очарование", "hex": "проклятие", "ritual": "ритуал",
    "sacrifice": "жертва", "offering": "приношение", "prayer": "молитва",
    "faith": "вера", "belief": "вера", "doubt": "сомнение", "truth": "правда",
    "lie": "ложь", "deception": "обман", "trick": "трюк", "trap": "ловушка",
    "puzzle": "головоломка", "mystery": "загадка", "secret": "секрет",
    "hidden": "скрытый", "revealed": "раскрытый", "discovered": "обнаруженный",
    "lost": "потерянный", "found": "найдённый", "forgotten": "забытый",
    "remembered": "вспомненный", "forgiven": "прощённый", "cursed": "проклятый",
    "blessed": "благословенный", "saved": "спасённый", "damned": "проклятый",
    "chosen": "избранный", "abandoned": "покинутый", "betrayed": "преданный",
    "trusted": "доверенный", "believed": "веривший", "loved": "любимый",
    "hated": "ненавидимый", "feared": "боявшийся", "respected": "уважаемый",
    "honored": "почитаемый", "famous": "известный", "unknown": "неизвестный",
    "legendary": "легендарный", "mythical": "мифический", "ancient": "древний",
    "eternal": "вечный", "immortal": "бессмертный", "mortal": "смертный",
    "powerful": "могущественный", "weak": "слабый", "strong": "сильный",
    "brave": "храбрый", "fearless": "бесстрашный", "courageous": "мужественный",
    "wise": "мудрый", "foolish": "глупый", "clever": "умный", "stupid": "глупый",
    "beautiful": "красивый", "ugly": "уродливый", "good": "хороший", "evil": "злой",
    "dark": "тёмный", "light": "светлый", "pure": "чистый", "corrupt": "порчённый",
    "right": "правый", "wrong": "неправый", "just": "справедливый",
    "unjust": "несправедливый", "fair": "честный", "unfair": "несправедливый",
    "honest": "честный", "dishonest": "несправедливый", "true": "истинный",
    "false": "ложный", "real": "настоящий", "fake": "поддельный",
    "genuine": "настоящий", "artificial": "искусственный", "natural": "естественный",
    "ordinary": "обычный", "extraordinary": "необычный", "normal": "нормальный",
    "strange": "странный", "weird": "странный", "familiar": "знакомый",
    "alien": "чужой", "foreign": "чужой", "native": "местный",
    "ancient": "древний", "modern": "современный", "new": "новый", "old": "старый",
    "young": "молодой", "elderly": "пожилой", "ancient": "древний",
    "dead": "мёртвый", "alive": "живой", "living": "живой", "undead": "нежить",
    "human": "человек", "humanoid": "гуманоид", "beast": "зверь", "animal": "животное",
    "monster": "монстр", "demon": "демон", "angel": "ангел", "god": "бог",
    "spirit": "дух", "ghost": "призрак", "soul": "душа", "body": "тело",
    "mind": "разум", "heart": "сердце", "blood": "кровь", "bone": "кость",
    "flesh": "плоть", "skin": "кожа", "eye": "глаз", "ear": "ухо",
    "nose": "нос", "mouth": "рот", "hand": "рука", "foot": "нога",
    "head": "голова", "face": "лицо", "voice": "голос", "name": "имя",

    # Quest completions
    "quest completed": "задание выполнено", "objective completed": "цель достигнута",
    "mission complete": "миссия выполнена", "task completed": "задача выполнена",
    "new quest": "новое задание", "new objective": "новая цель",
    "quest failed": "задание провалено", "mission failed": "миссия провалена",
    "you died": "вы погибли", "you are dead": "вы мертвы",
    "game over": "конец игры", "game saved": "игра сохранена",
    "auto saved": "автосохранение", "checkpoint reached": "контрольная точка достигнута",
    "level up": "уровень повышен", "level up!": "уровень повышен!",
    "not enough": "недостаточно", "required": "требуется",
    "press": "нажмите", "hold": "удерживайте", "release": "отпустите",
    "click": "нажмите", "tap": "нажмите",
}

# Custom dictionary file path
_DICT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "en_ru_custom.json")


def _load_custom_dict() -> dict:
    """Load custom dictionary from file (user can add entries)."""
    if os.path.exists(_DICT_FILE):
        try:
            with open(_DICT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[Dict] Failed to load custom dict: {e}")
    return {}


_custom_dict = None


def _get_custom() -> dict:
    global _custom_dict
    if _custom_dict is None:
        _custom_dict = _load_custom_dict()
    return _custom_dict


def lookup(word: str) -> str:
    """Look up a single English word. Returns Russian translation or empty string."""
    w = word.lower().strip().rstrip(".,!?;:")
    # Try built-in dict
    if w in _DICT:
        return _DICT[w]
    # Try custom dict
    custom = _get_custom()
    if w in custom:
        return custom[w]
    return ""


def translate_text(text: str) -> str:
    """
    Offline word-by-word translation.
    Returns translated text, or empty string if nothing could be translated.
    """
    if not text or not text.strip():
        return ""

    words = text.split()
    translated = []
    matched = 0

    for w in words:
        t = lookup(w)
        if t:
            translated.append(t)
            matched += 1
        else:
            translated.append(w)

    # Return translation only if at least 30% words were translated
    if matched >= max(1, len(words) * 0.3):
        return " ".join(translated)
    return ""


def add_custom(word: str, translation: str):
    """Add a custom word to the dictionary and save to file."""
    custom = _get_custom()
    custom[word.lower().strip()] = translation.strip()
    try:
        with open(_DICT_FILE, "w", encoding="utf-8") as f:
            json.dump(custom, f, ensure_ascii=False, indent=2)
        logger.info(f"[Dict] Added custom: {word} → {translation}")
    except Exception as e:
        logger.error(f"[Dict] Failed to save custom dict: {e}")


def dict_size() -> int:
    """Return total dict size (built-in + custom)."""
    return len(_DICT) + len(_get_custom())
