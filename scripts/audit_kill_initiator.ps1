#Requires -RunAsAdministrator
<#
ДИАГНОСТИКА, ВКЛЮЧАЕМАЯ ВРЕМЕННО. Решение владельца 18.08, пункт 3:
«включить, дождаться смерти, выключить». Напоминание выключить —
в state/dev_backlog_pipeline.md.

## Зачем это, если код выхода уже известен

Код −1 сузил круг, но НЕ назвал имени. `TerminateProcess(h, -1)` — это
подпись `Stop-Process` в PowerShell и `Process.Kill()` в .NET (`taskkill /F`
даёт 1, свой `os._exit(0)` — 0). То есть бота обрывает НАШ инструмент,
а не сторонний. Каких именно — из кода выхода не следует.

Событие 4689 («процесс завершён») инициатора НЕ называет: в нём Subject —
учётка САМОГО умершего процесса. Имя убийцы даёт другое событие — 4656
(«запрошен хэндл объекта»), и только если на объекте стоит SACL. Поэтому
здесь два действия, а не одно:

  1. включить подкатегории аудита (Kernel Object + Process Termination);
  2. повесить SACL на ЖИВОЙ процесс бота — аудит успеха на праве
     PROCESS_TERMINATE.

Тогда 4656 назовёт «Process Name» того, кто открыл хэндл с правом убить.

## Про два PID — это НЕ дубль

`.venv\Scripts\python.exe` в venv это ЛАУНЧЕР: он поднимает настоящий
интерпретатор отдельным процессом и держит его в job object с
kill-on-close. Замер 18.08 это подтвердил на живом боте:

    PID 3152 (лаунчер) : inJob=False
    PID 6696 (бот)     : inJob=True

Следствие дорогое: убийце достаточно снести ЛАУНЧЕР — настоящий бот умрёт
сам, вместе с закрытием job'а, без трассировки и без своего кода выхода.
Ровно это и видит отпечаток: «между BOOT и BOOT ни трассировки, ни
EXIT-CLEAN». Поэтому SACL вешается на ОБА процесса, а не на «главный».

## Прогон

    powershell -File scripts\audit_kill_initiator.ps1 -Arm      # включить
    powershell -File scripts\audit_kill_initiator.ps1 -Report   # что поймано
    powershell -File scripts\audit_kill_initiator.ps1 -Off      # ВЫКЛЮЧИТЬ
#>
[CmdletBinding()]
param(
    [switch]$Arm,
    [switch]$Report,
    [switch]$Off,
    # Бот перезапускается гардианом за ~90 с после смерти, а SACL живёт на
    # объекте ядра и умирает вместе с процессом. Без слежения проба
    # разоружается сама на первой же смерти — и следующую, ту самую, встретит
    # голой.
    [switch]$Watch,
    [int]$WatchSeconds = 20,
    # Окно отчёта. По умолчанию — сутки, чтобы отчёт после ночной смерти
    # не оказался пустым просто из-за границы окна.
    [int]$ReportHours = 24
)

$ErrorActionPreference = 'Stop'

$Root       = Split-Path -Parent $PSScriptRoot
$StateDir   = Join-Path $Root 'state\audit'
$BackupFile = Join-Path $StateDir 'auditpol_before_kill_probe.csv'
$MarkFile   = Join-Path $StateDir 'kill_probe_armed.txt'

# Подкатегории аудита названы GUID'ами намеренно: имена локализованы, и на
# русской системе `auditpol /set /subcategory:"Process Termination"` падает
# с 0x57. GUID одинаков на любой локали.
$SUB_KERNEL_OBJECT      = '{0CCE921F-69AE-11D9-BED3-505054503030}'
$SUB_PROCESS_TERMINATION = '{0CCE922C-69AE-11D9-BED3-505054503030}'
$SUB_PROCESS_CREATION    = '{0CCE922B-69AE-11D9-BED3-505054503030}'

$PROCESS_TERMINATE = 0x0001

function Get-BotProcesses {
    <# Тот же признак, которым бота находит гардиан, — иначе пробы разойдутся
       и SACL встанет не на тот процесс. #>
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*jarvis_smart_telegram_control*' })
}

