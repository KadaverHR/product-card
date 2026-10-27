# Команды сервера — product-card

Все команды выполняются в SSH-терминале сервера. Проект: `/opt/product-card`.

## 1. Подключиться с компьютера

```bash
ssh root@193.107.236.2
```

## 2. Обновить проект после push в GitHub

```bash
sudo -H -u productcard git -C /opt/product-card pull --ff-only
```

При ошибке обновления остановитесь. Обновление не меняет уже работающую задачу генерации: её нужно остановить и запустить заново.

## 3. Запустить только API

```bash
sudo systemctl enable --now product-card
```

После обновления кода — перезапустить API:

```bash
sudo systemctl restart product-card
sudo systemctl status product-card --no-pager
curl -sS --max-time 10 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/docs
```

Ожидаемый ответ проверки: `200`. Генерация фида от этих команд не запускается.

## 4. Остановить пакетные задачи перед новым запуском

```bash
sudo systemctl stop product-card-feed.service product-card-feed-all.service product-card-feed-light.service product-card-feed-dark.service product-card-compress.service
sudo systemctl reset-failed product-card-feed.service product-card-feed-all.service product-card-feed-light.service product-card-feed-dark.service product-card-compress.service
```

`not loaded` / `not found` означает, что соответствующей задачи нет — можно продолжать. API этими командами не останавливается.

## 5. Генерировать весь фид

Сначала выполните пункт 4. В первой строке выберите `dark`, `light` или `all`.

```bash
CARD_FEED_THEME=dark
sudo systemd-run --unit="product-card-feed-$CARD_FEED_THEME" \
  --uid=productcard --gid=productcard \
  --working-directory=/opt/product-card \
  --property="StandardOutput=append:/opt/product-card/feed-$CARD_FEED_THEME.log" \
  --property="StandardError=append:/opt/product-card/feed-$CARD_FEED_THEME.log" \
  /opt/product-card/.venv/bin/python -u /opt/product-card/feed_card.py \
  --limit 0 --theme "$CARD_FEED_THEME" --feed-attempts 5 --timeout 60 \
  --jpeg-quality 82 --max-edge 1200 --jpeg-subsampling 2 \
  --output /opt/product-card/feed-output
```

Результат: JPG 900×1200. Выбранные темы перезаписываются данными фида, включая ручные изменения. Проверьте, что в фиде исходные фото, а не уже оформленные карточки.

Лог и статус (в том же терминале):

```bash
sudo tail -n 50 -f "/opt/product-card/feed-$CARD_FEED_THEME.log"
sudo systemctl status "product-card-feed-$CARD_FEED_THEME" --no-pager
```

Для статуса сначала выйдите из просмотра лога через `Ctrl+C`. Из другого терминала: логи `feed-dark.log`, `feed-light.log`, `feed-all.log`; задачи `product-card-feed-dark`, `product-card-feed-light`, `product-card-feed-all`.

## 6. Сжать существующие JPG без повторной генерации

Сначала выполните пункт 4. Не запускайте генерацию этих же карточек через API во время сжатия.

Проверка первых 100 файлов без изменений:

```bash
sudo -H -u productcard /opt/product-card/.venv/bin/python -u /opt/product-card/compress_cards.py \
  --output /opt/product-card/feed-output --theme all \
  --quality 82 --max-edge 1200 --subsampling 2 --limit 100 --dry-run
```

Запуск сжатия всех JPG:

```bash
sudo systemd-run --unit=product-card-compress \
  --uid=productcard --gid=productcard \
  --working-directory=/opt/product-card \
  --property=StandardOutput=append:/opt/product-card/compression.log \
  --property=StandardError=append:/opt/product-card/compression.log \
  /opt/product-card/.venv/bin/python -u /opt/product-card/compress_cards.py \
  --output /opt/product-card/feed-output --theme all \
  --quality 82 --max-edge 1200 --subsampling 2
```

Сжатие заменяет JPG более компактной версией до 900×1200, без резервной копии. PNG и исходники не удаляет. Повторный запуск с теми же настройками пропускает уже обработанные неизменённые файлы. Служебную `.card-compression.sqlite3` не удаляйте.

Лог и статус:

```bash
sudo tail -n 50 -f /opt/product-card/compression.log
sudo systemctl status product-card-compress --no-pager
```

## 7. Проверить место и процессы

```bash
df -h /
free -h
du -sh /opt/product-card/feed-output /opt/product-card/feed-output-sources
pgrep -af '[f]eed_card.py|[c]ompress_cards.py|[u]vicorn'
```

`Ctrl+C` закрывает просмотр лога, но не останавливает задачу. Задачи `systemd-run` работают после закрытия SSH. Генерацию и сжатие одновременно не запускайте. Грузовые карточки доступны через API (`product_type: "truck"`, `theme: "dark"`) и общий редактор `/cards/viewer`. Команды фида пока генерируют только легковые карточки.
