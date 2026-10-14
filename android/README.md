# Lurg Notes Android

The current app is a native Kotlin / Jetpack Compose application. This directory
contains its source and is a standalone Gradle project.

## Build and test

Install JDK 17 and Android SDK Platform 36 with Build Tools 36.0.0. Configure the
SDK in Android Studio, set `ANDROID_HOME`, or create a local `local.properties`
containing `sdk.dir=/your/path/to/Android/Sdk`.

From this directory:

```sh
./gradlew :app:assembleDebug :app:testDebugUnitTest
```

On Windows use `gradlew.bat`. The debug APK is generated under
`app/build/outputs/apk/debug/`. Android tooling creates its own local debug
signing key; no signing key is included in this repository.

Firebase push notifications are optional for a debug build. To enable them,
download your own Firebase project's Android configuration as
`app/google-services.json` for the `pro.lurg.notes` package. The checked-in
`.example` file only illustrates the shape and contains placeholders.
Without a Firebase configuration, builds work and push registration is skipped
when Firebase is unavailable.

Debug builds use the public Lurg API and Auth endpoints by default. Set these
environment variables when building to use your own services:

```sh
LURG_DEBUG_API_URL=https://api.example.com \
LURG_DEBUG_AUTH_URL=https://auth.example.com/api/v1/auth \
LURG_DEBUG_LCP_WS_URL=wss://lcp.example.com/connect \
./gradlew :app:assembleDebug
```

`LURG_DEBUG_LCP_WS_URL` defaults to an empty string, disabling the WebSocket
connection in debug builds. Instrumented UI tests can be compiled with
`./gradlew :app:assembleDebugAndroidTest`. Running them requires an emulator or
device and the local test services described by their source; several tests
check that the API uses `127.0.0.1` before touching any account data. Use
`-PnotesUiTestPackage` for the isolated debug application ID
`pro.lurg.notes.uitest`.

## Release signing

Release tasks require your own signing credentials. Set
`LURG_ANDROID_KEYSTORE_PATH`, `LURG_ANDROID_KEYSTORE_PASSWORD`,
`LURG_ANDROID_KEY_ALIAS`, and `LURG_ANDROID_KEY_PASSWORD`, or put these keys in a
properties file outside the repository and set
`LURG_ANDROID_SIGNING_PROPERTIES` to its path. The default private properties
location is `~/.lurg/android-release/signing.properties`.

```sh
LURG_ANDROID_SIGNING_PROPERTIES=/private/path/signing.properties \
./gradlew :app:assembleRelease
```

For an independent distribution, configure your own application ID, production
endpoints, Firebase app, and update host / signing certificate pin in
`LurgNotesApplication.kt`. An app signed with your own certificate cannot
install official Lurg updates signed by a different key.

## Source layout

- `app/src/main/`: application, editor, Room storage, encryption, synchronization,
  API clients, graph UI, push notifications, and APK update verification.
- `app/src/test/`: local JVM tests.
- `app/src/androidTest/`: instrumented tests.

Do not commit local SDK configuration, Firebase configuration, signing keys,
signing properties, build caches, or generated APKs / app bundles.
