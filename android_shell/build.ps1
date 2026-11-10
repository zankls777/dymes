# BOM3 PDA shell - one-click build (no Android Studio / Gradle required)
# Requires: JDK with javac/keytool + Android SDK (platform-34 + build-tools 35.0.0)
# Usage: powershell -ExecutionPolicy Bypass -File build.ps1
#
# Change app name  -> res/values/strings.xml  (app_name / splash_subtitle)
# Change app logo  -> assets/logo.png         (square or any ratio, auto padded)
# Change splash art-> assets/splash.png       (optional, falls back to logo.png)

$ErrorActionPreference = 'Stop'

$root       = Split-Path -Parent $MyInvocation.MyCommand.Path
$sdk        = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $env:LOCALAPPDATA 'Android\sdk' }
$buildTools = Join-Path $sdk 'build-tools\35.0.0'
$androidJar = Join-Path $sdk 'platforms\android-34\android.jar'
$versionCode = 4
$versionName = '1.1.2'

if (-not $env:JAVA_HOME) { $env:JAVA_HOME = 'C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot' }
if (-not (Test-Path (Join-Path $env:JAVA_HOME 'bin\javac.exe'))) { throw 'javac not found, set JAVA_HOME' }
if (-not (Test-Path $androidJar)) { throw "missing $androidJar, install platforms;android-34" }
if (-not (Test-Path (Join-Path $buildTools 'aapt2.exe'))) { throw 'missing build-tools 35.0.0' }

$src      = Join-Path $root 'app\src\main'
$res      = Join-Path $src 'res'
$assets   = Join-Path $root 'assets'
$out      = Join-Path $root 'build'
$dist     = Join-Path $root 'dist'
$keystore = Join-Path $root 'bom3.keystore'
$ksPass   = 'bom3shell'
$apkName  = "BOM3-PDA-$versionName.apk"

if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Force -Path $out, "$out\gen", "$out\classes", "$out\dex", $dist | Out-Null

# ---------- generate launcher icons + splash image from assets ----------
Write-Host '[0/6] generate icons and splash'
Add-Type -AssemblyName System.Drawing

$logoFile = Join-Path $assets 'logo.png'
if (-not (Test-Path $logoFile)) { throw "missing assets\logo.png" }

function New-SquarePng([string]$srcPath, [int]$size, [string]$outPath) {
    $img = [System.Drawing.Image]::FromFile($srcPath)
    try {
        $bmp = New-Object System.Drawing.Bitmap($size, $size)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $g.Clear([System.Drawing.Color]::Transparent)
        $scale = [Math]::Min($size / $img.Width, $size / $img.Height)
        $w = [int][Math]::Round($img.Width * $scale)
        $h = [int][Math]::Round($img.Height * $scale)
        $g.DrawImage($img, [int](($size - $w) / 2), [int](($size - $h) / 2), $w, $h)
        $g.Dispose()
        New-Item -ItemType Directory -Force -Path (Split-Path $outPath) | Out-Null
        if (Test-Path $outPath) { Remove-Item $outPath -Force }
        $bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
    } finally {
        $img.Dispose()
    }
}

$densities = @{ 'mdpi' = 48; 'hdpi' = 72; 'xhdpi' = 96; 'xxhdpi' = 144; 'xxxhdpi' = 192 }
foreach ($d in $densities.Keys) {
    New-SquarePng $logoFile $densities[$d] (Join-Path $res "mipmap-$d\ic_launcher.png")
}

$splashSrc = Join-Path $assets 'splash.png'
if (-not (Test-Path $splashSrc)) { $splashSrc = $logoFile }
New-SquarePng $splashSrc 512 (Join-Path $res 'drawable\splash_image.png')

# ---------- build ----------
Write-Host '[1/6] compile resources'
& "$buildTools\aapt2.exe" compile --dir $res -o "$out\res.zip"
if ($LASTEXITCODE -ne 0) { throw 'aapt2 compile failed' }

Write-Host '[2/6] link resources and manifest'
& "$buildTools\aapt2.exe" link -o "$out\app-unsigned.apk" `
    -I $androidJar `
    --manifest "$src\AndroidManifest.xml" `
    --java "$out\gen" `
    --min-sdk-version 23 --target-sdk-version 34 `
    --version-code $versionCode --version-name $versionName `
    "$out\res.zip"
if ($LASTEXITCODE -ne 0) { throw 'aapt2 link failed' }

Write-Host '[3/6] compile java'
$sources = @()
$sources += Get-ChildItem -Path "$src\java", "$out\gen" -Recurse -Filter *.java | ForEach-Object { $_.FullName }
& "$env:JAVA_HOME\bin\javac.exe" -encoding UTF-8 --release 8 -nowarn `
    -classpath $androidJar -d "$out\classes" $sources
if ($LASTEXITCODE -ne 0) { throw 'javac failed' }

Write-Host '[4/6] dex'
$classes = Get-ChildItem -Path "$out\classes" -Recurse -Filter *.class | ForEach-Object { $_.FullName }
& "$buildTools\d8.bat" --min-api 23 --lib $androidJar --output "$out\dex" $classes
if ($LASTEXITCODE -ne 0) { throw 'd8 failed' }

Write-Host '[5/6] package and align'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open("$out\app-unsigned.apk", 'Update')
[System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
    $zip, "$out\dex\classes.dex", 'classes.dex', [System.IO.Compression.CompressionLevel]::NoCompression) | Out-Null
$zip.Dispose()
& "$buildTools\zipalign.exe" -f 4 "$out\app-unsigned.apk" "$out\app-aligned.apk"
if ($LASTEXITCODE -ne 0) { throw 'zipalign failed' }

Write-Host '[6/6] sign'
if (-not (Test-Path $keystore)) {
    & "$env:JAVA_HOME\bin\keytool.exe" -genkeypair -keystore $keystore -alias bom3 `
        -keyalg RSA -keysize 2048 -validity 10950 `
        -storepass $ksPass -keypass $ksPass -dname "CN=BOM3, OU=PDA, O=BOM3, C=CN" | Out-Null
}
$target = Join-Path $dist $apkName
if (Test-Path $target) { Remove-Item $target -Force }
& "$buildTools\apksigner.bat" sign --ks $keystore --ks-key-alias bom3 `
    --ks-pass "pass:$ksPass" --key-pass "pass:$ksPass" `
    --out $target "$out\app-aligned.apk"
if ($LASTEXITCODE -ne 0) { throw 'apksigner failed' }

Write-Host ''
Write-Host "OK: $target"
Write-Host ("size: {0:N0} KB" -f ((Get-Item $target).Length / 1KB))
