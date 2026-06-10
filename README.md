# CalAI — Telegram Bot для трекинга питания и калорий

Личный Telegram-бот для трекинга еды. Пишешь, что съел — бот записывает и примерно оценивает калории и белок.

## Быстрый старт

### 1. Установка

```bash
cd CalAI
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Создание .env файла

```bash
cp .env.example .env
```

Заполни обязательные переменные:

```ini
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
```

Остальное можно оставить как есть — бот будет работать с mock-провайдером.

### 3. Запуск

```bash
python -m app.main
```

### 4. Тестирование

Открой Telegram, найди своего бота и попробуй:

- `съел курицу с рисом и салатом`
- `на обед гречка, две котлеты и огурцы`
- `перекусил йогуртом с фруктами`
- `/today` — посмотреть итоги за день
- `/help` — подсказки
- Пришли фото еды — бот сохранит его и попросит описать текстом

## Как работает Mock Provider (по умолчанию)

Mock provider — это встроенный rule-based анализатор, которому **не нужен API-ключ**. Он работает так:

1. **Intent detection**: ищет ключевые слова (съел, обед, курица, гречка…) → определяет, что это приём пищи
2. **Food analysis**: сопоставляет текст с базой из ~20 популярных продуктов:
   - Курица ~165 ккал / 100 г, 31 г белка
   - Рис ~130 ккал / 100 г
   - Гречка ~110 ккал / 100 г
   - Котлета ~250 ккал за шт
   - Йогурт ~85 ккал / 100 г
   - Овощи ~25 ккал / 100 г
   - и другие
3. **Диапазоны**: всегда даёт `calories_min — calories_max` (±20% от оценки) вместо точных цифр
4. **Confidence**: если порция указана явно ("150 г") — medium, иначе low
5. **Vision**: не анализирует фото (просит описать текстом)

## Подключение реального LLM (OpenAI-совместимый API)

Чтобы подключить настоящую LLM для более точного анализа, укажи в `.env`:

```ini
LLM_PROVIDER=openai_compatible
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

Бот будет использовать LLM для:
- распознавания интента (в сложных случаях)
- анализа текстовых описаний еды

### Подключение Vision

```ini
VISION_PROVIDER=openai_compatible
VISION_API_KEY=sk-...
VISION_BASE_URL=https://api.openai.com/v1
VISION_MODEL=gpt-4o
```

После этого бот сможет анализировать фото еды.

### Поддерживаемые провайдеры

Подходит любой OpenAI-совместимый API:
- **OpenAI** — `base_url=https://api.openai.com/v1`
- **YandexGPT** — через Yandex Cloud API Gateway
- **GigaChat** — через совместимый прокси
- **OpenRouter** — `base_url=https://openrouter.ai/api/v1`
- **DeepSeek** — `base_url=https://api.deepseek.com/v1`
- **Gemini** — через OpenAI-совместимый слой

Для провайдеров с нестандартным форматом ответа можно адаптировать `app/providers/text_llm.py`.

## Структура проекта

```
CalAI/
├── app/
│   ├── main.py              # Точка входа
│   ├── config.py            # Настройки из .env
│   ├── bot/
│   │   ├── handlers.py      # Обработчики aiogram
│   │   └── keyboards.py     # Клавиатуры (пока пусто)
│   ├── db/
│   │   ├── models.py         # SQLAlchemy модели
│   │   ├── database.py       # Engine + сессии
│   │   └── repositories.py   # CRUD операции
│   ├── services/
│   │   ├── intent.py         # Распознавание интента
│   │   ├── food_analyzer.py  # Анализ еды
│   │   ├── meal_logger.py    # Сохранение приёмов пищи
│   │   └── summary.py        # Итоги дня
│   ├── providers/
│   │   ├── base.py           # Абстрактные классы
│   │   ├── mock.py           # Mock provider (rule-based)
│   │   ├── text_llm.py       # LLM text provider
│   │   └── vision_llm.py     # LLM vision provider
│   ├── schemas/
│   │   ├── food.py           # FoodAnalysis, FoodItem
│   │   └── intent.py         # IntentResult
│   └── utils/
│       ├── time.py           # Временные хелперы
│       └── files.py          # Скачивание фото
├── tests/
│   ├── conftest.py
│   ├── test_intent.py
│   ├── test_food_analyzer.py
│   └── test_meal_logger.py
├── data/
│   ├── photos/               # Фото еды (YYYY-MM-DD/)
│   └── app.db                # SQLite база
├── .env.example
├── requirements.txt
└── README.md
```

## База данных

SQLite, 4 таблицы:

- **users** — пользователи бота
- **meals** — приёмы пищи (meal_type, calories range, confidence, status)
- **meal_items** — отдельные блюда/продукты в приёме пищи
- **raw_messages** — все входящие сообщения

Таблицы создаются автоматически при запуске бота.

## Запуск тестов

```bash
python -m pytest tests/ -v
```

Тесты покрывают:
- распознавание интентов (log_meal, show_today, help, unknown)
- анализ еды по тексту (calories range, protein, meal_type, confidence)
- сохранение в БД
- итоги дня (/today)

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветственное сообщение |
| `/help` | Подсказки и примеры |
| `/today` | Что съедено за сегодня |

## Примеры сообщений для теста

```
съел курицу с рисом и салатом
на обед была гречка, две котлеты и огурцы
это был ужин
сохрани это как перекус
сегодня ел греческий йогурт с фруктами
перекусил яблоком и орехами
на завтрак овсянка с яйцом
что я сегодня ел
```

## Ограничения MVP

- Нет напоминаний и уведомлений
- Нет графиков и статистики
- Нет трекинга воды, шагов, тренировок
- Нет авторизации (привязка по telegram_id)
- Нет миграций (create_all при запуске)
- Mock provider даёт очень приблизительные оценки
