"""Manage this user's MWT logon task using Windows' built-in Task Scheduler.

InteractiveToken keeps the GUI in the signed-in user's desktop. HighestAvailable
supports the executable's administrator manifest without storing credentials.
https://learn.microsoft.com/windows/win32/taskschd/security-contexts-for-running-tasks
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys


@dataclass(frozen=True)
class StartupState:
    enabled: bool
    matches_current: bool


@dataclass(frozen=True)
class StartupCommand:
    executable: str
    arguments: str
    working_directory: str


def startup_command(*, start_in_tray: bool = True) -> StartupCommand:
    executable = Path(sys.executable).resolve()
    arguments = ["--start-in-tray"] if start_in_tray else []
    if getattr(sys, "frozen", False):
        return StartupCommand(str(executable), subprocess.list2cmdline(arguments), str(executable.parent))
    # Source launches need a stable script path, not the invoking working directory.
    script = Path(__file__).resolve().with_name("mwt.py")
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.is_file():
        executable = pythonw
    return StartupCommand(str(executable), subprocess.list2cmdline([str(script), *arguments]), str(script.parent))


# All variable data comes from stdin JSON, never from interpolated PowerShell.
# Task names and ownership are fixed to this application and the current SID.
_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    function Test-CurrentUser([string]$identity) {
        if ($identity -eq $sid) { return $true }
        try {
            $account = [Security.Principal.NTAccount]::new($identity)
            return $account.Translate([Security.Principal.SecurityIdentifier]).Value -eq $sid
        } catch { return $false }
    }
    $name = 'MWT-WindowResizer-' + $sid
    $source = 'MWT.WindowResizer.Startup'
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $service.GetFolder('\')
    $task = $null
    try { $task = $folder.GetTask($name) }
    catch {
        if ($_.Exception.GetBaseException().HResult -notin @(-2147024894, -2147024893)) { throw }
    }
    if ($null -ne $task -and $task.Definition.RegistrationInfo.Source -ne $source) {
        throw 'A task with this name is not owned by MWT; no changes were made.'
    }
    # A disabled task records the user's opt-out across future launches.
    # Migrate only our legacy command at this location, preserving opt-out and
    # every other task setting. Never redirect a task for another installation.
    if ($request.operation -eq 'ensure' -and $null -ne $task) {
        $definition = $task.Definition
        if ($definition.Actions.Count -eq 1 -and
            (Test-CurrentUser $definition.Principal.UserId)) {
            $action = $definition.Actions.Item(1)
            if ($action.Type -eq 0 -and $action.Path -ieq $request.executable -and
                $action.WorkingDirectory -ieq $request.working_directory -and
                [string]$action.Arguments -ceq [string]$request.legacy_arguments -and
                [string]$action.Arguments -cne [string]$request.arguments) {
                $definition.Settings.Enabled = [bool]$task.Enabled
                $action.Arguments = $request.arguments
                $task = $folder.RegisterTaskDefinition($name, $definition, 4, $sid, $null, 3, $null)
            }
        }
    }
    $createDefault = ($null -eq $task -and $request.operation -in @('ensure', 'disable'))
    if ($request.operation -eq 'enable' -or $createDefault) {
        if (-not (Test-Path -LiteralPath $request.executable -PathType Leaf)) {
            throw 'The startup executable no longer exists.'
        }
        $definition = $service.NewTask(0)
        $definition.RegistrationInfo.Source = $source
        $definition.RegistrationInfo.Description = 'Start MWT window resizer when this user signs in.'
        $definition.Principal.UserId = $sid
        $definition.Principal.LogonType = 3
        $definition.Principal.RunLevel = 1
        $trigger = $definition.Triggers.Create(9)
        $trigger.UserId = $sid
        $trigger.Enabled = $true
        $action = $definition.Actions.Create(0)
        $action.Path = $request.executable
        $action.Arguments = $request.arguments
        $action.WorkingDirectory = $request.working_directory
        $definition.Settings.Enabled = ($request.operation -ne 'disable')
        $definition.Settings.DisallowStartIfOnBatteries = $false
        $definition.Settings.StopIfGoingOnBatteries = $false
        $definition.Settings.ExecutionTimeLimit = 'PT0S'
        $definition.Settings.MultipleInstances = 2
        $definition.Settings.AllowDemandStart = $true
        $definition.Settings.StartWhenAvailable = $true
        # Creation only prevents a concurrent first launch overwriting a choice.
        $registrationFlags = if ($createDefault) { 2 } else { 6 }
        $task = $folder.RegisterTaskDefinition($name, $definition, $registrationFlags, $sid, $null, 3, $null)
    } elseif ($request.operation -eq 'disable') {
        $task.Enabled = $false
    } elseif ($request.operation -eq 'remove') {
        # Explicit removal is for cleanup/uninstall; normal opt-out uses disable.
        if ($null -ne $task) { $folder.DeleteTask($name, 0) }
        $task = $null
    } elseif ($request.operation -notin @('query', 'ensure')) {
        throw 'Unsupported startup operation.'
    }
    $enabled = $false
    $matches = $false
    if ($null -ne $task) {
        $enabled = [bool]$task.Enabled
        $definition = $task.Definition
        if ($definition.Actions.Count -eq 1 -and $definition.Triggers.Count -eq 1) {
            $action = $definition.Actions.Item(1)
            $trigger = $definition.Triggers.Item(1)
            $matches = ($action.Type -eq 0 -and $trigger.Type -eq 9 -and $trigger.Enabled -and
                (Test-CurrentUser $trigger.UserId) -and (Test-CurrentUser $definition.Principal.UserId) -and
                $definition.Principal.LogonType -eq 3 -and $definition.Principal.RunLevel -eq 1 -and
                $action.Path -ieq $request.executable -and
                # Task Scheduler may return null for an omitted Arguments element.
                # Compare missing/empty arguments consistently for legacy tasks.
                [string]$action.Arguments -ceq [string]$request.arguments -and
                $action.WorkingDirectory -ieq $request.working_directory)
        }
    }
    [Console]::WriteLine((@{enabled=$enabled; matches_current=[bool]$matches} | ConvertTo-Json -Compress))
    exit 0
} catch {
    [Console]::WriteLine((@{error=$_.Exception.Message; hresult=$_.Exception.GetBaseException().HResult} | ConvertTo-Json -Compress))
    exit 1
}
'''


def configure_startup(operation: str = "query") -> StartupState:
    """Manage only MWT's current-user task; never run it now.

    ensure defaults to enabled only when no task exists, and migrates a legacy
    command at the same location to tray startup without enabling it. disable retains a
    disabled task so future launches respect the user's choice; remove forgets
    that choice and is reserved for explicit cleanup/uninstall.
    """
    if operation not in ("query", "ensure", "enable", "disable", "remove"):
        raise ValueError("不支援的自動啟動操作")
    command = startup_command()
    payload = {"operation": operation, **command.__dict__,
               "legacy_arguments": startup_command(start_in_tray=False).arguments}
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    encoded = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            input=json.dumps(payload), capture_output=True, encoding="utf-8", errors="replace",
            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError("工作排程器回應逾時，請重新讀取狀態後再試") from exc
    try:
        response = json.loads(result.stdout.lstrip("\ufeff"))
    except (ValueError, TypeError) as exc:
        raise OSError("無法讀取工作排程器回應：" + (result.stderr.strip() or "沒有有效資料")) from exc
    if not isinstance(response, dict):
        raise OSError("工作排程器回傳了無效資料")
    if result.returncode or response.get("error"):
        if response.get("hresult") in (-2147024891, 2147942405, 5):
            raise PermissionError("工作排程器拒絕存取，請以管理員身分執行 MWT 後重試")
        raise OSError(str(response.get("error") or result.stderr.strip() or "工作排程器操作失敗"))
    if any(type(response.get(field)) is not bool for field in ("enabled", "matches_current")):
        raise OSError("工作排程器狀態不完整")
    state = StartupState(response["enabled"], response["matches_current"])
    if operation == "enable" and not (state.enabled and state.matches_current):
        raise OSError("無法確認開機啟動設定，請重新讀取狀態")
    if operation in ("disable", "remove") and state.enabled:
        raise OSError("無法確認開機啟動已關閉，請重新讀取狀態")
    return state
