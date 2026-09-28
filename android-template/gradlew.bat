@echo off
setlocal
set "GRADLE_VERSION=8.10.2"
set "GRADLE_SHA256=31c55713e40233a8303827ceb42ca48a47267a0ad4bab9177123121e71524c26"
if defined GRADLE_HOME if exist "%GRADLE_HOME%\bin\gradle.bat" (
  call "%GRADLE_HOME%\bin\gradle.bat" %*
  exit /b %ERRORLEVEL%
)
where gradle >nul 2>nul
if not errorlevel 1 (
  call gradle %*
  exit /b %ERRORLEVEL%
)
set "GRADLE_CACHE=%USERPROFILE%\.gradle\wrapper\dists\apk-builder\%GRADLE_VERSION%"
set "GRADLE_BIN=%GRADLE_CACHE%\gradle-%GRADLE_VERSION%\bin\gradle.bat"
if not exist "%GRADLE_BIN%" (
  if not exist "%GRADLE_CACHE%" mkdir "%GRADLE_CACHE%"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $z=Join-Path $env:GRADLE_CACHE 'gradle.zip'; Invoke-WebRequest -UseBasicParsing 'https://services.gradle.org/distributions/gradle-%GRADLE_VERSION%-bin.zip' -OutFile $z; if ((Get-FileHash -Algorithm SHA256 $z).Hash.ToLowerInvariant() -ne $env:GRADLE_SHA256) { Remove-Item -LiteralPath $z -Force; throw 'Gradle checksum verification failed' }; Expand-Archive -LiteralPath $z -DestinationPath $env:GRADLE_CACHE -Force; Remove-Item -LiteralPath $z -Force"
  if errorlevel 1 exit /b 1
)
call "%GRADLE_BIN%" %*
exit /b %ERRORLEVEL%
