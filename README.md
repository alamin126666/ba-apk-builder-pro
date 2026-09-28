# Private HTML → APK Builder

A private FastAPI dashboard that packages a single HTML page or a ZIP web project into an installable Android debug APK. The Android template includes a Kotlin WebView shell and an NDK/CMake C++ JNI loader. Uploads are treated as untrusted data and are never executed as build scripts.

## Requirements

- Docker Desktop for local APK builds, or Docker on a Linux host
- A Railway project connected to this repository for hosted use

The image pins Python 3.11, JDK 17, Android command-line tools, SDK platform 35, build tools 35.0.0, NDK 27.2.12479018, CMake 3.22.1, Gradle 8.10.2, Android Gradle Plugin 8.8.2, and Kotlin 2.0.21. AGP 8.8 requires at least Gradle 8.10.2 and JDK 17, and supports API 35; see the [Android AGP compatibility table](https://developer.android.com/build/releases/about-agp) and [AGP 8.8 release notes](https://developer.android.com/build/releases/agp-8-8-0-release-notes). Android SDK and NDK are installed in the image, not assumed to exist on Railway.

## Local Docker build and run

Set a dashboard password and build the image:

```powershell
$env:ADMIN_PASSWORD = Read-Host "Choose a strong dashboard password"
docker build -t private-apk-builder .
```

Start it on a local port (the process reads `PORT`; `8080` here is only the local example):

```powershell
$env:PORT = "8080"
docker run --rm -p 8080:8080 `
  -e PORT -e ADMIN_PASSWORD `
  -e MAX_UPLOAD_MB=25 -e MAX_CONCURRENT_BUILDS=1 `
  private-apk-builder
```

Open [http://localhost:8080](http://localhost:8080), sign in, upload an `.html` file or `.zip`, enter an app name and package such as `com.example.testwebapp`, optionally choose a PNG/JPG/WEBP icon, then select **Build APK**. The page polls the authenticated status endpoint and offers the verified, debug-signed APK. Android Gradle Plugin creates/uses its standard debug keystore automatically, so no keystore variables are needed. Debug APKs are installable for private testing; use a separately managed release key if you later distribute through an app store.

For running the dashboard directly with Python, use Python 3.11+, install `requirements-dev.txt`, set `PORT` and `ADMIN_PASSWORD`, and run `python -m app.main`. A direct run can build APKs only when the Android SDK/NDK and Gradle are installed and configured as described below. Docker is the reproducible path.

## Environment variables

| Name | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PORT` | Yes | — | Uvicorn binds to `0.0.0.0:$PORT`. Railway provides this value; set it for local runs. |
| `ADMIN_PASSWORD` | Yes | — | Password for the private dashboard. The app refuses to start without it. |
| `MAX_UPLOAD_MB` | No | `25` | Combined upload size limit for project and logo. Allowed range: 1–512. |
| `MAX_CONCURRENT_BUILDS` | No | `1` | Number of Android build workers. Allowed range: 1–8. |
| `BUILD_RETENTION_HOURS` | No | `24` | Retention for APKs, logs, and metadata. Allowed range: 1–720. |
| `SESSION_SECRET` | No | Derived from `ADMIN_PASSWORD` | Optional independent cookie-signing secret. Set a long random value in production if you want password rotation to preserve active sessions. |
| `ANDROID_SDK_ROOT` | No | `/opt/android-sdk` in Docker | Android SDK location. |
| `ANDROID_NDK_HOME` | No | Pinned NDK path in Docker | Android NDK location. |
| `BUILD_ROOT` | No | `/tmp/apk-builder` | Ephemeral build, APK, SQLite history, and private diagnostic storage. |

## Android SDK / NDK configuration outside Docker

The Android template pins compile/target SDK 35, build tools 35.0.0, NDK 27.2.12479018, and CMake 3.22.1. Install those packages with Android `sdkmanager`, accept the licenses, and export `ANDROID_SDK_ROOT`, `ANDROID_HOME`, and `ANDROID_NDK_HOME`. The template's `gradlew` bootstraps the checksum-verified Gradle 8.10.2 distribution when Gradle is not already installed. SDK/NDK installation instructions are in the [Android NDK/CMake guide](https://developer.android.com/studio/projects/install-ndk).

## Railway deployment

1. Push this project to a private Git repository and create a Railway project from it.
2. Railway uses `railway.toml` and the repository `Dockerfile`; its image installs Python, JDK, Android command-line tools, SDK, build tools, NDK, CMake, and Gradle.
3. In Railway **Variables**, set `ADMIN_PASSWORD`. Add `MAX_UPLOAD_MB`, `MAX_CONCURRENT_BUILDS`, `BUILD_RETENTION_HOURS`, and optionally `BUILD_TIMEOUT_SECONDS` as desired. Gradle builds time out after 20 minutes by default. Railway provides `PORT`; do not replace it with a fixed service port.
4. Deploy. The app binds to `0.0.0.0:$PORT`; `/health` is the unauthenticated deployment health check. HTTPS requests receive `Secure`, `HttpOnly`, `SameSite=Strict` session cookies.
5. Open the Railway public domain, sign in with `ADMIN_PASSWORD`, and submit a project from the dashboard.

The app does not require a Railway volume or an external storage service. Build workspaces, temporary uploads, result APKs, server-side logs, and a small SQLite history database live on ephemeral storage and are cleaned after `BUILD_RETENTION_HOURS`. Build history therefore lasts for the current container's writable storage lifetime; a redeploy or container replacement can remove older APKs/history. A persistent metadata or S3-compatible artifact adapter can be added later without changing the build API.

## Upload rules

- Upload one `.html`/`.htm` document, or a `.zip` containing a root `index.html` (also accepts `index.htm`) or one top-level folder with that entry point.
- ZIP paths are checked before extraction. Absolute paths, `..`, backslashes, symlinks, encrypted ZIPs, duplicate/case-colliding paths, excessive expansion, and unsupported file extensions are rejected.
- Supported web files include HTML, CSS, JavaScript, JSON, SVG, common raster images, fonts, WebAssembly, audio/video, and PDF. Gradle files, native source, shell scripts, executables, and arbitrary file types are not copied into the Android project.
- Project structure below the web root is preserved. A single HTML file is packaged as `index.html`.
- App names are limited to 50 printable characters and XML-escaped. Package names use lowercase Android-style identifier segments, e.g. `com.bd.alamin.webapp`; reserved Java/Android namespaces are rejected.
- The logo is optional. PNG, JPEG, and WEBP data is inspected by Pillow (not trusted by filename), cropped to launcher sizes, and used for density-specific and adaptive icons.

## Protected web assets

The build creates a single `assets/app.dat` WPK1 package. Its binary header stores a version marker, count, resource paths, nonces, and AES-GCM ciphertext. Each HTML/CSS/JavaScript/image/font/resource file is independently encrypted with a random 256-bit per-build key and its relative path as authenticated additional data. No uploaded file is copied as a plaintext APK asset. The native library parses the package, and C++ calls Android's AES/GCM provider through JNI to authenticate and decrypt each requested file. The WebView receives a byte stream directly from native memory at an HTTPS-style local base URL so relative paths work; decrypted web files are not written to external or internal storage.

This protects assets against casual inspection and ensures tampering is detected. The package key must be available to the app at runtime and is compiled into that build's native library, so a determined reverse engineer who controls the APK/device can recover the key or observe plaintext in memory. It is not DRM or a guarantee against reverse engineering. Resource path names remain visible in the encrypted package metadata.

The WebView enables JavaScript for normal web apps, disables file/content access and mixed content, serves local resources only through the protected loader, and blocks top-level navigation away from its local origin. Remote subresources/fetches can use the `INTERNET` permission; HTTPS is required for remote content.

## Build process and endpoints

Each request gets a UUID workspace. The queue has a bounded waiting capacity and runs at most `MAX_CONCURRENT_BUILDS` Gradle builds at once. The worker validates uploads and app metadata, encrypts the resources, copies/configures the trusted Android template, generates icons, invokes the pinned Gradle wrapper to build `assembleDebug`, checks the APK's protected asset/native library, verifies its Android debug signature with `apksigner`, and checks the package name. Source and intermediate Android workspaces are removed on success or failure; only the APK, bounded diagnostics, and metadata remain until retention cleanup.

All dashboard, history, upload, status, cancellation, and download operations require the signed session cookie. Mutating browser requests also require the session-bound CSRF header. Downloads use generated UUID identifiers rather than client-supplied paths.

- `GET /health`
- `GET /login`, `POST /login`, `POST /logout`, `GET /dashboard`
- `POST /api/build`
- `GET /api/builds`
- `GET /api/build/{id}/status`
- `POST /api/build/{id}/cancel`
- `GET /api/build/{id}/download`

## Tests

Install the test dependencies and run:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The unit/security suite covers authentication and CSRF, app/package validation, upload limits, ZIP Slip and symlink rejection, AES-GCM container integrity, history/status authorization, download authorization, and unique workspaces. A real Android build test is opt-in because it requires a working SDK/NDK installation:

```powershell
$env:RUN_ANDROID_INTEGRATION = "1"
python -m pytest -q -m android_integration
```

That integration test submits a real HTML project through the authenticated API, waits for the debug APK, and inspects its archive for `assets/app.dat`, the native loader, and absence of plaintext HTML/CSS/JS. Install the resulting debug APK on a supported Android device to validate device-specific behavior.

## Troubleshooting

- **Debug APK signature verification fails:** Gradle normally creates and signs with its debug keystore automatically. Check the private build log for Android SDK/build-tools errors; no release credentials are required.
- **`SDK location not found` or NDK/CMake missing:** build and run the provided Docker image, or set the Android paths and install the pinned SDK, NDK, and CMake versions.
- **Gradle dependency download or plugin resolution fails:** the first build downloads pinned Gradle/AGP/Kotlin artifacts from their official repositories; verify the container has outbound HTTPS access and retry after a transient outage.
- **Gradle daemon disappears or appears stuck:** Gradle and CMake are limited to one worker and a 768 MB Java heap to reduce Railway memory pressure. Gradle status polling reconnects after temporary network errors, active builds resume after dashboard refresh, and a build is stopped after `BUILD_TIMEOUT_SECONDS` (20 minutes by default). Railway logs emit a safe heartbeat with the last Gradle task each minute. If it times out or the daemon exits, increase the service's available memory and verify outbound dependency access; the dashboard shows a sanitized failure summary.
- **ZIP rejected:** keep `index.html` at the archive root or in a single top-level folder; remove symlinks, scripts, executable files, unsupported extensions, duplicate names, and excessive compression ratios.
- **APK installs but a local resource is missing:** use a relative URL with correct case, include that resource inside the uploaded web root, and check the file extension is on the allowed list. Local resource paths are case-sensitive on Android.
- **Build queue full:** wait for an active build or lower upload size; at most 20 jobs wait in the queue in addition to active worker(s).

## Project layout

```text
app/                    FastAPI application, auth, build queue, templates, static UI
android-template/       Trusted Kotlin/WebView and C++/JNI Android project
scripts/                Web package encryption and Android project preparation
tests/                  Unit/security and opt-in debug Android integration tests
Dockerfile              Pinned Python/JDK/Android SDK/NDK/Gradle environment
railway.toml            Railway Docker deployment and health check
```
