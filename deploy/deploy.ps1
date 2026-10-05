param(
    [Parameter(Mandatory = $true)] [string]$Server,
    [Parameter(Mandatory = $true)] [string]$Release,
    [string]$RemoteRoot = "/opt/local-video-editor",
    [string]$Python = "python3.11"
)

$ErrorActionPreference = "Stop"
$remoteRelease = "$RemoteRoot/releases/$Release"
$archive = Join-Path $env:TEMP "local-video-editor-$Release.zip"

# Runtime media is intentionally excluded from the release archive.
if (Test-Path $archive) { Remove-Item $archive -Force }
& tar.exe -a -c -f $archive `
    --exclude=data `
    --exclude=projects `
    --exclude=logs `
    --exclude=.pytest_cache `
    --exclude=__pycache__ `
    --exclude=.env `
    --exclude=*.sqlite3 `
    .

ssh $Server "mkdir -p '$remoteRelease'"
scp $archive "$Server`:$remoteRelease/release.zip"
ssh $Server "cd '$remoteRelease' && unzip -oq release.zip && rm release.zip && '$RemoteRoot/venv/bin/pip' install --disable-pip-version-check -e '.[dev]'"
ssh $Server "ln -sfn '$remoteRelease' '$RemoteRoot/current' && sudo systemctl restart local-video-editor && sudo systemctl is-active --quiet local-video-editor"

Remove-Item $archive -Force
Write-Host "Deployed $Release to $Server"
