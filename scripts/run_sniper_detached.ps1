# Launches the RunPod GPU sniper fully detached from any SSH session.
# Invoked by the JarvisSniperDetached scheduled task (runs in session 0,
# survives SSH/tunnel drops, logoff, and sshd restarts).
# PYTHONUTF8=1 prevents the cp1251 emoji crash (see memory jarvis-detached-bot-utf8).
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
# DEV-59: declare the console encoding as UTF-8 -- the READER side, and the
# OTHER end of the pipe from PYTHONUTF8 just above (the WRITER side). Both
# stay: the mediator is what makes the child python emit UTF-8, the
# declaration is what makes this console read it back. Removing one because
# the other landed would close a path with nothing.
# Measured 24.08 with a probe task: under Task Scheduler the console starts in
# cp866 and this setter does not throw even with no console attached.
# Stands before the first print (`& $py ... 1>> $out` below).
# ASCII only on purpose -- no BOM on this file, so PS 5.1 decodes it cp1251.
# KNOWN AND NOT FIXED HERE: `1>>` writes UTF-16LE no matter what this line
# declares (spec DEV-59, section 7). Separate defect, separate change.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location 'C:\jarvis'
$py  = 'C:\jarvis\.venv\Scripts\python.exe'
$out = 'C:\jarvis\state\logs\runpod_sniper.detached.stdout.log'
$err = 'C:\jarvis\state\logs\runpod_sniper.detached.stderr.log'
& $py 'scripts\runpod_gpu_sniper.py' 1>> $out 2>> $err
