# Run from any folder; arguments are forwarded as individual values, never shell text.
$ErrorActionPreference = 'Stop'
$AccSetup = Join-Path $PSScriptRoot 'acc/setup.py'
$AccArguments = @($args)
if ($AccArguments.Count -eq 0) { $AccArguments = @('launch') }
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $AccSetup @AccArguments
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $AccSetup @AccArguments
} else {
    throw 'ACC requires Python 3.10 or newer on PATH.'
}
exit $LASTEXITCODE
