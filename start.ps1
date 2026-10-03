$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskNode = Get-Command node -ErrorAction SilentlyContinue
if (-not $taskNode) { throw 'Install Node.js 24 or newer, then run this script again.' }
& $taskNode.Source --env-file-if-exists=.env server.mjs
