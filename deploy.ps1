# =============================================================================
# 涨停回调低吸战法 —— 一键部署脚本（Windows / PowerShell 5.1+ / PowerShell 7+）
#
# 编码要求：本文件必须保存为 UTF-8 with BOM。Windows PowerShell 5.1 读取无 BOM 的
#          UTF-8 文件时会按 ANSI 解码，导致中文乱码与语法解析错误。
#
# 用法（在项目根目录执行）：
#   pwsh -File .\deploy.ps1                     # 一键部署
#   powershell -ExecutionPolicy Bypass -File .\deploy.ps1      # 系统自带 PowerShell 5.1
#   pwsh -File .\deploy.ps1 -Port 8080          # 指定对外端口
#   pwsh -File .\deploy.ps1 -Mode synthetic     # 强制内置演示数据（完全离线）
#   pwsh -File .\deploy.ps1 -Https              # 启用 Caddy 自动 HTTPS
#   pwsh -File .\deploy.ps1 -Update             # 更新代码并重建
#   pwsh -File .\deploy.ps1 -Logs               # 查看实时日志
#   pwsh -File .\deploy.ps1 -Status             # 查看状态与健康检查
#   pwsh -File .\deploy.ps1 -Stop               # 停止服务
#   pwsh -File .\deploy.ps1 -Down               # 移除容器（保留数据卷）
#   pwsh -File .\deploy.ps1 -Destroy            # 彻底清理（含数据卷，危险）
#
# 全程使用相对路径，不含任何绝对路径。
# =============================================================================

