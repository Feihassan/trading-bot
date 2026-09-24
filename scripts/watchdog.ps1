# Keeps the dashboard reachable from Netlify with no manual steps:
#   1. runs the FastAPI backend and a Cloudflare quick tunnel to it,
#      restarting either if it exits;
#   2. restarts the tunnel if it stops answering from the outside
#      (quick tunnels have no uptime guarantee);
#   3. whenever the tunnel URL changes (it does on every cloudflared
#      restart), sets Netlify's VITE_BACKEND_URL env var and triggers a
#      rebuild so the site points at the new URL (see netlify.toml).
#
# Started at logon by scripts/install-autostart.ps1. Needs in .env:
#   NETLIFY_AUTH_TOKEN  personal access token (Netlify > User settings > Applications)
#   NETLIFY_SITE        site name or ID, e.g. animated-nasturtium-0d15f4
# Log: logs\watchdog.log

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force $LogDir | Out-Null
$WatchdogLog = Join-Path $LogDir 'watchdog.log'
$TunnelLog = Join-Path $LogDir 'cloudflared.log'

$CheckIntervalSec = 30
$PublicFailuresBeforeRestart = 4   # ~2 minutes of the tunnel not answering

function Log([string]$Message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message" | Add-Content -Path $WatchdogLog -Encoding utf8
}

function Read-DotEnv {
    $vars = @{}
    foreach ($line in Get-Content (Join-Path $Root '.env')) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $vars[$Matches[1]] = $Matches[2].Trim('"', "'")
        }
    }
    $vars
}

$Env_ = Read-DotEnv
$ApiPort = if ($Env_['API_PORT']) { $Env_['API_PORT'] } else { '8000' }
$LocalUrl = "http://127.0.0.1:$ApiPort"
$ApiHeaders = @{}
if ($Env_['API_KEY']) { $ApiHeaders['X-API-Key'] = $Env_['API_KEY'] }

function Test-Url([string]$Url) {
    try {
        $r = Invoke-WebRequest "$Url/api/status" -Headers $ApiHeaders -UseBasicParsing -TimeoutSec 15
        return $r.StatusCode -eq 200
    } catch { return $false }
}

# --- processes -------------------------------------------------------------

function Stop-Stale {
    # Leftovers from a previous watchdog run would hold port 8000 / a dead tunnel.
    Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq 'cloudflared.exe' -and $_.CommandLine -match 'tunnel') -or
        ($_.Name -eq 'python.exe' -and $_.CommandLine -match 'app\.api\.server')
    } | ForEach-Object {
        Log "Stopping stale $($_.Name) (pid $($_.ProcessId))"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Start-Backend {
    Log 'Starting backend'
    Start-Process -FilePath (Join-Path $Root '.venv\Scripts\python.exe') `
        -ArgumentList '-m', 'app.api.server' -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'backend.out.log') `
        -RedirectStandardError (Join-Path $LogDir 'backend.err.log') -PassThru
}

function Start-Tunnel {
    Log 'Starting cloudflared quick tunnel'
    $proc = Start-Process -FilePath 'cloudflared' `
        -ArgumentList 'tunnel', '--no-autoupdate', '--url', $LocalUrl -WindowStyle Hidden `
        -RedirectStandardError $TunnelLog -RedirectStandardOutput (Join-Path $LogDir 'cloudflared.out.log') -PassThru
    # cloudflared prints the assigned URL to stderr within a few seconds.
    for ($i = 0; $i -lt 60 -and -not $proc.HasExited; $i++) {
        Start-Sleep -Seconds 1
        $m = Select-String -Path $TunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) {
            $url = $m.Matches[0].Value
            Log "Tunnel URL: $url"
            return @{ Process = $proc; Url = $url }
        }
    }
    Log 'cloudflared did not report a URL - will retry'
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    return $null
}

# --- Netlify ---------------------------------------------------------------

function Invoke-Netlify([string]$Method, [string]$Path, $Body) {
    $params = @{
        Method  = $Method
        Uri     = "https://api.netlify.com/api/v1$Path"
        Headers = @{ Authorization = "Bearer $($Env_['NETLIFY_AUTH_TOKEN'])" }
        ContentType = 'application/json'
    }
    if ($null -ne $Body) { $params.Body = ConvertTo-Json -InputObject $Body -Depth 5 -Compress }
    Invoke-RestMethod @params
}

$script:WarnedMissingNetlify = $false

function Publish-BackendUrl([string]$Url) {
    # Re-read .env so adding the Netlify token doesn't need a watchdog restart.
    $script:Env_ = Read-DotEnv
    if (-not $Env_['NETLIFY_AUTH_TOKEN'] -or -not $Env_['NETLIFY_SITE']) {
        if (-not $script:WarnedMissingNetlify) {
            Log 'NETLIFY_AUTH_TOKEN / NETLIFY_SITE missing from .env - cannot update Netlify (will keep checking)'
            $script:WarnedMissingNetlify = $true
        }
        return $false
    }
    try {
        $siteRef = $Env_['NETLIFY_SITE']
        if ($siteRef -notmatch '\.') { $siteRef = "$siteRef.netlify.app" }
        $site = Invoke-Netlify GET "/sites/$siteRef" $null
        $envPath = "/accounts/$($site.account_id)/env"
        $q = "?site_id=$($site.id)"

        $current = $null
        try { $current = Invoke-Netlify GET "$envPath/VITE_BACKEND_URL$q" $null } catch {}
        if ($current) {
            if (($current.values | Where-Object { $_.context -eq 'all' }).value -eq $Url) {
                Log 'Netlify already has this URL - no rebuild needed'
                return $true
            }
            Invoke-Netlify PATCH "$envPath/VITE_BACKEND_URL$q" @{ context = 'all'; value = $Url } | Out-Null
        } else {
            Invoke-Netlify POST "$envPath$q" @(@{ key = 'VITE_BACKEND_URL'; values = @(@{ context = 'all'; value = $Url }) }) | Out-Null
        }
        Invoke-Netlify POST "/sites/$($site.id)/builds" @{} | Out-Null
        Log "Netlify VITE_BACKEND_URL set to $Url, rebuild triggered"
        return $true
    } catch {
        Log "Netlify update failed: $($_.Exception.Message)"
        return $false
    }
}

# --- main loop -------------------------------------------------------------

Log '=== watchdog started ==='
Stop-Stale
$backend = Start-Backend
$tunnel = $null
$publishedUrl = $null
$publicFailures = 0

while ($true) {
    try {
        if ($backend.HasExited) {
            Log "Backend exited (code $($backend.ExitCode)) - restarting"
            $backend = Start-Backend
        }

        if (-not $tunnel -or $tunnel.Process.HasExited) {
            if ($tunnel) { Log 'cloudflared exited - restarting' }
            $tunnel = Start-Tunnel
            $publicFailures = 0
        }

        if ($tunnel) {
            if ($tunnel.Url -ne $publishedUrl -and (Publish-BackendUrl $tunnel.Url)) {
                $publishedUrl = $tunnel.Url
            }

            # Only blame the tunnel if the backend itself is answering.
            if ((Test-Url $LocalUrl) -and -not (Test-Url $tunnel.Url)) {
                $publicFailures++
                if ($publicFailures -ge $PublicFailuresBeforeRestart) {
                    Log "Tunnel unreachable from outside $publicFailures checks in a row - restarting it"
                    Stop-Process -Id $tunnel.Process.Id -Force -ErrorAction SilentlyContinue
                    $tunnel = $null
                }
            } else {
                $publicFailures = 0
            }
        }
    } catch {
        Log "Watchdog loop error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $CheckIntervalSec
}