function Initialize-Interop {
    if ('KillProbe.Native' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace KillProbe {
  [StructLayout(LayoutKind.Sequential)]
  public struct LUID { public uint LowPart; public int HighPart; }

  [StructLayout(LayoutKind.Sequential)]
  public struct LUID_AND_ATTRIBUTES { public LUID Luid; public uint Attributes; }

  [StructLayout(LayoutKind.Sequential)]
  public struct TOKEN_PRIVILEGES {
    public uint PrivilegeCount;
    public LUID_AND_ATTRIBUTES Privilege;
  }

  public static class Native {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr h);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr GetCurrentProcess();

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool IsProcessInJob(IntPtr proc, IntPtr job, out bool result);

    [DllImport("advapi32.dll", SetLastError = true)]
    public static extern bool OpenProcessToken(IntPtr proc, uint access, out IntPtr token);

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool LookupPrivilegeValue(string system, string name, out LUID luid);

    [DllImport("advapi32.dll", SetLastError = true)]
    public static extern bool AdjustTokenPrivileges(IntPtr token, bool disableAll,
      ref TOKEN_PRIVILEGES newState, uint bufferLength, IntPtr previous, IntPtr returnLength);

    [DllImport("advapi32.dll", SetLastError = true)]
    public static extern uint SetSecurityInfo(IntPtr handle, int objectType,
      uint securityInfo, IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl);
  }
}
'@
}

function Enable-SecurityPrivilege {
    <# Без SeSecurityPrivilege SetSecurityInfo на SACL вернёт «отказано» даже
       администратору: право есть в токене, но по умолчанию ВЫКЛЮЧЕНО. #>
    Initialize-Interop
    $TOKEN_ADJUST_PRIVILEGES = 0x0020
    $TOKEN_QUERY             = 0x0008
    $SE_PRIVILEGE_ENABLED    = 0x00000002

    $token = [IntPtr]::Zero
    if (-not [KillProbe.Native]::OpenProcessToken([KillProbe.Native]::GetCurrentProcess(),
            $TOKEN_ADJUST_PRIVILEGES -bor $TOKEN_QUERY, [ref]$token)) {
        throw "OpenProcessToken: $([ComponentModel.Win32Exception]::new([Runtime.InteropServices.Marshal]::GetLastWin32Error()).Message)"
    }
    try {
        $luid = New-Object KillProbe.LUID
        if (-not [KillProbe.Native]::LookupPrivilegeValue($null, 'SeSecurityPrivilege', [ref]$luid)) {
            throw "LookupPrivilegeValue: $([ComponentModel.Win32Exception]::new([Runtime.InteropServices.Marshal]::GetLastWin32Error()).Message)"
        }
        # 🔴 Собирать вложенную структуру ЦЕЛИКОМ и присваивать одним куском.
        # `$tp.Privilege.Luid = $luid` правит КОПИЮ поля-структуры и молча
        # пропадает: в API уезжает пустой LUID, а ответ приходит «1300 — право
        # не назначено», хотя `whoami /priv` показывает его включённым. Ровно
        # тот же класс, что `-Db` против `-Debug`: сообщение указывает не туда.
        $la = New-Object KillProbe.LUID_AND_ATTRIBUTES
        $la.Luid = $luid
        $la.Attributes = $SE_PRIVILEGE_ENABLED

        $tp = New-Object KillProbe.TOKEN_PRIVILEGES
        $tp.PrivilegeCount = 1
        $tp.Privilege = $la
        [void][KillProbe.Native]::AdjustTokenPrivileges($token, $false, [ref]$tp, 0, [IntPtr]::Zero, [IntPtr]::Zero)
        # AdjustTokenPrivileges возвращает TRUE и тогда, когда включила НЕ ВСЁ.
        # Единственный честный признак — код последней ошибки.
        $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if ($err -ne 0) { throw "AdjustTokenPrivileges: код $err (право не включено)" }
    }
    finally { [void][KillProbe.Native]::CloseHandle($token) }
}