[CmdletBinding()]
param(
    [int]$Port = 0,
    [ValidateSet('auto', 'real', 'eastmoney', 'tencent', 'ths', 'sina', 'synthetic')]
    [string]$Mode = '',
    [string]$Domain = '',
    [string]$Email = '',
    [switch]$Https,
    [switch]$Update,
    [switch]$Logs,
    [switch]$Status,
    [switch]$Stop,
    [switch]$Down,
    [switch]$Destroy
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# 基础定义
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ScriptDir

$EnvFile    = '.env'
$EnvSample  = '.env.example'
$ComposeFile = 'docker-compose.yml'

function Write-Info  { param([string]$m) Write-Host "[信息] $m" -ForegroundColor Cyan }
function Write-Ok    { param([string]$m) Write-Host "[成功] $m" -ForegroundColor Green }
function Write-Warn2 { param([string]$m) Write-Host "[警告] $m" -ForegroundColor Yellow }
function Write-Err   { param([string]$m) Write-Host "[错误] $m" -ForegroundColor Red }
function Write-Title { param([string]$m) Write-Host "`n$m" -ForegroundColor White -BackgroundColor DarkBlue }

# ---------------------------------------------------------------------------
# 依赖探测
# ---------------------------------------------------------------------------
$script:ComposeCmd = @()

function Test-Native {
    <#
      执行外部命令并返回是否成功（$LASTEXITCODE -eq 0）。
      兼容性要点：Windows PowerShell 5.1 下若把原生命令的 stderr 用 `*>` 重定向到 $null，
      会被包装成 NativeCommandError，并在 $ErrorActionPreference='Stop' 时抛出终止错误。
      因此这里统一用 2>&1 合并输出流并临时放宽 ErrorActionPreference，避免误判。
    #>
    param([string]$File, [string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $File @Arguments 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
    finally {
        $ErrorActionPreference = $prev
    }
}

function Get-ComposeCommand {
    if ($script:ComposeCmd.Count -gt 0) { return $script:ComposeCmd }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Err '未检测到 Docker。请安装 Docker Desktop：https://docs.docker.com/desktop/'
        exit 1
    }
    if (Test-Native -File 'docker' -Arguments @('compose', 'version')) {
        $script:ComposeCmd = @('docker', 'compose')
    }
    elseif (Get-Command docker-compose -ErrorAction SilentlyContinue) {
        $script:ComposeCmd = @('docker-compose')
    }
    else {
        Write-Err '未检测到 Docker Compose（docker compose 或 docker-compose）。'
        exit 1
    }
    return $script:ComposeCmd
}

function Assert-Docker {
    $null = Get-ComposeCommand
    if (-not (Test-Native -File 'docker' -Arguments @('info'))) {
        Write-Err 'Docker 守护进程未运行。请先启动 Docker Desktop 后重试。'
        exit 1
    }
    $v = (docker --version)
    Write-Ok "Docker 环境就绪：$v"
}

function Invoke-Compose {
    param([string[]]$Arguments)
    $cmd = Get-ComposeCommand
    $all = @($cmd[1..($cmd.Count - 1)]) + @('-f', $ComposeFile) + $Arguments
    & $cmd[0] @all
    if ($LASTEXITCODE -ne 0) { throw "compose 命令执行失败：$($all -join ' ')" }
}

# ---------------------------------------------------------------------------
# .env 读写
# ---------------------------------------------------------------------------
function Get-EnvValue {
    param([string]$Key)
    if (-not (Test-Path -LiteralPath $EnvFile)) { return '' }
    $line = Select-String -LiteralPath $EnvFile -Pattern "^$([regex]::Escape($Key))=" -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if (-not $line) { return '' }
    return ($line.Line -replace "^$([regex]::Escape($Key))=", '')
}

function Set-EnvValue {
    param([string]$Key, [string]$Value)
    if (-not (Test-Path -LiteralPath $EnvFile)) { New-Item -ItemType File -Path $EnvFile -Force | Out-Null }
    $content = Get-Content -LiteralPath $EnvFile -Raw -ErrorAction SilentlyContinue
    if ($null -eq $content) { $content = '' }
    if ($content -match "(?m)^$([regex]::Escape($Key))=") {
        $content = [regex]::Replace($content, "(?m)^$([regex]::Escape($Key))=.*$", "$Key=$Value")
    }
    else {
        if ($content.Length -gt 0 -and -not $content.EndsWith("`n")) { $content += "`n" }
        $content += "$Key=$Value`n"
    }
    Set-Content -LiteralPath $EnvFile -Value $content -NoNewline -Encoding UTF8
}

function Initialize-Env {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        if (Test-Path -LiteralPath $EnvSample) {
            Copy-Item -LiteralPath $EnvSample -Destination $EnvFile
            Write-Ok "已根据 $EnvSample 生成 $EnvFile"
        }
        else {
            Write-Warn2 "$EnvSample 不存在，生成最小化 .env"
            Set-Content -LiteralPath $EnvFile -Value "HOST_PORT=8000`nAPP_PORT=8000`nDATA_SOURCE_MODE=auto`nTZ=Asia/Shanghai`n" -Encoding UTF8
        }
    }
    else {
        Write-Info "复用已存在的 $EnvFile"
    }

    if ($Port -gt 0) { Set-EnvValue -Key 'HOST_PORT' -Value "$Port"; Write-Info "对外端口设为 $Port" }
    if ($Mode -ne '') { Set-EnvValue -Key 'DATA_SOURCE_MODE' -Value $Mode; Write-Info "数据源模式设为 $Mode" }
    if ($Domain -ne '') { Set-EnvValue -Key 'DOMAIN' -Value $Domain }
    if ($Email -ne '') { Set-EnvValue -Key 'ACME_EMAIL' -Value $Email }

    $hp = Get-EnvValue -Key 'HOST_PORT'
    if ([string]::IsNullOrWhiteSpace($hp)) { $hp = '8000'; Set-EnvValue -Key 'HOST_PORT' -Value $hp }
    $parsed = 0
    if (-not [int]::TryParse($hp, [ref]$parsed) -or $parsed -lt 1 -or $parsed -gt 65535) {
        Write-Err "HOST_PORT 非法：$hp（应为 1-65535 的整数）"
        exit 1
    }
    $dm = Get-EnvValue -Key 'DATA_SOURCE_MODE'
    if ([string]::IsNullOrWhiteSpace($dm)) { $dm = 'auto'; Set-EnvValue -Key 'DATA_SOURCE_MODE' -Value $dm }
    if (@('auto', 'real', 'eastmoney', 'tencent', 'ths', 'sina', 'synthetic') -notcontains $dm) {
        Write-Err "DATA_SOURCE_MODE 非法：$dm（应为 auto / real / eastmoney / tencent / ths / sina / synthetic）"
        exit 1
    }
    $script:HostPort = $parsed
    $script:ModeResolved = $dm
}

# ---------------------------------------------------------------------------
# 防火墙提示（Windows 主机）
# ---------------------------------------------------------------------------
function Open-Firewall {
    param([int]$P)
    $ruleName = "LimitUpPullback-$P"
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if ($existing) { Write-Info "防火墙规则已存在：$ruleName"; return }
    try {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $P -ErrorAction Stop | Out-Null
        Write-Ok "已创建防火墙入站规则放行 TCP $P"
    }
    catch {
        Write-Warn2 "自动放行防火墙端口失败（可能需要管理员权限）。请手动放行 TCP $P。"
    }
}

# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------
function Wait-Healthy {
    param([int]$P, [int]$Tries = 60)
    $url = "http://127.0.0.1:$P/api/v1/health"
    Write-Info "等待服务就绪：$url"
    for ($i = 1; $i -le $Tries; $i++) {
        try {
            $resp = Invoke-RestMethod -Uri $url -TimeoutSec 3 -ErrorAction Stop
            Write-Ok "服务健康检查通过（第 $i 次探测）"
            $resp | ConvertTo-Json -Depth 4
            return $true
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }
    Write-Warn2 '健康检查在预期时间内未通过，请执行：.\deploy.ps1 -Logs'
    return $false
}

function Show-Access {
    param([int]$P)
    Write-Title '=============================================='
    Write-Ok '部署完成！'
    Write-Host "  本机访问    : http://127.0.0.1:$P"
    try {
        $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
               Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
               Select-Object -First 1).IPAddress
        if ($ip) { Write-Host "  局域网访问  : http://${ip}:$P" }
    }
    catch { }
    $d = Get-EnvValue -Key 'DOMAIN'
    if (-not [string]::IsNullOrWhiteSpace($d)) { Write-Host "  域名访问    : https://$d" }
    Write-Host ''
    Write-Host "  数据源模式  : $script:ModeResolved"
    Write-Host '  常用命令    : .\deploy.ps1 -Logs | -Status | -Update | -Stop'
    Write-Host '  完整部署文档: docs/DEPLOYMENT.md'
    Write-Title '=============================================='
}

# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
function Start-Deployment {
    $profileArgs = @()
    $useHttps = $false

    if ($Https) {
        $d = Get-EnvValue -Key 'DOMAIN'
        if ([string]::IsNullOrWhiteSpace($d)) {
            Write-Warn2 '启用 HTTPS 需要域名，且该域名需已解析到本机公网 IP。'
            $input = Read-Host '请输入域名（直接回车跳过）'
            if (-not [string]::IsNullOrWhiteSpace($input)) { Set-EnvValue -Key 'DOMAIN' -Value $input; $d = $input }
        }
        $e = Get-EnvValue -Key 'ACME_EMAIL'
        if (-not [string]::IsNullOrWhiteSpace($d) -and [string]::IsNullOrWhiteSpace($e)) {
            $input2 = Read-Host "请输入 Let's Encrypt 通知邮箱"
            if (-not [string]::IsNullOrWhiteSpace($input2)) { Set-EnvValue -Key 'ACME_EMAIL' -Value $input2 }
        }
        if (-not [string]::IsNullOrWhiteSpace($d)) {
            $profileArgs = @('--profile', 'https')
            $useHttps = $true
            Write-Info '已启用 Caddy 反向代理 + 自动 HTTPS'
        }
        else {
            Write-Warn2 '跳过 HTTPS：未配置域名'
        }
    }

    if (-not $useHttps) { Open-Firewall -P $script:HostPort }

    Write-Title '>>> 构建镜像与启动容器'
    Invoke-Compose -Arguments (@($profileArgs) + @('up', '-d', '--build', '--remove-orphans'))

    Write-Title '>>> 等待服务启动'
    if (Wait-Healthy -P $script:HostPort) {
        Show-Access -P $script:HostPort
    }
    else {
        Write-Warn2 '服务可能仍在初始化。查看日志：.\deploy.ps1 -Logs'
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 动作分发
# ---------------------------------------------------------------------------
try {
    if ($Destroy) {
        Assert-Docker; Initialize-Env
        Write-Warn2 '该操作将删除容器、网络以及【全部数据卷】（行情缓存/策略参数/低吸池）！'
        $ans = Read-Host '请输入 YES 确认'
        if ($ans -ne 'YES') { Write-Warn2 '已取消'; exit 0 }
        Invoke-Compose -Arguments @('--profile', 'https', 'down', '-v', '--remove-orphans')
        Write-Ok '已彻底清理。'
    }
    elseif ($Down) {
        Assert-Docker; Initialize-Env
        Write-Info '移除容器与网络（数据卷保留）'
        Invoke-Compose -Arguments @('down', '--remove-orphans')
        Write-Ok '已移除。数据仍保存在 Docker 卷中。'
    }
    elseif ($Stop) {
        Assert-Docker; Initialize-Env
        Write-Info '停止服务（数据卷保留）'
        Invoke-Compose -Arguments @('stop')
        Write-Ok '已停止。重新启动：.\deploy.ps1'
    }
    elseif ($Logs) {
        Assert-Docker; Initialize-Env
        Invoke-Compose -Arguments @('logs', '-f', '--tail=200')
    }
    elseif ($Status) {
        Assert-Docker; Initialize-Env
        Write-Title '>>> 容器状态'
        Invoke-Compose -Arguments @('ps')
        Write-Title '>>> 健康检查'
        try {
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:$script:HostPort/api/v1/health" -TimeoutSec 5
            $h | ConvertTo-Json -Depth 4
            Write-Ok '应用健康'
        }
        catch { Write-Warn2 '应用未响应' }
    }
    elseif ($Update) {
        Write-Title '>>> 更新部署'
        Assert-Docker; Initialize-Env
        if (Test-Path -LiteralPath '.git') {
            Write-Info '拉取最新代码 ...'
            git pull --ff-only
            if ($LASTEXITCODE -ne 0) { Write-Warn2 'git pull 失败，跳过（继续使用当前代码）' }
        }
        else { Write-Info '非 Git 仓库，跳过代码拉取' }
        Start-Deployment
    }
    else {
        Write-Title '>>> 检查运行环境'
        Assert-Docker; Initialize-Env
        Start-Deployment
    }
}
catch {
    Write-Err $_.Exception.Message
    exit 1
}
