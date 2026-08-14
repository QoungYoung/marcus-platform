$c = Get-Content -LiteralPath 'C:\Users\fengx\.codex\skills\marcus-panel-tools\SKILL.md' -TotalCount 3
$joined = $c -join ' '
$hasChinese = $joined.Contains([char]0x4EA4)
Write-Output ('first line: ' + $c[0])
Write-Output ('decoded contains chinese char: ' + $hasChinese)
