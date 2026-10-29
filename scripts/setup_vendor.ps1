# Fetch pinned WOFOST crop parameters into vendor\wofost72 and vendor\wofost81 (no git needed).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$root = Split-Path $PSScriptRoot -Parent
$pins = @{ "wofost72" = "fc6eda46ea0081eb0ad31fa92ea49fea60e11075"; "wofost81" = "1ca91d7dbb5aaf6b41462162de03a8254812d3a5" }
foreach ($b in $pins.Keys) {
    $sha = $pins[$b]
    $dest = Join-Path $root "vendor\$b"
    if (Test-Path (Join-Path $dest "potato.yaml")) { Write-Host "$b already present"; continue }
    $tmp = Join-Path $env:TEMP "wofost_$b"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zip = Join-Path $tmp "p.zip"
    Invoke-WebRequest -UseBasicParsing "https://github.com/ajwdewit/WOFOST_crop_parameters/archive/$sha.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $tmp
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Move-Item (Join-Path $tmp "WOFOST_crop_parameters-$sha") $dest
    Remove-Item -Recurse -Force $tmp
    Write-Host "$b ready: $dest"
}
