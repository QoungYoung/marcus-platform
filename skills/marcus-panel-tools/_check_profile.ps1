Write-Output ('PROFILE: ' + $PROFILE)
Write-Output ('exists: ' + (Test-Path $PROFILE))
if (Test-Path $PROFILE) {
    Write-Output '--- current content ---'
    Get-Content -LiteralPath $PROFILE -Encoding UTF8
    Write-Output '--- end ---'
}
