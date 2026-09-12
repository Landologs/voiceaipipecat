param(
    [int]$Port = 8000
)

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = Join-Path $root ".env"
if (-not $env:NGROK_AUTHTOKEN -and (Test-Path -LiteralPath $envFile)) {
    $tokenLine = Get-Content -LiteralPath $envFile |
        Where-Object { $_ -match '^\s*NGROK_AUTHTOKEN\s*=' } |
        Select-Object -Last 1
    if ($tokenLine) {
        $token = ($tokenLine -split '=', 2)[1].Trim().Trim('"').Trim("'")
        if ($token) {
            $env:NGROK_AUTHTOKEN = $token
        }
    }
}

$ngrokCommand = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrokCommand) {
    Write-Error "ngrok is not installed. Install the official ngrok client, authenticate it, and rerun this script."
    exit 1
}

& $ngrokCommand.Source http "http://127.0.0.1:$Port"
