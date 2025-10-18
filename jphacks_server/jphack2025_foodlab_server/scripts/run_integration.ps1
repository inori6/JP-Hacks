param(
    [string[]]$ExtraArgs
)

if (-not $Env:API_BASE) {
    $Env:API_BASE = 'https://lab.160.16.126.35.sslip.io'
}
$Env:RUN_INTEGRATION = '1'

pytest -m integration -q @ExtraArgs
