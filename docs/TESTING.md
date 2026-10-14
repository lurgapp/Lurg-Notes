# Тестирование

## Android

Нужны JDK 17 и Android SDK 36. Из `android/`:

```bash
./gradlew :app:assembleDebug :app:testDebugUnitTest :app:assembleDebugAndroidTest
```

JVM-тесты проверяют Markdown, состояние редактора, E2EE, синхронизацию и обновления. Instrumented tests требуют эмулятор или устройство и отдельные локальные сервисы; подробности — в [Android README](../android/README.md). Проверка подписи внешнего production APK требует `LURG_UPDATE_TEST_APK`.

## Server

Из `server/`, после установки `requirements-dev.txt` в виртуальное окружение:

```bash
env -u LURG_TEST_DATABASE_URL .venv/bin/python -m pytest -q
```

Обычные тесты используют синтетические настройки и mock-сервисы. PostgreSQL integration tests включаются только через `LURG_TEST_DATABASE_URL`. Fixture принимает localhost-базу с именем `lurgtest` и создаёт временные схемы. Для неё требуется pgvector. Подробнее — в [Server README](../server/README.md).

## LCP

Из `lcp/`, после установки `.[dev]`:

```bash
.venv/bin/pytest -q
# Полный lifespan/WebSocket-тест требует отдельный локальный Redis:
LCP_TEST_REDIS_URL=redis://127.0.0.1:16479/0 .venv/bin/pytest -q
```

## Website

Из `website/`:

```bash
npm ci
NEXT_TELEMETRY_DISABLED=1 npm run build
```

## Проверенный снимок

Для версии 0.5.4 проверки выполнены 14 октября 2026 года:

| Компонент | Результат |
| --- | --- |
| Android | Debug APK и instrumented test APK собраны; JVM: 40 passed, 1 skipped. |
| Server | 227 passed, 22 PostgreSQL integration tests skipped. |
| LCP | 48 passed, 1 native Redis test skipped. |
| Website | Production build, TypeScript и статическая генерация страницы прошли. |
| Alembic | 27 связанных ревизий, единственный head `20261008_0027`. |
| Compose | YAML, build contexts, пути и настройки проверены статически. |

Этот прогон не подтверждает запуск Compose, применение миграций в настоящей PostgreSQL, доставку email/FCM, реальные AI-провайдеры и полную HTTP/WebSocket-связку. Android-тесты на устройстве и release-подпись в этом прогоне не проверялись.
