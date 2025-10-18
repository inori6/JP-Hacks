[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BaseUrl,
    [string]$ApiKey,
    [string]$ReportDir
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Report {
    param([string]$Name, [string]$Content)
    if (-not $ReportDir) { return }
    try {
        if (-not (Test-Path -LiteralPath $ReportDir)) {
            New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
        }
        $path = Join-Path $ReportDir $Name
        $Content | Out-File -FilePath $path -Encoding utf8
    } catch {
        Write-Warning "Failed to write report file '$Name': $($_.Exception.Message)"
    }
}

$normalizedBase = $BaseUrl.TrimEnd('/')
$openApiUrl = "$normalizedBase/openapi.json"

try {
    $openApiResponse = Invoke-WebRequest -Uri $openApiUrl -ErrorAction Stop
    if ($null -ne $openApiResponse.Content) {
        Write-Report -Name 'A2_openapi.txt' -Content $openApiResponse.Content
        $spec = $openApiResponse.Content | ConvertFrom-Json -ErrorAction Stop
    } else {
        throw "Empty response content"
    }
} catch {
    $message = "Failed to load OpenAPI from ${openApiUrl}: $($_.Exception.Message)"
    Write-Report -Name 'A2_openapi.txt' -Content $message
    Write-Warning $message
    return @{}
}

if ($spec -and $spec.components -and $spec.components.securitySchemes -and $spec.security) {
    foreach ($requirement in $spec.security) {
        if (-not $requirement) { continue }
        foreach ($property in $requirement.PSObject.Properties) {
            $schemeName = $property.Name
            $schemeProperty = $spec.components.securitySchemes.PSObject.Properties | Where-Object { $_.Name -eq $schemeName } | Select-Object -First 1
            if (-not $schemeProperty) { continue }
            $scheme = $schemeProperty.Value
            $type = $scheme.type

            if ($type -eq 'apiKey' -and $scheme.'in' -eq 'header' -and $scheme.name) {
                if (-not $ApiKey) {
                    Write-Warning "API key required for scheme '$schemeName' but no value was supplied."
                    return @{}
                }
                $result = @{}
                $result[$scheme.name] = $ApiKey
                return $result
            }

            if ($type -eq 'http' -and ($scheme.scheme -eq 'bearer')) {
                if (-not $ApiKey) {
                    Write-Warning "Bearer token required for scheme '$schemeName' but no value was supplied."
                    return @{}
                }
                return @{ Authorization = "Bearer $ApiKey" }
            }
        }
    }
}

if ($spec -and $spec.paths) {
    $headerName = $null
    foreach ($pathEntry in $spec.paths.PSObject.Properties) {
        $operations = $pathEntry.Value
        foreach ($operationEntry in $operations.PSObject.Properties) {
            $operation = $operationEntry.Value
            if (-not $operation) { continue }
            $parameters = @()
            if ($operation.parameters) { $parameters += $operation.parameters }
            foreach ($param in $parameters) {
                if ($param.'in' -ne 'header' -or -not $param.name) { continue }
                $nameLower = $param.name.ToString().ToLowerInvariant()
                if ($nameLower -like '*api-key*') {
                    $headerName = $param.name
                    break
                }
                if ($nameLower -eq 'authorization') {
                    $headerName = $param.name
                    break
                }
            }
            if ($headerName) { break }
        }
        if ($headerName) { break }
    }

    if ($headerName) {
        if (-not $ApiKey) {
            Write-Warning "Header '$headerName' detected but no API key supplied."
            return @{}
        }

        if ($headerName.ToLowerInvariant() -eq 'authorization') {
            return @{ Authorization = "Bearer $ApiKey" }
        }

        $result = @{}
        $result[$headerName] = $ApiKey
        return $result
    }
}

return @{}
