# Обёртка над build_one.py
#   .\build.ps1 -DiscordId 123456789012345678
#   .\build.ps1 -DiscordId 123456789012345678 -Obfuscate
param(
    [Parameter(Mandatory = $true)]
    [string]$DiscordId,
    [switch]$Obfuscate
)

$ErrorActionPreference = "Stop"
$arguments = @("build_one.py", "--discord-id", $DiscordId)
if ($Obfuscate) { $arguments += "--obfuscate" }

python @arguments
exit $LASTEXITCODE
