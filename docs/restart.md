# Перезапуск бота

## Быстрый перезапуск

```bash
cd /root/medtech
pkill -f "python run.py"; sleep 2
source venv/bin/activate && nohup python run.py > /tmp/medbot.log 2>&1 &
```

## Проверить что работает

```bash
# Процесс запущен?
pgrep -af "python run.py"

# Логи (последние 20 строк)
tail -20 /tmp/medbot.log

# Логи в реальном времени
tail -f /tmp/medbot.log
```

## Остановить

```bash
pkill -f "python run.py"
```

## Перезапуск с очисткой кэша

После изменений в коде:

```bash
pkill -f "python run.py"; sleep 2
find /root/medtech -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
source venv/bin/activate && nohup python run.py > /tmp/medbot.log 2>&1 &
tail -5 /tmp/medbot.log
```

## Проверить что ARI подключился

В логах должно быть:

```
INFO: MedBot starting...
INFO: ARI: http://127.0.0.1:8088 (app=medbot)
INFO: Connecting to ARI WebSocket...
INFO: Connected to ARI WebSocket
```

Если `Connected` нет — Asterisk не запущен или ARI модуль не загружен.
