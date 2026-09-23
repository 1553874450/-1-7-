$ErrorActionPreference = "Stop"

$Python = "C:\Users\Jerry\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$Script = Join-Path $PSScriptRoot "ark_1_7_runner.py"

& $Python $Script --mode find-and-loop @args
