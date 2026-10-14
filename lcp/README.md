# LCP Gateway

Общий шлюз транспорта Lurg, используемый Android-клиентом Notes для уведомлений об изменениях. Исходники протокола, сессий, проверки авторизации, Redis Streams, повторной доставки и тесты сохранены из проекта `lcp/`.

Документы передаются через HTTP API сервера Notes. Событие `notes.events` сообщает клиенту, что нужно запросить изменения; расшифрованный текст документа через шлюз не передаётся.

Для отдельного запуска нужны Python 3.12+, Redis и сервер из `../server/`:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
# Настройте REDIS_URL, AUTH_JWT_SECRET и LCP_INTERNAL_TOKEN.
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001
```

`AUTH_JWT_SECRET` и `LCP_INTERNAL_TOKEN` должны совпадать с соответствующими секретами сервера; `AUTH_SESSION_STATUS_URL` указывает на его `/v1/auth/internal/session-status`. Для контейнерного запуска используется `../compose.yml`, без внешних сетей рабочего сервера.

```bash
.venv/bin/pytest -q
# Для интеграционных тестов можно задать отдельный локальный Redis:
LCP_TEST_REDIS_URL=redis://127.0.0.1:16479/0 .venv/bin/pytest -q
```

Проверки: `/health/live`, `/health/ready`, `/metrics`. WebSocket: `/connect`. В дереве также сохранены общие адаптеры Nearby/Core/Graph. Nearby в этот репозиторий не входит; его RPC требует отдельно развёрнутый сервис.

Шлюз проверяет текущую серверную сессию перед приватной доставкой и возобновлением соединения. Изменения Notes публикуются серверным transactional outbox в Redis Stream; события привязаны к владельцу и содержат стабильный идентификатор для дедупликации. Клиент повторно подключается с session/resume token и подтверждает последовательности ACK. Хранение очереди и replay ограничено настройками Redis и временем жизни сессии.