function Set-TerminateAudit {
    param([int]$ProcId, [switch]$Clear)

    Initialize-Interop
    $ACCESS_SYSTEM_SECURITY = 0x01000000
    $READ_CONTROL           = 0x00020000
    $SE_KERNEL_OBJECT       = 6
    $SACL_SECURITY_INFORMATION = 0x00000008

    $h = [KillProbe.Native]::OpenProcess($ACCESS_SYSTEM_SECURITY -bor $READ_CONTROL, $false, $ProcId)
    if ($h -eq [IntPtr]::Zero) {
        throw "OpenProcess($ProcId): $([ComponentModel.Win32Exception]::new([Runtime.InteropServices.Marshal]::GetLastWin32Error()).Message)"
    }
    $pSacl = [IntPtr]::Zero
    try {
        $sacl = New-Object System.Security.AccessControl.RawAcl(
            [System.Security.AccessControl.GenericAcl]::AclRevision, 1)
        if (-not $Clear) {
            # Everyone. Аудитим УСПЕХ и ровно право «убить»: если аудитить
            # всё подряд, журнал забьют опросы гардиана (он читает процесс
            # каждые несколько секунд), и в шуме утонет единственная строка,
            # ради которой всё затевалось.
            $everyone = New-Object System.Security.Principal.SecurityIdentifier(
                [System.Security.Principal.WellKnownSidType]::WorldSid, $null)
            $ace = New-Object System.Security.AccessControl.CommonAce(
                [System.Security.AccessControl.AceFlags]::SuccessfulAccess,
                [System.Security.AccessControl.AceQualifier]::SystemAudit,
                $PROCESS_TERMINATE, $everyone, $false, $null)
            $sacl.InsertAce(0, $ace)
        }
        $bytes = New-Object byte[] $sacl.BinaryLength
        $sacl.GetBinaryForm($bytes, 0)
        $pSacl = [Runtime.InteropServices.Marshal]::AllocHGlobal($bytes.Length)
        [Runtime.InteropServices.Marshal]::Copy($bytes, 0, $pSacl, $bytes.Length)

        $rc = [KillProbe.Native]::SetSecurityInfo($h, $SE_KERNEL_OBJECT,
                $SACL_SECURITY_INFORMATION, [IntPtr]::Zero, [IntPtr]::Zero, [IntPtr]::Zero, $pSacl)
        if ($rc -ne 0) {
            throw "SetSecurityInfo($ProcId): код $rc ($([ComponentModel.Win32Exception]::new([int]$rc).Message))"
        }
    }
    finally {
        if ($pSacl -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::FreeHGlobal($pSacl) }
        [void][KillProbe.Native]::CloseHandle($h)
    }
}

function Test-InJob {
    param([int]$ProcId)
    Initialize-Interop
    $PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    $h = [KillProbe.Native]::OpenProcess($PROCESS_QUERY_LIMITED_INFORMATION, $false, $ProcId)
    if ($h -eq [IntPtr]::Zero) { return $null }
    try {
        $inJob = $false
        if ([KillProbe.Native]::IsProcessInJob($h, [IntPtr]::Zero, [ref]$inJob)) { return $inJob }
        return $null
    }
    finally { [void][KillProbe.Native]::CloseHandle($h) }
}

