# Compila la app del celular con una versión nueva y la deja lista para que se actualice sola.
#
#   powershell -ExecutionPolicy Bypass -File movil\compilar.ps1
#
# Necesita JAVA_HOME apuntando a un JDK 17. Sin daemon de Gradle: no queda Java ocupando RAM
# ni colgando la consola que lo lanzó. Deja movil\Jarvis.apk y movil\Jarvis.apk.json;
# Jarvis los ofrece al celular (/api/app) desde la carpeta "movil" junto a Jarvis.exe.
# La firma es la clave de depuración de esta PC (%USERPROFILE%\.android\debug.keystore):
# si se pierde, el celular no acepta la actualización y hay que desinstalar la app una vez.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $env:JAVA_HOME) { throw "Define JAVA_HOME con un JDK 17 antes de compilar." }
if (-not $env:JAVA_TOOL_OPTIONS) {
    # Evita «Unable to establish loopback connection» de Gradle en esta PC.
    $env:JAVA_TOOL_OPTIONS = "-Djdk.net.unixdomain.tmpdir=Z:\noexiste"
}

$ahora = [DateTime]::UtcNow
$codigo = [int][Math]::Floor(($ahora - [DateTime]::new(2026, 1, 1, 0, 0, 0, [DateTimeKind]::Utc)).TotalMinutes)
$nombre = "1." + $ahora.ToLocalTime().ToString("yyyyMMdd.HHmm")

cmd /c ".\gradlew.bat assembleDebug --no-daemon --console=plain -q -PjarvisVersionCode=$codigo -PjarvisVersionName=$nombre 2>&1"
if ($LASTEXITCODE -ne 0) { throw "Falló la compilación de la app (código $LASTEXITCODE)." }

Copy-Item "app\build\outputs\apk\debug\app-debug.apk" "Jarvis.apk" -Force
@{ versionCode = $codigo; versionName = $nombre } | ConvertTo-Json | Set-Content "Jarvis.apk.json" -Encoding utf8
Write-Output "App $nombre (versión $codigo) lista en movil\Jarvis.apk"
