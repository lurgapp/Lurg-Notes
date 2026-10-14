# Lurg Notes

Lurg Notes - нативный Android-редактор экосистемы Lurg. Он использует единый Lurg ID, Markdown как канонический формат документа, локальное редактирование и явные интеграции с AI и Lurg Graph.

## Product Boundaries

- Notes не создаёт отдельные аккаунты: владельцем заметки является текущий Lurg ID.
- Редактор работает локально и не требует сети для ввода, Markdown, таблиц и чек-листов.
- AI является инструментом редактора, а не отдельным чатом.
- Graph никогда не меняется автоматически.
- Tasks в текущей версии являются checklist blocks внутри заметки.

## Editor

Документ хранится в Markdown и отображается как visual live-editor. Блоки включают текст, H1-H3, жирный, курсив, зачёркивание, inline/code blocks, цитаты, списки, чек-листы, сворачиваемые блоки, таблицы, разделители и media references.

Списки - структурные блоки. Enter создаёт следующий элемент того же типа; пустой list item завершает список. Нумерация нормализуется при сериализации. Широкие таблицы скроллятся только внутри таблицы и сохраняются как семантический Markdown, не как изображение.

## AI Runtime

Notes использует общий `AIEngine` и provider infrastructure Lurg, но имеет собственный service namespace `lurg_notes`, action registry, prompts, usage events и context policy.

Каждое действие имеет явный тип: `rewrite`, `shorten`, `expand`, `fix_grammar`, `make_table`, `make_checklist`, `structure_markdown` и другие editor actions. Notes не передаёт system prompt через public API. Preview использует тот же Markdown renderer, что и сохранённый документ; AI diff не применяется без Apply.

## Lurg Graph

Действие `Извлечь факты для Lurg Graph` запускается только вручную. Перед анализом Notes показывает отдельное согласие на отправку текста в Lurg AI. Автосохранение, открытие заметки и background sync никогда не создают Graph facts.

1. Notes очищает presentation-only Markdown: code blocks, цитаты и таблицы не считаются личными фактами.
2. Backend создаёт один `graph_change_candidate` на атомарный факт.
3. Пользователь видит текст, тип, confidence и исходный фрагмент каждого candidate.
4. Пользователь выбирает отдельные facts.
5. Подтверждённые candidates проходят существующий Graph commit-layer.

Graph использует общий canonical entity resolution, deduplication, versioning и lifecycle. Provenance для Notes содержит `source_type=lurg_notes`, `source_id=note_id`, note title, document revision, content hash, excerpt и `confirmed=true`.

## Encryption And Sync

Cloud sync uses E2EE v2: AES-256-GCM document envelopes, an owner-specific user master key, revision-checked document uploads and trusted-device credentials. Device keys use Android Keystore; other devices receive encrypted key envelopes after approval. Recovery is optional and uses a user-held secret, never an access token. The protocol and recovery boundaries are documented in the [E2EE v2 protocol](lurg-notes-e2ee-v2.md).

Backend scopes every encrypted document to the authenticated Lurg ID and uses compare-and-swap revisions, commit-ordered cursors, paginated deltas and tombstones. Tokens, keys, Room queries, background jobs and responses are checked against their account owner. Logout preserves the device's E2EE keys; switching accounts does not expose another owner's notes.

Editing and previews use local Room data. Foreground/resume and connectivity restoration trigger sync; an open app polls every 30 seconds. WorkManager provides a 15-minute background fallback, which Android may defer. Document transfer uses HTTP. While the app is in the foreground, its LCP connection subscribes to owner-scoped `notes.events` and triggers an immediate HTTP pull when an invalidation arrives; polling and WorkManager remain fallbacks.

Cloud text envelopes are encrypted, but local Room and photos do not have an additional application encryption layer. Photos currently remain local. AI receives only explicitly selected text after consent; Graph facts are stored in plaintext on the server. Soft deletion and backup copies do not imply immediate physical erasure.

## Push Notifications

Android uses Firebase Cloud Messaging after the user grants notification permission. The native app registers an owner-scoped FCM token through the shared device-token contract. When a Notes release is published, the protected backend sender notifies only active Android devices that run an older version and have `notifications_enabled` enabled. Firebase service-account credentials are mounted only as a production secret and never enter the repository, APK, Docker image or logs.

New release publications use a durable app-scoped queue with bounded retries and exact-release deduplication. The client obtains update metadata from its fixed HTTPS manifest and verifies the APK's package, version, size, SHA256 and actual signing certificate. Notification payloads never select an arbitrary download URL. The first upgrade from clients without the native updater requires their existing APK-install flow.

## API

- `/notes/e2ee/devices`, `/notes/e2ee/key-envelope` and `/notes/e2ee/recovery` manage E2EE v2 trusted devices, recovery and key envelopes.
- `PUT /notes/e2ee/documents/{note_id}` uploads a revision-checked AES-GCM envelope; `GET /notes/e2ee/sync` returns paginated metadata deltas.
- Document and sync endpoints require the E2EE v2 device credential; legacy mutations cannot bypass that check. See the protocol document for the exact request contract.
- `POST /notes/{note_id}/graph-candidates` creates reviewable candidates and never commits Graph.
- `POST /graph/changes/{candidate_id}/confirm` commits an explicitly selected candidate.
- `POST /graph/changes/{candidate_id}/reject` rejects it.

## Native Stack

Lurg Notes uses Kotlin, Jetpack Compose for application UI, Room local store, DataStore-compatible settings, WorkManager sync and Android Keystore. The editor and sync do not depend on React Native, Hermes, Metro or a JavaScript bundle.