function Invoke-Arm {
    if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }

    # 1. Снимок политики ДО правки. Выключение — это восстановление снимка, а
    #    не «поставить обратно Disable»: часть подкатегорий могла быть включена
    #    до нас, и глушить их значит увести машину в состояние, которого
    #    никогда не было.
    if (-not (Test-Path $BackupFile)) {
        & auditpol /backup /file:"$BackupFile" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "auditpol /backup вернул $LASTEXITCODE — состояние ДО не снято, включать нельзя" }
        Write-Host "снимок политики ДО: $BackupFile"
    } else {
        Write-Host "снимок политики ДО уже есть, не перезаписываю: $BackupFile"
    }

    foreach ($sub in @($SUB_KERNEL_OBJECT, $SUB_PROCESS_TERMINATION, $SUB_PROCESS_CREATION)) {
        & auditpol /set /subcategory:"$sub" /success:enable | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "auditpol /set $sub вернул $LASTEXITCODE" }
    }
    Write-Host "аудит включён: Kernel Object + Process Termination + Process Creation (успех)"

    # 🔴 Без этого ключа 4688 называет только ИМЯ образа. 18.08 замер уперся
    # ровно в это: ETW назвал убийцу — PID 8416, powershell.exe, — а какой
    # скрипт он исполнял, восстановить было НЕЧЕМ: процесс прожил секунды и
    # вышел сразу после убийства. Имя `powershell.exe` без командной строки
    # не отвечает на вопрос «кто», оно только сужает его.
    #
    # Ключ РЕЕСТРА, а не политика: снимается отдельно (см. -Off и DEV-37).
    $auditKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Audit'
    if (-not (Test-Path $auditKey)) { New-Item -Path $auditKey -Force | Out-Null }
    New-ItemProperty -Path $auditKey -Name 'ProcessCreationIncludeCmdLine_Enabled' `
        -Value 1 -PropertyType DWord -Force | Out-Null
    Write-Host "командная строка в 4688 включена (ключ реестра — снимается в -Off)"

    Enable-SecurityPrivilege

    $bots = Get-BotProcesses
    if ($bots.Count -eq 0) { throw "бот не найден — вешать SACL не на что (гардиан поднимет его в течение минуты, повтори)" }

    $armed = @()
    foreach ($b in $bots) {
        Set-TerminateAudit -ProcId $b.ProcessId
        $inJob = Test-InJob -ProcId $b.ProcessId
        $armed += "PID $($b.ProcessId) (родитель $($b.ParentProcessId), inJob=$inJob)"
        Write-Host "SACL повешен: PID $($b.ProcessId) | родитель $($b.ParentProcessId) | inJob=$inJob"
    }

    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    @("armed $stamp") + $armed | Set-Content -Path $MarkFile -Encoding UTF8
    Write-Host ""
    Write-Host "ВООРУЖЕНО в $stamp. Ждём смерти бота, потом: -Report, потом ОБЯЗАТЕЛЬНО -Off."
    Write-Host "SACL живёт на ЭТИХ процессах. Бот перезапустится — вооружать заново."
}

function Invoke-Report {
    $since = (Get-Date).AddHours(-1 * $ReportHours)
    if (Test-Path $MarkFile) { Write-Host (Get-Content $MarkFile -Raw) }

    Write-Host "=== 4656: кто открывал хэндл процесса с правом «убить» (с $since) ==="
    $handles = @()
    try {
        $handles = @(Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4656; StartTime=$since} -ErrorAction Stop)
    } catch [Exception] {
        Write-Host "событий 4656 в окне нет"
    }
    $procHandles = @($handles | Where-Object { $_.Properties[4].Value -eq 'Process' -or $_.Message -match 'Process' })
    if ($procHandles.Count -eq 0) {
        Write-Host "ни одного открытия хэндла процесса — либо смерти не было, либо SACL не сработал"
    }
    foreach ($e in $procHandles) {
        Write-Host "--- $($e.TimeCreated) ---"
        Write-Host $e.Message
    }

    Write-Host ""
    Write-Host "=== 4689: завершения python.exe (с $since) ==="
    try {
        Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4689; StartTime=$since} -ErrorAction Stop |
            Where-Object { $_.Message -match 'python\.exe' } |
            ForEach-Object {
                $code = ($_.Message | Select-String -Pattern '0x[0-9a-fA-F]+' -AllMatches).Matches |
                        Select-Object -Last 1
                Write-Host "$($_.TimeCreated) | код $($code.Value)"
            }
    } catch [Exception] {
        Write-Host "событий 4689 в окне нет"
    }
}

function Invoke-Off {
    if (-not (Test-Path $BackupFile)) {
        throw "снимка политики нет ($BackupFile) — восстанавливать не из чего, выключай руками через auditpol"
    }
    & auditpol /restore /file:"$BackupFile" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "auditpol /restore вернул $LASTEXITCODE — политика НЕ вернулась, это надо чинить руками" }
    Write-Host "политика аудита восстановлена из снимка ДО: $BackupFile"

    # SACL снимаем с тех процессов, что ещё живы. Умершие уже унесли его с
    # собой: SACL живёт на объекте ядра, а не на образе.
    try {
        Enable-SecurityPrivilege
        foreach ($b in Get-BotProcesses) {
            Set-TerminateAudit -ProcId $b.ProcessId -Clear
            Write-Host "SACL снят: PID $($b.ProcessId)"
        }
    } catch [Exception] {
        Write-Host "SACL снять не удалось ($($_.Exception.Message)) — политика уже выключена, событий это не породит"
    }

    $auditKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Audit'
    if (Get-ItemProperty -Path $auditKey -Name 'ProcessCreationIncludeCmdLine_Enabled' -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path $auditKey -Name 'ProcessCreationIncludeCmdLine_Enabled' -Force
        Write-Host "ключ реестра ProcessCreationIncludeCmdLine_Enabled снят"
    }
    if (Test-Path $MarkFile) { Remove-Item $MarkFile -Force }
    Write-Host "ВЫКЛЮЧЕНО."
}

function Invoke-Watch {
    Invoke-Arm
    $known = @(Get-BotProcesses | ForEach-Object { $_.ProcessId }) -join ','
    while ($true) {
        Start-Sleep -Seconds $WatchSeconds
        $now = @(Get-BotProcesses | ForEach-Object { $_.ProcessId }) -join ','
        if ($now -eq $known) { continue }
        # Состав PID сменился — значит бот умер и поднялся заново. Вооружаем
        # нового. Сообщение обязано быть громким: тихое перевооружение
        # означало бы, что смерть прошла мимо журнала пробы.
        Write-Host "$(Get-Date -Format 'HH:mm:ss') | состав PID сменился: [$known] -> [$now] — перевооружаю"
        try { Invoke-Arm } catch [Exception] { Write-Host "перевооружить не вышло: $($_.Exception.Message)" }
        $known = @(Get-BotProcesses | ForEach-Object { $_.ProcessId }) -join ','
    }
}

if ($Watch)  { Invoke-Watch }
elseif ($Arm)    { Invoke-Arm }
if ($Report) { Invoke-Report }
if ($Off)    { Invoke-Off }
if (-not ($Arm -or $Report -or $Off -or $Watch)) {
    Write-Host "нужен один из ключей: -Arm | -Watch | -Report | -Off"
    exit 2
}
