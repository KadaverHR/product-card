# Product Card — генератор карточек 3sta.ru

Python-проект для создания фирменных карточек шин размером 1200×1600. Поддерживает светлую и тёмную темы, загрузку товаров из YML-фида и HTTP API. Использует настоящее фото товара, удаляет белый фон, выравнивает тон и повышает резкость внутри силуэта. Тексты и логотип накладываются программно; внешний сервис генерации изображений не нужен.

## Установка

Python 3.10+, Windows или Linux. После клонирования откройте терминал в папке проекта:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

На Linux активируйте окружение командой `source .venv/bin/activate` и установите DejaVu Sans: `sudo apt-get install fonts-dejavu-core`. На Windows используется Arial. Фирменные фоны и логотипы находятся в `assets/` и включены в репозиторий.

## Генерация из фида

```powershell
# Один товар, обе темы
python feed_card.py --id S207352
# Первые 10 товаров, обе темы
python feed_card.py --limit 10 --theme all
```

Фид по умолчанию: `https://3sta.ru/yandex/feed.xml`. Он читается потоком до выбранного товара или лимита. Полная обработка включается явно: `--limit 0`.

Результаты: `feed-output/dark/<артикул>.png` и `feed-output/light/<артикул>.png`. Исходники и характеристики сохраняются в `feed-output-sources/`. Эти папки исключены из Git.

Подробности: [генерация из фида и оформление](README-feed.md).

## HTTP API

```powershell
Copy-Item .env.example .env
# Укажите собственный CARD_API_KEY в .env
python -m uvicorn api:app --env-file .env --host 0.0.0.0 --port 8000
```

Файл `.env` загружается при запуске с `--env-file .env` и не включается в Git. Переменные, уже заданные в окружении, имеют приоритет. В шаблоне указан публичный адрес `https://tools.3sta.ru`; для локальной проверки задайте `CARD_PUBLIC_BASE_URL=http://localhost:8000` в своём `.env`.

| Запрос | Назначение |
| --- | --- |
| `GET /api/v1/cards?theme=dark` | Все готовые карточки темы |
| `GET /api/v1/cards/S207352?theme=light` | Карточка артикула или 404 |
| `POST /api/v1/cards/generate` | Генерация из JSON и возврат ссылки |

API и генератор фида используют общую папку карточек. Запросы API при настроенном ключе требуют заголовок `X-API-Key`; изображения по возвращаемым ссылкам открываются без ключа.

Локальная интерактивная документация: `http://localhost:8000/docs`. На сервере с настроенным доменом: `https://tools.3sta.ru/docs`. Сам запуск Uvicorn не настраивает домен или HTTPS. Для публикации нужен сервер и обратный прокси; публикуйте `/api` и `/images`, а для документации также `/docs`, `/redoc`, `/openapi.json`.

Подробные поля, ответы, ошибки и примеры: [документация API](README-api.md).

## Проверка

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest test_api -v
```

Тесты не скачивают фид и не создают реальные карточки. В GitHub Actions настроена проверка на Windows и Linux с Python 3.10 и 3.12. Она запускается после push и в pull request. Используемые действия: [checkout](https://github.com/actions/checkout) и [setup-python](https://github.com/actions/setup-python).

## Файлы проекта

| Файл | Назначение |
| --- | --- |
| `api.py` | HTTP API и публикация изображений |
| `feed_card.py` | Генерация товаров из YML/XML |
| `banner_card.py` | Основной макет двух тем |
| `build.py` | Вырезание товара и обработка фото |
| `branded.py` | Шрифты, текст и дополнительный макет |
| `assets/` | Логотипы, фоны и демонстрационное фото |
| `test_api.py` | Тесты API |
| `.env.example` | Пример настроек без настоящих ключей |

`README-v4.md` и `README-v7.md` описывают ранние версии оформления. Актуальные инструкции — в этом README, `README-feed.md` и `README-api.md`.

## Загрузка в GitHub

Создайте пустой репозиторий GitHub без автоматически добавленных README и лицензии. В папке проекта:

```powershell
git add .
git diff --cached --stat
git commit -m "Initial product card generator and API"
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
```

Замените `YOUR_ACCOUNT` и `YOUR_REPOSITORY` своими значениями. `.gitignore` исключает ключи, окружения, скачанные фото, результаты генерации и временные файлы. При использовании нестандартной папки вывода добавьте её в `.gitignore` перед коммитом. Фирменные ресурсы в `assets/` сохраняются в репозитории, поскольку нужны для сборки карточек. Лицензия на исходный код и фирменные ресурсы пока не задана.
