# Проверка публичной копии

Проверки выполнены 14 октября 2026 года непосредственно из `lurg-notes-public/`. Использованы уже установленные локальные зависимости; сами зависимости и результаты сборок из экспорта удалены.

| Часть | Проверка | Результат |
| --- | --- | --- |
| Android | Gradle offline: `:app:assembleDebug :app:testDebugUnitTest :app:assembleDebugAndroidTest`, JDK 17 / SDK 36 | Сборка успешна, 79 Gradle tasks. JVM: 40 passed, 1 skipped. APK приложения и instrumented tests скомпилированы. |
| Server | `python -m pytest -q`, синтетические DATABASE_URL/JWT_SECRET, без LURG_TEST_DATABASE_URL | 227 passed, 22 skipped. |
| Server dependencies | Проверка локальных imports и загрузка scoped OpenAPI | 40 app-модулей, отсутствующих импортируемых модулей нет. API Notes, Identity, Graph и App зарегистрированы. |
| Alembic | Проверка всей цепочки и `alembic heads` | 27 ревизий, единственный head `20261008_0027`. |
| LCP | `python -m pytest -q` | 48 passed, 1 skipped. |
| Website | `NEXT_TELEMETRY_DISABLED=1 npm run build`, Next.js 15.5.20 | Production build, TypeScript и статическая генерация страницы прошли. |
| Compose | YAML, пути build context/schema.sql, настройки env и привязка опубликованных портов | Статическая проверка прошла. |

Один Android-тест требует внешний production-signed APK fixture для проверки подписи. Пропущенные server-тесты требуют явно указанную отдельную PostgreSQL-базу `lurgtest` на localhost. Пропущенный LCP-тест требует отдельный native Redis.

Полный серверный прогон сначала столкнулся с ограничением sandbox при завершении mocked DNS/thread executor; повторный прогон с явными синтетическими настройками и отключённым PostgreSQL integration target успешно завершился. Ни рабочая БД, ни действующие API-аккаунты для этих проверок не использовались.

Docker здесь не установлен, поэтому свежая база, выполнение миграций в PostgreSQL, запуск Compose, доставка email/FCM, реальные AI-провайдеры и полная HTTP/WebSocket-связка этим прогоном не подтверждены. Android-инструментальные тесты на устройстве и release-подпись не запускались. Актуальная версия приложения собирается из `android/`.

Контрольные суммы каждого файла и его происхождение фиксируются в `../SOURCE_MANIFEST.json`. Проверка перед публикацией: `python3 scripts/check-public.py`; при наличии исходного рабочего дерева: `python3 scripts/check-public.py --verify-sources --source-root ..`.
