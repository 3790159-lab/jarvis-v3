# Launches the RunPod GPU sniper fully detached from any SSH session.
# Invoked by the JarvisSniperDetached scheduled task (runs in session 0,
# survives SSH/tunnel drops, logoff, and sshd restarts).
# PYTHONUTF8=1 prevents the cp1251 emoji crash (see memory jarvis-detached-bot-utf8).
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location 'C:\jarvis'
$py  = 'C:\jarvis\.venv\Scripts\python.exe'
$out = 'C:\jarvis\state\logs\runpod_sniper.detached.stdout.log'
$err = 'C:\jarvis\state\logs\runpod_sniper.detached.stderr.log'
& $py 'scripts\runpod_gpu_sniper.py' 1>> $out 2>> $err
