# Перешифровать .env -> .env.enc после add_secret.ps1.
#
# ЗАЧЕМ: add_secret.ps1 пишет в PLAINTEXT .env, а bootstrap_env при наличии
# .env.enc читает ТОЛЬКО .enc (P1P2 §2.1). Без этого шага добавленный ключ до
# процесса НЕ доезжает, и никто об этом не сообщает. Трижды делали руками;
# один раз .env был свежий, .env.enc четырёхдневный, и бандл секретов чуть не
# уехал со старым env — recovery export берёт .enc.
#
# ОБЁРТКА ТОНКАЯ НАМЕРЕННО. Вся логика в scripts/reencrypt_env.py, потому что
# она под pytest (tests/test_reencrypt_env.py), а PowerShell тестируется плохо
# — тот же принцип, что в chatter_guardian_detached.ps1: решение принимает
# сторона, которую можно закрыть сторожем.
#
# Использование:
#   .\scripts\reencrypt_env.ps1              # перешифровать (если нужно)
#   .\scripts\reencrypt_env.ps1 -Check       # только доложить статус
#
# Коды возврата -Check: 0 = in_sync (можно снимать бандл), 1 = всё остальное.
#
# Значения секретов не печатаются: отчёт содержит только имена ключей.
param(
    [string]$Root = 'C:\jarvis',
    [switch]$Check
)

$ErrorActionPreference = 'Stop'

# Только венвовый интерпретатор: `python` в PATH — чужое окружение, и
# chatter.security оттуда не импортируется (грабля jarvis-wrong-python-on-path).
$py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "нет $py — venv не на месте, останавливаюсь" }

$env:PYTHONUTF8 = '1'
$script = Join-Path $Root 'scripts\reencrypt_env.py'
$argsList = @($script, '--root', $Root)
if ($Check) { $argsList += '--check' }

& $py @argsList
exit $LASTEXITCODE
