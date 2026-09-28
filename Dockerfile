FROM python:3.11-slim-bookworm

ARG ANDROID_CMDLINE_TOOLS_VERSION=15859902
ARG ANDROID_CMDLINE_TOOLS_SHA256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
ARG GRADLE_VERSION=8.10.2
ARG GRADLE_SHA256=31c55713e40233a8303827ceb42ca48a47267a0ad4bab9177123121e71524c26

ENV DEBIAN_FRONTEND=noninteractive \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_NDK_HOME=/opt/android-sdk/ndk/27.2.12479018 \
    ANDROID_NDK_ROOT=/opt/android-sdk/ndk/27.2.12479018 \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    GRADLE_HOME=/opt/gradle/gradle-8.10.2 \
    GRADLE_USER_HOME=/home/builder/.gradle \
    PATH=/opt/gradle/gradle-8.10.2/bin:/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:/opt/android-sdk/build-tools/35.0.0:$PATH \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip zip git openjdk-17-jdk-headless libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    mkdir -p /opt/gradle; \
    curl --fail --location --silent --show-error "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" -o /tmp/gradle.zip; \
    echo "${GRADLE_SHA256}  /tmp/gradle.zip" | sha256sum --check --status; \
    unzip -q /tmp/gradle.zip -d /opt/gradle; \
    rm /tmp/gradle.zip

RUN set -eu; \
    mkdir -p "${ANDROID_SDK_ROOT}/cmdline-tools"; \
    curl --fail --location --silent --show-error \
      "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_CMDLINE_TOOLS_VERSION}_latest.zip" \
      -o /tmp/android-commandline-tools.zip; \
    echo "${ANDROID_CMDLINE_TOOLS_SHA256}  /tmp/android-commandline-tools.zip" | sha256sum --check --status; \
    unzip -q /tmp/android-commandline-tools.zip -d "${ANDROID_SDK_ROOT}/cmdline-tools"; \
    mv "${ANDROID_SDK_ROOT}/cmdline-tools/cmdline-tools" "${ANDROID_SDK_ROOT}/cmdline-tools/latest"; \
    rm /tmp/android-commandline-tools.zip; \
    yes | sdkmanager --licenses >/dev/null; \
    sdkmanager --install \
      "platform-tools" \
      "platforms;android-35" \
      "build-tools;35.0.0" \
      "ndk;27.2.12479018" \
      "cmake;3.22.1"; \
    chmod -R a+rX "${ANDROID_SDK_ROOT}"

RUN groupadd --system builder && useradd --system --create-home --gid builder --shell /usr/sbin/nologin builder
WORKDIR /opt/apk-builder
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY android-template ./android-template
COPY scripts ./scripts
COPY README.md ./README.md
RUN chmod 0755 android-template/gradlew \
    && mkdir -p /tmp/apk-builder "/home/builder/.gradle" \
    && chown -R builder:builder /opt/apk-builder /tmp/apk-builder "/home/builder/.gradle"

USER builder
CMD ["python", "-m", "app.main"]
