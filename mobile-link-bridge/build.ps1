$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $Root
$SdkCandidates = @(
    $env:SPIRITKIN_ANDROID_SDK_ROOT,
    $env:ANDROID_SDK_ROOT,
    $env:ANDROID_HOME,
    (Join-Path $WorkspaceRoot 'tools\android-sdk')
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
$SdkRoot = $null
foreach ($Candidate in $SdkCandidates) {
    $ResolvedCandidate = [System.IO.Path]::GetFullPath($Candidate)
    if (Test-Path (Join-Path $ResolvedCandidate 'platforms\android-35\android.jar')) {
        $SdkRoot = $ResolvedCandidate
        break
    }
}
if ([string]::IsNullOrWhiteSpace($SdkRoot)) {
    throw "Android SDK not found. Set SPIRITKIN_ANDROID_SDK_ROOT, ANDROID_SDK_ROOT, or place the SDK under $WorkspaceRoot\tools\android-sdk."
}
$BuildTools = Join-Path $SdkRoot 'build-tools\35.0.0'
$PlatformJar = Join-Path $SdkRoot 'platforms\android-35\android.jar'
foreach ($RequiredTool in @('aapt2.exe', 'aapt.exe', 'zipalign.exe', 'apksigner.bat', 'd8.bat')) {
    if (!(Test-Path (Join-Path $BuildTools $RequiredTool))) {
        throw "Android build tool missing: $RequiredTool under $BuildTools"
    }
}
$Out = Join-Path $Root 'out'
$Releases = Join-Path $Out 'releases'
$Classes = Join-Path $Out 'classes'
$Unsigned = Join-Path $Out 'mobile-link-bridge-unsigned.apk'
$Aligned = Join-Path $Out 'mobile-link-bridge-aligned.apk'
$Signed = Join-Path $Out 'mobile-link-bridge.apk'
$ReleaseManifest = Join-Path $Out 'release-manifest.json'
$ReleaseHistory = Join-Path $Out 'release-history.json'
$HashFile = Join-Path $Out 'mobile-link-bridge.apk.sha256'
$ClassesJar = Join-Path $Out 'classes.jar'
$Keystore = Join-Path $Root 'debug.keystore'
$OldKeystore = Join-Path $Out 'debug.keystore'
$PreviousHistory = $null
$PreviousReleaseFiles = @()
if (Test-Path $ReleaseHistory) {
    try {
        $PreviousHistory = Get-Content -LiteralPath $ReleaseHistory -Raw | ConvertFrom-Json
    } catch {
        $PreviousHistory = $null
    }
}
if (Test-Path $Releases) {
    $PreviousReleaseFiles = Get-ChildItem -LiteralPath $Releases -File | ForEach-Object {
        [ordered]@{
            Name = $_.Name
            Bytes = [System.IO.File]::ReadAllBytes($_.FullName)
        }
    }
}

if (!(Test-Path $Keystore) -and (Test-Path $OldKeystore)) {
    Copy-Item -LiteralPath $OldKeystore -Destination $Keystore -Force
}

if (Test-Path $Out) {
    Remove-Item -LiteralPath $Out -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Classes | Out-Null
New-Item -ItemType Directory -Force -Path $Releases | Out-Null
foreach ($ReleaseFile in $PreviousReleaseFiles) {
    [System.IO.File]::WriteAllBytes((Join-Path $Releases $ReleaseFile.Name), [byte[]]$ReleaseFile.Bytes)
}

& (Join-Path $BuildTools 'aapt2.exe') compile --dir (Join-Path $Root 'res') -o (Join-Path $Out 'res.zip')
& (Join-Path $BuildTools 'aapt2.exe') link `
    -I $PlatformJar `
    --manifest (Join-Path $Root 'AndroidManifest.xml') `
    -o $Unsigned `
    (Join-Path $Out 'res.zip') `
    --java (Join-Path $Out 'gen')

$Sources = @()
$Sources += Get-ChildItem -Path (Join-Path $Root 'src') -Filter *.java -Recurse | ForEach-Object FullName
$Sources += Get-ChildItem -Path (Join-Path $Out 'gen') -Filter *.java -Recurse | ForEach-Object FullName
javac -encoding UTF-8 -source 8 -target 8 -bootclasspath $PlatformJar -d $Classes $Sources
Push-Location $Classes
try {
    jar cf $ClassesJar .
} finally {
    Pop-Location
}
& (Join-Path $BuildTools 'd8.bat') --lib $PlatformJar --output $Out $ClassesJar
if (!(Test-Path (Join-Path $Out 'classes.dex'))) {
    throw "d8 did not generate classes.dex"
}
Copy-Item -LiteralPath $Unsigned -Destination (Join-Path $Out 'with-classes.apk') -Force
Push-Location $Out
try {
    & (Join-Path $BuildTools 'aapt.exe') add 'with-classes.apk' 'classes.dex' | Out-Null
} finally {
    Pop-Location
}
& (Join-Path $BuildTools 'zipalign.exe') -f 4 (Join-Path $Out 'with-classes.apk') $Aligned

if (!(Test-Path $Keystore)) {
    keytool -genkeypair -keystore $Keystore -storepass android -keypass android -alias debug `
        -keyalg RSA -keysize 2048 -validity 10000 `
        -dname 'CN=PDD Link Bridge,O=SpiritKin,C=CN'
}
& (Join-Path $BuildTools 'apksigner.bat') sign --ks $Keystore --ks-pass pass:android --key-pass pass:android --out $Signed $Aligned
& (Join-Path $BuildTools 'apksigner.bat') verify $Signed
$ManifestText = Get-Content -LiteralPath (Join-Path $Root 'AndroidManifest.xml') -Raw
$PackageName = [regex]::Match($ManifestText, 'package="([^"]+)"').Groups[1].Value
$VersionCode = [regex]::Match($ManifestText, 'android:versionCode="([^"]+)"').Groups[1].Value
$VersionName = [regex]::Match($ManifestText, 'android:versionName="([^"]+)"').Groups[1].Value
$MinSdk = [regex]::Match($ManifestText, 'android:minSdkVersion="([^"]+)"').Groups[1].Value
$TargetSdk = [regex]::Match($ManifestText, 'android:targetSdkVersion="([^"]+)"').Groups[1].Value
if ([string]::IsNullOrWhiteSpace($PackageName)) { $PackageName = 'com.spiritkin.mobilelinkbridge' }
if ([string]::IsNullOrWhiteSpace($MinSdk)) { $MinSdk = '23' }
if ([string]::IsNullOrWhiteSpace($TargetSdk)) { $TargetSdk = '35' }
$ApkItem = Get-Item -LiteralPath $Signed
$Sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Signed).Hash.ToLowerInvariant()
$BuiltAt = (Get-Date).ToUniversalTime().ToString('o')
$KeystoreHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Keystore).Hash.ToLowerInvariant()
$DownloadFile = Split-Path -Leaf $Signed
$VersionedApkName = "mobile-link-bridge-$VersionName.apk"
Copy-Item -LiteralPath $Signed -Destination (Join-Path $Releases $VersionedApkName) -Force
$ReleaseEntry = [ordered]@{
    package_name = $PackageName
    version_code = [int64]$VersionCode
    version_name = $VersionName
    file_name = $DownloadFile
    archive_file = "releases/$VersionedApkName"
    sha256 = $Sha256
    size_bytes = [int64]$ApkItem.Length
    built_at = $BuiltAt
}
$PreviousVersions = @()
$SeenVersionCodes = @{}
$SeenVersionCodes["$VersionCode"] = $true
if ($PreviousHistory -and $PreviousHistory.releases) {
    foreach ($Release in @($PreviousHistory.releases)) {
        if ($null -eq $Release.version_code) { continue }
        $ReleaseVersionCode = "$($Release.version_code)"
        if ($SeenVersionCodes.ContainsKey($ReleaseVersionCode)) { continue }
        $SeenVersionCodes[$ReleaseVersionCode] = $true
        $PreviousVersions += [ordered]@{
            package_name = "$($Release.package_name)"
            version_code = [int64]$Release.version_code
            version_name = "$($Release.version_name)"
            file_name = "$($Release.file_name)"
            archive_file = "$($Release.archive_file)"
            sha256 = "$($Release.sha256)"
            size_bytes = [int64]$Release.size_bytes
            built_at = "$($Release.built_at)"
        }
    }
}
$PreviousVersions = @($PreviousVersions | Select-Object -First 9)
$ReleaseManifestObject = [ordered]@{
    manifest_version = 2
    app_id = $PackageName
    package_name = $PackageName
    version_code = [int64]$VersionCode
    version_name = $VersionName
    download_file = $DownloadFile
    archive_file = "releases/$VersionedApkName"
    download_url = ''
    sha256 = $Sha256
    size_bytes = [int64]$ApkItem.Length
    updated_at = $BuiltAt
    compatibility = [ordered]@{
        min_sdk = [int]$MinSdk
        target_sdk = [int]$TargetSdk
        max_sdk = 0
        requires_unknown_app_install_permission = $true
    }
    integrity = [ordered]@{
        algorithm = 'sha256'
        sha256 = $Sha256
        size_bytes = [int64]$ApkItem.Length
        signature_scheme = 'apk_signature_v2_or_newer'
        same_package_signature_required = $true
        keystore_sha256 = $KeystoreHash
    }
    rollback = [ordered]@{
        supported = $true
        strategy = 'serve an older signed APK with matching package/signing key if release-manifest.json is rolled back'
        previous_versions = $PreviousVersions
    }
    notes = 'SpiritKin Control Bridge Android update'
}
$ReleaseManifestObject | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReleaseManifest -Encoding UTF8
"$Sha256  $DownloadFile" | Set-Content -LiteralPath $HashFile -Encoding ASCII
$HistoryObject = [ordered]@{
    history_version = 1
    updated_at = $BuiltAt
    releases = @($ReleaseEntry) + $PreviousVersions
}
$HistoryObject | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReleaseHistory -Encoding UTF8
Write-Output $Signed
