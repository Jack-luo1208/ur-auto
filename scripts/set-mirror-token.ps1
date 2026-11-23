param(
    [Parameter(Mandatory = $true)][string]$Token,
    [string]$Repo = "cctrylyj/ur-auto",
    [switch]$Dispatch
)

# Sets the MIRROR_TOKEN repository secret without touching the GitHub web UI.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\set-mirror-token.ps1 -Token ghp_xxx -Dispatch
$ErrorActionPreference = "Stop"
$env:GIT_TERMINAL_PROMPT = "0"

$cred = "protocol=https`nhost=github.com`n`n" | git credential fill 2>$null
$apiTok = ($cred | Select-String '^password=').ToString() -replace '^password=', ''
if (-not $apiTok) { throw "no stored github credential for this machine" }

$py = @'
import base64, json, os, sys, urllib.request
from nacl import encoding, public

repo, value, api_tok = sys.argv[1], sys.argv[2], sys.argv[3]
H = {"Authorization": "token " + api_tok, "User-Agent": "ps", "Accept": "application/vnd.github+json"}

def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("https://api.github.com" + path, data=data, method=method, headers=H)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}

pk = api("/repos/%s/actions/secrets/public-key" % repo)
sealed = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder))
enc = base64.b64encode(sealed.encrypt(value.encode())).decode()
api("/repos/%s/actions/secrets/MIRROR_TOKEN" % repo, "PUT", {"encrypted_value": enc, "key_id": pk["key_id"]})
names = [s["name"] for s in api("/repos/%s/actions/secrets" % repo).get("secrets", [])]
print("secrets:", ", ".join(names))
'@

$tmp = Join-Path $env:TEMP ("set-mirror-" + [guid]::NewGuid().ToString("N") + ".py")
Set-Content -LiteralPath $tmp -Value $py -Encoding utf8
try {
    python $tmp $Repo $Token $apiTok
}
finally {
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
}

if ($Dispatch) {
    $body = @{ ref = "main" } | ConvertTo-Json
    Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$Repo/actions/workflows/mirror.yml/dispatches" `
        -Headers @{ Authorization = "token $apiTok"; "User-Agent" = "ps"; Accept = "application/vnd.github+json" } `
        -Body $body -ContentType "application/json" | Out-Null
    Write-Host "mirror workflow dispatched — open https://github.com/$Repo/actions"
}
