# Keeps the dashboard reachable from Netlify with no manual steps:
#   1. runs the FastAPI backend, restarting it if it exits;
#   2. exposes it on the internet with Tailscale Funnel at this machine's
#      permanent https://<machine>.<tailnet>.ts.net address, re-applying
#      the Funnel config if the public address stops answering;
#   3. makes sure Netlify's VITE_BACKEND_URL env var holds that address,
#      rebuilding the site only if it had to change it (see netlify.toml).
#      With Funnel the address never changes, so after the first run
#      restarts need no Netlify rebuild.
#
# Tailscale itself runs as a Windows service (starts at boot) and remembers
# the Funnel config, so this script mostly keeps the backend alive.
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
$Tailscale = 'C:\Program Files\Tailscale\tailscale.exe'

$CheckIntervalSec = 30
$PublicFailuresBeforeReset = 4   # ~2 minutes of the public address not answering

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
    # Leftovers from a previous watchdog run would hold port 8000. Also
    # clears out any cloudflared quick tunnel from the pre-Tailscale setup.
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

# --- Tailscale Funnel ------------------------------------------------------

function Get-PublicUrl {
    # This machine's permanent MagicDNS name, e.g. desktop-x.tailnet.ts.net
    try {
        $status = & $Tailscale status --json | ConvertFrom-Json
        if ($status.BackendState -ne 'Running') {
            Log "Tailscale not running (state: $($status.BackendState))"
            return $null
        }
        return 'https://' + $status.Self.DNSName.TrimEnd('.')
    } catch {
        Log "Could not read Tailscale status: $($_.Exception.Message)"
        return $null
    }
}

function Enable-Funnel {
    # Idempotent; the config persists across reboots in the Tailscale service.
    $out = & $Tailscale funnel --bg $ApiPort 2>&1 | Out-String
    Log "tailscale funnel --bg ${ApiPort}: $($out.Trim() -replace '\s+', ' ')"
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
                Log "Netlify already points at $Url - no rebuild needed"
                return $true
            }
            # PUT replaces the whole variable. PATCH (the "update one value"
            # endpoint) was observed to return 422 or silently not apply.
            Invoke-Netlify PUT "$envPath/VITE_BACKEND_URL$q" @{
                key = 'VITE_BACKEND_URL'; scopes = @('builds', 'functions', 'post_processing', 'runtime')
                values = @(@{ context = 'all'; value = $Url })
            } | Out-Null
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
$publicUrl = $null
$publishedUrl = $null
$funnelEnabled = $false
$publicFailures = 0

while ($true) {
    try {
        if ($backend.HasExited) {
            Log "Backend exited (code $($backend.ExitCode)) - restarting"
            $backend = Start-Backend
        }

        if (-not $publicUrl) {
            $publicUrl = Get-PublicUrl
            if ($publicUrl) { Log "Public URL: $publicUrl" }
        }

        if ($publicUrl) {
            if (-not $funnelEnabled) {
                Enable-Funnel
                $funnelEnabled = $true
            }
            if ($publicUrl -ne $publishedUrl -and (Publish-BackendUrl $publicUrl)) {
                $publishedUrl = $publicUrl
            }

            # Only blame Funnel if the backend itself is answering.
            if ((Test-Url $LocalUrl) -and -not (Test-Url $publicUrl)) {
                $publicFailures++
                if ($publicFailures -ge $PublicFailuresBeforeReset) {
                    Log "Public URL unreachable $publicFailures checks in a row - re-applying Funnel"
                    $publicUrl = $null
                    $funnelEnabled = $false
                    $publicFailures = 0
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
